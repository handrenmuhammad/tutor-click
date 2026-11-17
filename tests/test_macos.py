"""The macOS backend above its system calls. native.py is replaced by a stand-in: nothing here has run on a Mac."""
import os
import subprocess
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_NO_GLIB", "1")
pytest.importorskip("PySide6")

FLAGS = {"cmd": 1 << 20, "ctrl": 1 << 18, "alt": 1 << 19, "shift": 1 << 17}
native = types.ModuleType("tutor_click.platform.macos.native")
native.FLAGS = FLAGS
sys.modules["tutor_click.platform.macos.native"] = native

from tutor_click import platform
from tutor_click.platform import hands as base_hands
from tutor_click.platform import macos
from tutor_click.platform.base import Monitor, Refused
from tutor_click.platform.macos import hands as mac_hands
from tutor_click.platform.macos import screen as mac_screen
from tutor_click.platform.macos.hands import parse_combo, parse_mods

CMD, CTRL, ALT, SHIFT = FLAGS["cmd"], FLAGS["ctrl"], FLAGS["alt"], FLAGS["shift"]


def win(pid, number, owner, x, y, w, h, layer=0, title="") -> dict:
    return {"pid": pid, "number": number, "owner": owner, "title": title or owner, "layer": layer, "x": x, "y": y, "w": w, "h": h}


class FakeMac:
    """A Mac, as far as native.py shows one: a stack of windows, a front app, a cursor, and a list of what was sent."""

    def __init__(self):
        self.wins = [
            win(900, 1, "Tutor Click", 0, 0, 1440, 900, layer=3),  # our overlay floats above everything
            win(77, 2, "Dock", 0, 850, 1440, 50, layer=20),
            win(101, 3, "Pinta", 0, 25, 1000, 800, title="Unsaved Image 1"),
            win(102, 4, "Terminal", 1000, 25, 440, 800),
            win(103, 5, "Finder", 200, 100, 600, 400),  # behind Pinta
        ]
        self.front = (103, "Finder")
        self.cursor_at = (0.0, 0.0)
        self.sent = []
        self.activation_works = True
        self.nudge = (0, 0)  # the user's hand on the mouse
        self.fail_on_drag = None  # which drag move macOS refuses
        self.trusted, self.sees_screen, self.asked = True, True, 0

    def windows(self):
        return list(self.wins)

    def frontmost(self):
        return self.front

    def activate(self, pid):
        self.sent.append(("activate", pid))
        if self.activation_works:
            self.front = (pid, next(w["owner"] for w in self.wins if w["pid"] == pid))
        return self.activation_works

    def cursor(self):
        return self.cursor_at

    def mouse(self, kind, x, y, button="left", flags=0, clicks=1):
        if kind == "drag" and self.fail_on_drag is not None:
            self.fail_on_drag -= 1
            if self.fail_on_drag < 0:
                raise OSError("the event was not posted")
        self.sent.append((kind, x, y, button, flags, clicks))
        if kind in ("move", "drag"):
            self.cursor_at = (x + self.nudge[0], y + self.nudge[1])

    def wheel(self, lines):
        self.sent.append(("wheel", lines))

    def key(self, code, flags=0):
        self.sent.append(("key", code, flags))

    def text(self, ch):
        self.sent.append(("text", ch))

    def can_control(self):
        return self.trusted

    def can_see_screen(self):
        return self.sees_screen

    def ask_to_see_screen(self):
        self.asked += 1


@pytest.fixture
def mac(monkeypatch):
    fake = FakeMac()
    for name in ("windows", "frontmost", "activate", "cursor", "mouse", "wheel", "key", "text", "can_control", "can_see_screen", "ask_to_see_screen"):
        monkeypatch.setattr(native, name, getattr(fake, name), raising=False)
    for module in (base_hands, mac_hands):
        monkeypatch.setattr(module, "time", SimpleNamespace(sleep=lambda _s: None))
    return fake


def make() -> mac_hands.Hands:
    return mac_hands.Hands(Monitor("Built-in", 1440, 900, 2.0))


def test_macos_is_the_backend_on_a_mac_and_has_the_whole_surface(monkeypatch):
    monkeypatch.setattr(platform.sys, "platform", "darwin")
    desk = platform.load()
    assert desk is macos and desk.NAME == "macOS"
    surface = ("NAME", "preflight", "permissions", "App", "make_ui", "make_probe", "focused_monitor", "active_window", "capture", "Hands", "binds",
               "sound", "prompt", "install", "run_bench", "MOVE_SECS")
    assert [name for name in surface if not hasattr(desk, name)] == []
    assert [name for name in ("record", "stop_recording", "play_pcm", "say") if not hasattr(desk.sound, name)] == []


# ---- windows -------------------------------------------------------------------
def test_the_window_under_a_point_is_the_frontmost_app_window_not_our_overlay_or_the_dock(mac):
    h = make()
    assert h.window_at(300, 300).cls == "Pinta"  # Finder is behind it, the overlay above it on another layer
    assert h.window_at(1200, 300).cls == "Terminal"
    assert h.window_at(700, 870) is None  # only the Dock is there
    assert h.window_at(300, 300).address == "101:3"


def test_the_focused_window_is_named_by_app_and_title(mac):
    assert mac_screen.active_window() == 'Finder - "Finder"'
    mac.front = (101, "Pinta")
    assert mac_screen.active_window() == 'Pinta - "Unsaved Image 1"'
    mac.front = None
    assert mac_screen.active_window() == "unknown"


# ---- clicks ------------------------------------------------------------------
def test_click_brings_the_app_forward_moves_then_presses_and_releases_there(mac):
    win_ = make().click(300, 300)
    assert win_.cls == "Pinta"
    assert mac.sent == [("activate", 101), ("move", 300, 300, "left", 0, 1), ("down", 300, 300, "left", 0, 1), ("up", 300, 300, "left", 0, 1)]


def test_double_click_counts_its_presses_and_modifiers_are_held(mac):
    make().click(300, 300, button="right", count=2, mods="cmd+shift")
    presses = [e for e in mac.sent if e[0] in ("down", "up")]
    assert presses == [("down", 300, 300, "right", CMD | SHIFT, 1), ("up", 300, 300, "right", CMD | SHIFT, 1),
                       ("down", 300, 300, "right", CMD | SHIFT, 2), ("up", 300, 300, "right", CMD | SHIFT, 2)]


def test_a_second_screen_is_reached_through_its_place_in_the_layout(mac):
    mac.wins.append(win(104, 6, "Preview", 1440, 0, 1920, 1080))
    h = mac_hands.Hands(Monitor("Second", 1920, 1080, 1.0, x=1440, y=0))
    assert h.click(100, 50).cls == "Preview"
    assert ("down", 1540, 50, "left", 0, 1) in mac.sent


def test_nothing_is_pressed_if_macos_does_not_bring_the_app_forward(mac):
    mac.activation_works = False
    with pytest.raises(Refused) as e:
        make().click(300, 300)
    assert e.value.code == "WINDOW_NOT_FOCUSED"
    assert [s[0] for s in mac.sent] == ["activate"]


def test_nothing_is_pressed_if_the_mouse_is_moved_at_the_last_moment(mac):
    mac.nudge = (40, 0)
    with pytest.raises(Refused) as e:
        make().click(300, 300)
    assert e.value.code == "CURSOR_NOT_AT_TARGET"
    assert not any(s[0] in ("down", "up") for s in mac.sent)


def test_terminals_are_never_touched_and_a_lesson_stays_in_its_app(mac):
    h = make()
    with pytest.raises(Refused) as e:
        h.click(1200, 300)
    assert e.value.code == "NEVER_ACT"
    assert mac.sent == []
    h.click(300, 300)  # the lesson is now Pinta's
    mac.wins.insert(2, win(105, 7, "Preview", 0, 25, 500, 400))
    with pytest.raises(Refused) as e:
        h.click(100, 100)
    assert e.value.code == "OUTSIDE_APP"


# ---- drag and scroll -----------------------------------------------------------
def test_drag_holds_the_button_through_every_move_and_lets_go_at_the_end(mac):
    make().drag(100, 100, 340, 220, mods="shift")
    kinds = [s[0] for s in mac.sent]
    assert kinds == ["activate", "move", "down", *["drag"] * 12, "up"]
    assert all(s[4] == SHIFT for s in mac.sent if s[0] == "drag")  # the modifier stays held while dragging
    assert mac.sent[2][:4] == ("down", 100, 100, "left") and mac.sent[-1][:4] == ("up", 340, 220, "left")


def test_a_drag_that_fails_halfway_lets_go_where_it_got_to_not_at_the_far_end(mac):
    mac.fail_on_drag = 4
    with pytest.raises(OSError, match="not posted"):
        make().drag(100, 100, 340, 220)
    assert mac.sent[-1][:4] == ("up", 180, 140, "left")  # the fourth of twelve steps
    assert sum(s[0] == "drag" for s in mac.sent) == 4


def test_a_drag_may_not_end_outside_the_app(mac):
    with pytest.raises(Refused) as e:
        make().drag(900, 300, 1200, 300)
    assert e.value.code == "OUTSIDE_APP"
    assert not any(s[0] in ("down", "drag") for s in mac.sent)


def test_scroll_turns_the_wheel_under_the_cursor_without_changing_the_front_app(mac):
    make().scroll(300, 300, 2)
    make().scroll(300, 300, -1)
    assert [s for s in mac.sent if s[0] != "move"] == [("wheel", -2 * mac_hands.LINES_PER_NOTCH), ("wheel", mac_hands.LINES_PER_NOTCH)]
    assert mac.front == (103, "Finder")


# ---- keys and typing -------------------------------------------------------------
def test_keys_go_to_the_lesson_app_as_key_codes_with_their_modifiers(mac):
    h = make()
    h.click(300, 300)
    mac.sent.clear()
    h.keys("cmd+z g X 2 enter cmd+shift+s")
    assert mac.sent == [("key", 6, CMD), ("key", 5, 0), ("key", 7, SHIFT), ("key", 19, 0), ("key", 36, 0), ("key", 1, CMD | SHIFT)]


def test_keys_with_a_hover_point_move_there_first(mac):
    make().keys("tab", hover=(300, 300))
    assert mac.sent == [("activate", 101), ("move", 300, 300, "left", 0, 1), ("key", 48, 0)]


def test_keys_without_a_point_and_without_a_lesson_app_go_nowhere_but_the_front_app(mac):
    make().keys("escape")  # Finder is in front
    assert mac.sent == [("key", 53, 0)]
    mac.front = (102, "Terminal")
    with pytest.raises(Refused) as e:
        make().keys("enter")
    assert e.value.code == "NEVER_ACT"


def test_key_names_the_mac_way_and_the_linux_way_both_work():
    assert parse_combo("cmd+c") == parse_combo("super+c") == parse_combo("command+C".lower()) == (CMD, 8)
    assert parse_combo("option+left") == parse_combo("alt+left") == (ALT, 123)
    assert parse_combo("ctrl+shift+a") == (CTRL | SHIFT, 0)
    assert parse_combo("?") == (SHIFT, 44)
    assert parse_combo("cmd++") == (CMD | SHIFT, 24)
    assert parse_combo("f5") == (0, 96)
    assert parse_combo("numpad1") == (0, 83)
    assert parse_mods("") == 0
    for bad in ("hyper+a", "cmd+nosuchkey", "é"):
        with pytest.raises(Refused) as e:
            parse_combo(bad)
        assert e.value.code == "UNKNOWN_KEY"


def test_typing_sends_each_character_as_itself(mac):
    h = make()
    h.click(300, 300)
    mac.sent.clear()
    h.type_text("Héllo 1!")
    assert mac.sent == [("text", ch) for ch in "Héllo 1!"]


def test_text_with_a_line_break_or_too_long_is_refused_before_anything_is_typed(mac):
    h = make()
    for text, code in (("one\ntwo", "UNKNOWN_KEY"), ("x" * (base_hands.MAX_TYPE_CHARS + 1), "TOO_LONG"), ("", "TOO_LONG")):
        with pytest.raises(Refused) as e:
            h.type_text(text)
        assert e.value.code == code
    with pytest.raises(Refused):
        h.keys(" ".join("a" * (base_hands.MAX_KEYS + 1)))
    assert mac.sent == []


# ---- screen and permissions ----------------------------------------------------
def test_capture_takes_the_screens_rectangle_and_scales_it_down(mac, monkeypatch):
    from images import jpeg

    ran = []

    def run(cmd, **kwargs):
        ran.append(cmd)
        assert kwargs == {"check": True, "capture_output": True}
        size = (2880, 1800) if cmd[0] == "screencapture" else (1568, 980)  # a Retina screen comes out at twice its size
        Path(cmd[-1]).write_bytes(jpeg(*size, ()))
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(mac_screen.subprocess, "run", run)
    shot = mac_screen.capture(Monitor("Second", 1440, 900, 2.0, x=1440, y=-200), 1568)
    assert ran[0][:5] == ["screencapture", "-x", "-t", "jpg", "-R"] and ran[0][5] == "1440,-200,1440,900"
    assert ran[1][:5] == ["sips", "-Z", "1568", "-s", "formatOptions"] and ran[1][-1] == ran[0][-1]
    assert (shot.width, shot.height) == (1568, 980)
    assert shot.factor == pytest.approx(1568 / 1440)
    assert not os.path.exists(ran[0][-1])  # the file does not outlive the call


def test_permissions_name_what_is_off_and_ask_macos_to_list_the_app(mac):
    assert [granted for _, granted, _ in macos.permissions()] == [True, True]
    assert mac.asked == 0
    mac.trusted = mac.sees_screen = False
    listed = macos.permissions()
    assert [(what.split(" (")[0], granted) for what, granted, _ in listed] == [("Screen Recording", False), ("Accessibility", False)]
    assert all("System Settings > Privacy & Security" in how for _, _, how in listed)
    assert mac.asked == 1


# ---- the lesson's app (found on a real Mac, 2026-11-18) -----------------------------
def test_opening_an_app_through_the_launcher_does_not_tie_the_lesson_to_what_was_in_front(mac):
    h = make()  # Finder is in front; the lesson is about Pinta, which the tutor opens itself
    h.keys("cmd+space")
    h.type_text("Pinta")
    h.keys("enter")
    assert h.target is None  # keys with no point say nothing about where the lesson is
    assert [s[0] for s in mac.sent] == ["key", *["text"] * 5, "key"]
    h.click(300, 300)  # the first action aimed at a window is what ties it
    assert h.target == "Pinta"
    with pytest.raises(Refused) as e:
        h.click(400, 300 + 0) if mac.wins.insert(2, win(105, 7, "Preview", 0, 25, 900, 700)) else h.click(400, 300)
    assert e.value.code == "OUTSIDE_APP"


def test_keys_before_any_lesson_app_still_never_go_to_a_terminal(mac):
    mac.front = (102, "Terminal")
    with pytest.raises(Refused) as e:
        make().keys("cmd+space")
    assert e.value.code == "NEVER_ACT"
    assert mac.sent == []


def test_keys_bring_the_lesson_app_forward_when_another_app_has_the_keyboard(mac):
    h = make()
    h.click(300, 300)  # the lesson is Pinta's, and Pinta is in front
    mac.front = (103, "Finder")  # the user clicked elsewhere
    mac.sent.clear()
    h.keys("cmd+z")
    assert mac.sent == [("activate", 101), ("key", 6, CMD)]
    mac.front, mac.activation_works = (103, "Finder"), False
    mac.sent.clear()
    with pytest.raises(Refused) as e:
        h.keys("cmd+z")
    assert e.value.code == "WINDOW_NOT_FOCUSED"
    assert ("key", 6, CMD) not in mac.sent  # nothing was typed into Finder
