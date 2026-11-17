"""The GTK main loop, the global hotkeys and the start-up check for Hyprland on Wayland."""
import json
import os
import signal
import subprocess
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk

from ... import config
from .overlay import Overlay
from .panel import Panel

_MODS = ("super", "alt", "ctrl", "shift")
_KEYS = {"space": "space", "escape": "Escape"}

DESKTOP = """[Desktop Entry]
Type=Application
Name=Tutor Click
GenericName=Screen Tutor
Comment=A tutor that watches your screen and teaches you any app
Exec={exe} app
Icon=tutor-click
Terminal=false
Categories=Education;
"""


def preflight(argv: list[str]):
    """gtk4-layer-shell must be loaded before libwayland-client, so the process re-execs itself with it preloaded.

    argv: what to run again, e.g. ["app"].
    """
    if not os.path.exists(config.LAYER_SHELL_LIB):
        sys.exit(f"{config.LAYER_SHELL_LIB} not found - install it: sudo pacman -S gtk4-layer-shell")
    if config.LAYER_SHELL_LIB not in os.environ.get("LD_PRELOAD", ""):
        env = dict(os.environ, LD_PRELOAD=config.LAYER_SHELL_LIB)
        os.execve(sys.executable, [sys.executable, "-m", "tutor_click", *argv], env)


def permissions() -> list[tuple[str, bool, str]]:
    """Hyprland asks the user for nothing."""
    return []


def prompt() -> str:
    """A small text box on screen, for the type-a-question hotkey. Empty if it was dismissed."""
    r = subprocess.run(["fuzzel", "--dmenu", "--prompt", "Ask the tutor: ", "--lines", "0"], input="", capture_output=True, text=True, check=False)
    return r.stdout.strip()


def install():
    """Launcher entry, icon, and a `tutor-click` command in ~/.local/bin. No root needed."""
    exe = Path(sys.executable).with_name("tutor-click")
    share = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    desktop = share / "applications/tutor-click.desktop"
    icon = share / "icons/hicolor/scalable/apps/tutor-click.svg"
    link = Path.home() / ".local/bin/tutor-click"
    for path in (desktop, icon, link):
        path.parent.mkdir(parents=True, exist_ok=True)
    desktop.write_text(DESKTOP.format(exe=exe))
    icon.write_bytes((Path(__file__).parents[3] / "assets/tutor-click.svg").read_bytes())
    if link.is_symlink() or not link.exists():
        link.unlink(missing_ok=True)
        link.symlink_to(exe)
    else:
        print(f"left {link} alone: it is not a symlink")
    print(f"installed:\n  {desktop}\n  {icon}\n  {link} -> {exe}")


class App:
    def __init__(self, on_start):
        self.gtk = Gtk.Application(application_id="dev.handren.TutorClick", flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.gtk.connect("activate", lambda _app: (on_start(self), self.gtk.hold()))

    def run(self) -> int:
        for sig in (signal.SIGINT, signal.SIGTERM):  # Ctrl-C / kill end the GTK loop cleanly
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, lambda: (self.gtk.quit(), False)[1])
        return self.gtk.run([])

    def quit(self):
        GLib.idle_add(self.gtk.quit)  # GTK calls must run on the main loop, whatever thread asks


def make_ui(app: App, monitor, on_command, backends, backend):
    overlay = Overlay(app.gtk, monitor.name)
    panel = Panel(app.gtk, Overlay._gdk_monitor(monitor.name), monitor, on_command, backends, backend)
    return overlay, panel


def _combo(keys: str) -> str:
    """'super+alt+n' -> 'SUPER + ALT + N', the way Hyprland writes a bind."""
    return " + ".join(k.upper() if k in _MODS or len(k) == 1 else _KEYS[k] for k in keys.split("+"))


def bind_code(on: bool, keys: list[tuple[str, str, bool]], cli: str) -> str:
    if on:
        return " ".join(
            f"hl.bind({json.dumps(_combo(k))}, hl.dsp.exec_cmd({json.dumps(f'{cli} {cmd}')}){', { release = true }' if release else ''})"
            for k, cmd, release in keys
        )
    return " ".join(f"hl.unbind({json.dumps(_combo(k))})" for k in dict.fromkeys(k for k, _, _ in keys))


def binds(on: bool, keys: list[tuple[str, str, bool]], cli: str):
    """Register or remove the global hotkeys in the running Hyprland (not in its config file)."""
    r = subprocess.run(["hyprctl", "eval", bind_code(on, keys, cli)], capture_output=True, text=True, check=False)
    if r.stdout.strip() != "ok":
        print("hotkeys:", r.stdout.strip()[:300], file=sys.stderr)
