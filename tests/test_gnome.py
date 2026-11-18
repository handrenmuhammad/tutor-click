"""GNOME routing, screenshot coordinates and guarded input; real delivery is checked by doctor."""
import base64
from io import BytesIO

import pytest
from PIL import Image

from tutor_click import platform
from tutor_click.platform.base import Monitor, Refused, Window
from tutor_click.platform.gnome import hands, screen


@pytest.mark.parametrize("desktop", ["GNOME", "ubuntu:GNOME", "GNOME-Classic:GNOME"])
def test_gnome_session_selects_its_backend(desktop, monkeypatch):
    monkeypatch.setattr(platform.sys, "platform", "linux")
    monkeypatch.delenv("HYPRLAND_INSTANCE_SIGNATURE", raising=False)
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", desktop)
    assert platform.load().__name__ == "tutor_click.platform.gnome"


def test_capture_scales_image_and_preserves_logical_coordinate_factor(monkeypatch):
    output = BytesIO()
    Image.new("RGB", (2400, 1600), (30, 80, 100)).save(output, "PNG")
    calls = []
    monkeypatch.setattr(screen, "request", lambda op, **args: calls.append((op, args)) or base64.b64encode(output.getvalue()))
    mon = Monitor("1", 1200, 800, 2, -1200, 100)
    shot = screen.capture(mon, 1500)
    assert (shot.width, shot.height, shot.factor) == (1500, 1000, 1.25)
    assert Image.open(BytesIO(shot.jpeg)).size == (1500, 1000)
    assert calls == [("screenshot", {"monitor": 1})]


def test_window_origin_scale_and_focus_come_from_compositor(monkeypatch):
    monkeypatch.setattr(screen, "state", lambda: {"monitor": {"index": 1, "x": -1200, "y": 100, "width": 1200,
                                                              "height": 800, "scale": 2}})
    assert screen.focused_monitor() == Monitor("1", 1200, 800, 2, -1200, 100)


@pytest.fixture
def desktop(monkeypatch):
    win = {"id": "42", "cls": "pinta", "title": "test", "at": [100, 100], "size": [800, 600]}
    state = {"active": win, "windows": [win], "cursor": [0, 0]}
    calls = []
    def request(op, **args):
        calls.append((op, args))
        if op == "window-at":
            return win
        if op == "move":
            state["cursor"] = [args["x"], args["y"]]
        return True
    monkeypatch.setattr(hands, "request", request)
    monkeypatch.setattr(hands, "state", lambda: state)
    monkeypatch.setattr(hands.time, "sleep", lambda _: None)
    return hands.Hands(Monitor("0", 1200, 800, 1, 100, 50)), state, calls


def test_click_uses_global_coordinates_and_releases_modifiers(desktop):
    h, _, calls = desktop
    h.click(100, 100, mods="ctrl+shift")
    assert ("move", {"x": 200, "y": 150}) in calls
    buttons = [args for op, args in calls if op == "button"]
    assert [b["down"] for b in buttons] == [True, False]
    assert buttons[0]["mods"] == buttons[1]["mods"] == hands.parse_mods("ctrl+shift")


def test_gnome_terminals_are_never_acted_in(desktop):
    h, state, calls = desktop
    state["active"]["cls"] = "org.gnome.Ptyxis"
    with pytest.raises(Refused, match="never click"):
        h.click(100, 100)
    assert not any(op == "button" for op, _ in calls)


def test_focus_and_cursor_must_be_confirmed_before_click(desktop, monkeypatch):
    h, state, calls = desktop
    monkeypatch.setattr(h, "_focus", lambda _: None)
    state["active"] = None
    with pytest.raises(Refused) as exc:
        h.click(100, 100)
    assert exc.value.code == "WINDOW_NOT_FOCUSED"
    assert not any(op == "button" for op, _ in calls)


def test_lost_cursor_prevents_press(desktop, monkeypatch):
    h, _, calls = desktop
    monkeypatch.setattr(h, "_cursor", lambda: (0, 0))
    with pytest.raises(Refused) as exc:
        h.click(100, 100)
    assert exc.value.code == "CURSOR_NOT_AT_TARGET"
    assert not any(op == "button" for op, _ in calls)


def test_keys_target_the_checked_window_and_unknown_text_is_rejected_before_typing(desktop):
    h, _, calls = desktop
    h.keys("ctrl+a", hover=(100, 100))
    keys = [args for op, args in calls if op == "key"]
    assert len(keys) == 1 and keys[0]["id"] == "42"
    calls.clear()
    with pytest.raises(Refused):
        h.type_text("abc\n")
    assert not any(op == "key" for op, _ in calls)


def test_a_backend_error_is_a_refusal_not_an_unchecked_action(desktop, monkeypatch):
    h, _, _ = desktop
    def fail(*args, **kwargs):
        raise RuntimeError("Close the GNOME overview")
    monkeypatch.setattr(hands, "request", fail)
    with pytest.raises(Refused, match="overview") as exc:
        h.click(100, 100)
    assert exc.value.code == "INPUT_REJECTED"


def test_scroll_names_the_guarded_window_and_enforces_the_step_limit(desktop, monkeypatch):
    h, _, _ = desktop
    h.last = Window("42", "pinta", "test")
    calls = []
    def send(op, **args):
        calls.append((op, args))
    monkeypatch.setattr(h, "_send", send)
    h._wheel(2)
    assert calls == [("scroll", {"notches": 2, "id": "42"})]
    calls.clear()
    with pytest.raises(Refused):
        h._wheel(11)
    assert calls == []


@pytest.mark.parametrize("key", ["ctrl++", "shift+a", "numpad1", "ctrl+shift+tab", "F12"])
def test_supported_keys_parse(key):
    mods, val = hands.parse_combo(key)
    assert val > 0 and isinstance(mods, list)
