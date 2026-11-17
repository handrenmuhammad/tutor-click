"""macOS: Quartz for windows and input, screencapture for the screen, Qt for the overlay and panel.

Written on Linux against Apple's documented calls and never run by its author. The logic above
the system calls is tested with a stand-in for native.py; native.py itself is only exercised on a
real Mac, which is what `tutor-click doctor --input` is for.
"""
import sys

from ..qt import MOVE_SECS, App, focused_monitor, make_ui
from ..qt.probe import make_probe
from . import native, sound
from .hands import Hands
from .screen import active_window, capture

__all__ = ["MOVE_SECS", "NAME", "App", "Hands", "active_window", "binds", "capture", "focused_monitor", "install", "make_probe", "make_ui",
           "permissions", "preflight", "prompt", "run_bench", "sound"]

NAME = "macOS"
_SETTINGS = "System Settings > Privacy & Security > {}: switch on the app you start Tutor Click from (Terminal, iTerm...), then start it again"


def preflight(argv: list[str]):
    """Nothing to arrange before the app starts."""


def permissions() -> list[tuple[str, bool, str]]:
    """(what, granted, how to grant it) for each permission macOS makes the user give."""
    seeing = native.can_see_screen()
    if not seeing:
        native.ask_to_see_screen()  # puts the app in the list in System Settings, so it can be switched on
    return [
        ("Screen Recording (screenshots and window titles)", seeing, _SETTINGS.format("Screen & System Audio Recording")),
        ("Accessibility (moving the mouse, clicking, typing)", native.can_control(), _SETTINGS.format("Accessibility")),
    ]


def binds(on: bool, keys, cli: str):
    """Global hotkeys are not built for macOS yet. Every command is on the panel."""


def prompt() -> str:
    sys.exit("`tutor-click type` is not built for macOS. Type in the panel instead.")


def install():
    sys.exit("Nothing to install on macOS yet: start the app with `tutor-click`.")


def run_bench(args: list[str]):
    sys.exit("The input benchmark only exists for Hyprland. On macOS run: tutor-click doctor --input")
