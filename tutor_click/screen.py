"""Hyprland monitor/window info and screenshots via grim."""
import json
import subprocess
from dataclasses import dataclass


@dataclass
class Monitor:
    name: str
    logical_w: float
    logical_h: float
    scale: float
    x: int = 0  # position in the global layout
    y: int = 0


@dataclass
class Shot:
    jpeg: bytes
    width: int  # image pixels
    height: int
    factor: float  # image px = logical px * factor


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
    w, h = _jpeg_size(jpeg)
    return Shot(jpeg, w, h, w / mon.logical_w)


def _jpeg_size(data: bytes) -> tuple[int, int]:
    """Read width/height from the JPEG SOF marker."""
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xC0, 0xC1, 0xC2):
            h = int.from_bytes(data[i + 5 : i + 7], "big")
            w = int.from_bytes(data[i + 7 : i + 9], "big")
            return w, h
        i += 2 + int.from_bytes(data[i + 2 : i + 4], "big")
    raise ValueError("not a JPEG or no SOF marker")
