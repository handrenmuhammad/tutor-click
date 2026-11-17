"""Hyprland on Wayland: grim screenshots, hyprctl windows and input, a GTK4 layer-shell overlay and panel, PipeWire sound."""
from . import sound
from .app import App, binds, install, make_ui, permissions, preflight, prompt
from .hands import Hands
from .overlay import MOVE_SECS
from .screen import active_window, capture, focused_monitor

__all__ = ["MOVE_SECS", "NAME", "App", "Hands", "active_window", "binds", "capture", "focused_monitor", "install", "make_ui",
           "permissions", "preflight", "prompt", "run_bench", "sound"]

NAME = "Linux (Hyprland)"


def run_bench(args: list[str]):
    from .bench import main

    main(args)
