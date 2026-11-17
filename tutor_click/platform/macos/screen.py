"""The screen and its windows on macOS: screencapture and sips, both part of the system."""
import subprocess
import tempfile
from pathlib import Path

from ..base import Monitor, Shot, Window, jpeg_size
from . import native


def app_windows() -> list[Window]:
    """Ordinary app windows, front to back. Menus, the Dock and our own overlay and panel sit on other layers."""
    return [
        Window(f"{w['pid']}:{w['number']}", w["owner"], w["title"], (round(w["x"]), round(w["y"])), (round(w["w"]), round(w["h"])))
        for w in native.windows() if w["layer"] == 0 and w["w"] > 1 and w["h"] > 1
    ]


def pid_of(win: Window) -> int:
    return int(win.address.split(":")[0])


def active_window() -> str:
    front = native.frontmost()
    if front is None:
        return "unknown"
    pid, name = front
    title = next((w.title for w in app_windows() if pid_of(w) == pid), "")
    return f'{name} - "{title}"'


def capture(mon: Monitor, max_edge: int) -> Shot:
    with tempfile.TemporaryDirectory() as tmp:  # under the user's own TMPDIR; gone when the call returns
        path = Path(tmp) / "shot.jpg"
        rect = f"{round(mon.x)},{round(mon.y)},{round(mon.logical_w)},{round(mon.logical_h)}"
        subprocess.run(["screencapture", "-x", "-t", "jpg", "-R", rect, str(path)], check=True, capture_output=True)
        # A Retina screen comes out at twice the size. sips never enlarges.
        subprocess.run(["sips", "-Z", str(max_edge), "-s", "formatOptions", "85", str(path)], check=True, capture_output=True)
        jpeg = path.read_bytes()
    w, h = jpeg_size(jpeg)
    return Shot(jpeg, w, h, w / mon.logical_w)
