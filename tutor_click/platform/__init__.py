"""Everything that depends on the desktop: screen, windows, input, overlay, panel, hotkeys, sound.

The lesson loop, trace, verification, voice models and providers reach the desktop only through
the backend that load() returns: hyprland/, gnome/ or macos/. hands.py holds the
input guards every backend shares, qt/ the overlay and panel for desktops without a layer shell,
pcm.py the microphone and speaker for those without command-line tools for them.

A backend is a module with:
    NAME                            the desktop in words, for the model ("macOS")
    preflight(argv)                 runs first in the process; may re-exec it with those arguments
    permissions()                   (what, granted, how to grant it) for what the system makes the user allow
    App(on_start)                   the main loop. on_start(app) runs on it once windows can be made.
                                    .run() -> exit code, .quit() from any thread
    make_ui(app, monitor, on_command, backends, backend) -> (overlay, panel)
    focused_monitor() -> Monitor    active_window() -> "class - title"    capture(monitor, max_edge) -> Shot
    Hands(monitor)                  click, drag, scroll, keys, type_text, window_at, reset, .target.
                                    Keys and modifiers arrive as the model wrote them ("ctrl+shift")
    binds(on, keys, cli)            register or remove global hotkeys; keys: ("super+alt+n", command, on release)
    sound                           record(path), stop_recording(proc), play_pcm(rate), say(text): processes
    prompt() -> str                 a text box on screen for the type-a-question hotkey
    install()                       put the app in the launcher
    run_bench(args)                 the input delivery benchmark
    make_probe(app, monitor)        optional: a window that records the input reaching it, for the doctor
    MOVE_SECS                       how long the overlay's pointer takes to arrive
"""
import os
import sys


class Unsupported(Exception):
    """There is no backend for this desktop."""


def load():
    """The backend for the desktop this process is running on."""
    if sys.platform.startswith("linux") and os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        from . import hyprland

        return hyprland
    desktops = os.environ.get("XDG_CURRENT_DESKTOP", "").lower().split(":")
    if sys.platform.startswith("linux") and ("gnome" in desktops or os.environ.get("DESKTOP_SESSION", "").lower() == "gnome"):
        from . import gnome

        return gnome
    if sys.platform == "darwin":
        from . import macos

        return macos
    raise Unsupported(f"Tutor Click runs on Hyprland (Wayland), GNOME and macOS so far; this desktop is unsupported ({sys.platform}).")
