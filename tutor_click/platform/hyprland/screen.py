"""Hyprland monitor/window info and screenshots via grim."""
import json
import subprocess

from ..base import Monitor, Shot, jpeg_size


def focused_monitor() -> Monitor:
    mons = json.loads(subprocess.check_output(["hyprctl", "monitors", "-j"]))
    m = next((m for m in mons if m.get("focused")), mons[0])
    w, h = m["width"], m["height"]
    if m.get("transform", 0) % 2 == 1:  # rotated 90/270
        w, h = h, w
    s = m["scale"]
    return Monitor(m["name"], w / s, h / s, s, m["x"], m["y"])


def active_window() -> str:
    try:
        w = json.loads(subprocess.check_output(["hyprctl", "activewindow", "-j"]))
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        return "unknown"
    if not w:
        return "none (desktop)"
    return f'{w.get("class", "?")} - "{w.get("title", "")}"'


def capture(mon: Monitor, max_edge: int) -> Shot:
    # grim -s is relative to logical size; never upscale past the native scale.
    s = min(mon.scale, max_edge / max(mon.logical_w, mon.logical_h))
    jpeg = subprocess.check_output(
        ["grim", "-o", mon.name, "-s", f"{s:.4f}", "-t", "jpeg", "-q", "85", "-"]
    )
    w, h = jpeg_size(jpeg)
    return Shot(jpeg, w, h, w / mon.logical_w)
