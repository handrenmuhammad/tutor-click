"""Overlay, panel and main loop in Qt, for the desktops that have no layer shell (macOS first).

A backend for such a desktop takes App, make_ui, focused_monitor and MOVE_SECS from here and adds
its own screen capture, windows, input, hotkeys and sound.
"""
from .app import App, focused_monitor, make_ui
from .overlay import MOVE_SECS

__all__ = ["MOVE_SECS", "App", "focused_monitor", "make_ui"]
