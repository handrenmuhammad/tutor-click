"""The seam between the lesson loop and the desktop."""
import re
from pathlib import Path

import pytest

import tutor_click
from tutor_click import platform
from tutor_click.platform import hands as base_hands
from tutor_click.platform.base import Monitor, Refused
from tutor_click.platform.hyprland import app, hands

PACKAGE = Path(tutor_click.__file__).parent
SURFACE = ("NAME", "preflight", "permissions", "App", "make_ui", "focused_monitor", "active_window", "capture", "Hands", "binds", "sound",
           "prompt", "install", "run_bench", "MOVE_SECS")


def test_a_hyprland_session_gets_the_hyprland_backend(monkeypatch):
    monkeypatch.setattr(platform.sys, "platform", "linux")
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "abc")
    desk = platform.load()
    assert desk.__name__ == "tutor_click.platform.hyprland"
    assert [name for name in SURFACE if not hasattr(desk, name)] == []
    assert [name for name in ("record", "stop_recording", "play_pcm", "say") if not hasattr(desk.sound, name)] == []


@pytest.mark.parametrize(("system", "signature"), [("win32", "abc"), ("linux", None)])
def test_any_other_desktop_is_refused_in_words_not_with_a_traceback(system, signature, monkeypatch):
    monkeypatch.setattr(platform.sys, "platform", system)
    monkeypatch.delenv("XDG_CURRENT_DESKTOP", raising=False)
    monkeypatch.delenv("DESKTOP_SESSION", raising=False)
    if signature:
        monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", signature)
    else:
        monkeypatch.delenv("HYPRLAND_INSTANCE_SIGNATURE", raising=False)
    with pytest.raises(platform.Unsupported, match="runs on Hyprland .*GNOME and macOS"):
        platform.load()


def test_nothing_outside_the_platform_folder_names_a_desktop_tool_or_toolkit():
    desktop = re.compile(r"hyprctl|\bgrim\b|pw-record|paplay|fuzzel|LayerShell|platform\.(hyprland|macos|qt)|\bGtk\b|\bGLib\b|GdkPixbuf"
                         r"|PySide6|\bQuartz\b|screencapture|sounddevice|Desktop Entry")
    found = {
        f"{path.relative_to(PACKAGE)}:{n}": line.strip()
        for path in PACKAGE.rglob("*.py") if "platform" not in path.relative_to(PACKAGE).parts
        for n, line in enumerate(path.read_text().splitlines(), 1) if desktop.search(line)
    }
    assert found == {}


def test_hotkeys_are_written_the_way_hyprland_binds_them():
    keys = [("super+alt+space", "ptt-down", False), ("super+alt+space", "ptt-up", True), ("super+alt+n", "next", False), ("super+alt+escape", "stop", False)]
    assert app.bind_code(True, keys, "/venv/python -m tutor_click") == (
        'hl.bind("SUPER + ALT + space", hl.dsp.exec_cmd("/venv/python -m tutor_click ptt-down")) '
        'hl.bind("SUPER + ALT + space", hl.dsp.exec_cmd("/venv/python -m tutor_click ptt-up"), { release = true }) '
        'hl.bind("SUPER + ALT + N", hl.dsp.exec_cmd("/venv/python -m tutor_click next")) '
        'hl.bind("SUPER + ALT + Escape", hl.dsp.exec_cmd("/venv/python -m tutor_click stop"))'
    )
    assert app.bind_code(False, keys, "x") == 'hl.unbind("SUPER + ALT + space") hl.unbind("SUPER + ALT + N") hl.unbind("SUPER + ALT + Escape")'


def test_the_hands_take_modifiers_as_the_model_writes_them(monkeypatch):
    sent = []
    h = hands.Hands(Monitor("HDMI-A-1", 3440, 1440, 1.0))
    win = hands.Window("0xaa", "pinta", "x", (0, 0), (1000, 800))
    monkeypatch.setattr(h, "window_at", lambda x, y: win)
    monkeypatch.setattr(h, "_allow", lambda w: w)
    monkeypatch.setattr(h, "_aim", lambda *a, **k: None)
    monkeypatch.setattr(h, "_released", lambda *a: None)
    monkeypatch.setattr(h, "_move", lambda *a, **k: None)
    monkeypatch.setattr(h, "_press", lambda button, down, held, *a: sent.append(("down" if down else "up", held)))
    monkeypatch.setattr(base_hands, "time", type("T", (), {"sleep": staticmethod(lambda _s: None)}))
    h.click(10, 10, mods="ctrl+shift")
    h.drag(10, 10, 50, 50, mods="Shift")
    assert sent == [("down", "CTRL SHIFT"), ("up", "CTRL SHIFT"), ("down", "SHIFT"), ("up", "SHIFT")]
    with pytest.raises(Refused, match="modifier hyper"):
        h.click(10, 10, mods="hyper")
