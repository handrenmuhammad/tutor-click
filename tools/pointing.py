"""Measure how accurately the model points at named controls.

Ground truth comes from the accessibility tree (AT-SPI) of a running GTK app, so it only works
for apps that expose one. Pinta does; Blender does not.

    python tools/pointing.py collect pinta     # Pinta must be open: saves a screenshot and its targets
    python tools/pointing.py show SET          # draws the target boxes on the screenshot, to check them by eye
    TUTOR_CLICK_BACKEND=codex python tools/pointing.py run SET [N]   # asks that provider to point at N targets

A set is a folder under ~/.local/share/tutor-click/pointing/. Everything outside the app's window is
blanked in the saved screenshot, so a set can be sent to another provider later without the rest
of the screen.
"""
import json
import statistics
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import gi
import numpy as np

gi.require_version("Atspi", "2.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Atspi, GdkPixbuf, GLib

from tutor_click import config, screen, verify
from tutor_click.brain import Tutor
from tutor_click.screen import Shot

ROOT = Path.home() / ".local/share/tutor-click/pointing"
ROLES = ("button", "push button", "toggle button", "radio button", "check box")
MAX_NODES = 4000
PARALLEL = 3


def _encode(pixels: np.ndarray) -> bytes:
    h, w, _ = pixels.shape
    pb = GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(pixels.tobytes()), GdkPixbuf.Colorspace.RGB, False, 8, w, h, w * 3)
    return bytes(pb.save_to_bufferv("jpeg", ["quality"], ["90"])[1])


def _controls(frame_hint: str) -> list[dict]:
    """Named, showing controls of the app whose window title contains frame_hint: window-relative boxes."""
    desktop = Atspi.get_desktop(0)
    apps = [desktop.get_child_at_index(i) for i in range(desktop.get_child_count())]
    app = next((a for a in apps if a and any(
        frame_hint.lower() in (a.get_child_at_index(i).get_name() or "").lower() for i in range(a.get_child_count()))), None)
    if app is None:
        sys.exit(f"no app with a window titled like {frame_hint!r} on the accessibility bus")
    found, seen = [], 0

    def walk(node, depth):
        nonlocal seen
        if node is None or depth > 30 or seen >= MAX_NODES:
            return
        seen += 1
        label = (node.get_name() or node.get_description() or "").split("\n")[0].strip().rstrip(".")
        if node.get_role_name() in ROLES and label and node.get_state_set().contains(Atspi.StateType.SHOWING):
            e = node.get_extents(Atspi.CoordType.WINDOW)
            if e.width > 4 and e.height > 4:
                found.append({"name": label, "box": [e.x, e.y, e.width, e.height]})
        for i in range(node.get_child_count()):
            walk(node.get_child_at_index(i), depth + 1)

    walk(app, 0)
    # A name that appears at two places is ambiguous to ask for; the same box twice is one control.
    boxes = {}
    for c in found:
        boxes.setdefault(c["name"], set()).add(tuple(c["box"]))
    return [{"name": n, "box": list(next(iter(b)))} for n, b in boxes.items() if len(b) == 1]


def collect(cls_hint: str):
    clients = json.loads(subprocess.check_output(["hyprctl", "clients", "-j"]))
    win = next((c for c in clients if cls_hint.lower() in c["class"].lower()), None)
    if win is None:
        sys.exit(f"no open window with a class like {cls_hint!r}")
    mon = screen.focused_monitor()
    shot = screen.capture(mon, config.MAX_EDGE)
    px = verify.pixels(shot.jpeg).copy()
    f = shot.factor
    left, top = round((win["at"][0] - mon.x) * f), round((win["at"][1] - mon.y) * f)
    right, bottom = round(left + win["size"][0] * f), round(top + win["size"][1] * f)
    keep = np.zeros(px.shape[:2], bool)
    keep[max(0, top):bottom, max(0, left):right] = True
    px[~keep] = 30  # nothing but the app's window leaves this machine with the set
    out = ROOT / f"{cls_hint}-{time.strftime('%Y%m%d-%H%M%S')}"
    out.mkdir(parents=True)
    (out / "shot.jpg").write_bytes(_encode(px))
    targets = _controls(cls_hint)
    meta = {
        "class": win["class"], "title": win["title"], "window_at": win["at"], "window_size": win["size"],
        "monitor": {"name": mon.name, "x": mon.x, "y": mon.y, "w": mon.logical_w, "h": mon.logical_h, "scale": mon.scale},
        "shot": {"w": shot.width, "h": shot.height, "factor": shot.factor}, "targets": targets,
    }
    (out / "set.json").write_text(json.dumps(meta, indent=1))
    print(f"{len(targets)} targets saved to {out}")


def _load(path: str) -> tuple[Path, dict, Shot]:
    d = Path(path) if Path(path).is_dir() else ROOT / path
    meta = json.loads((d / "set.json").read_text())
    s = meta["shot"]
    return d, meta, Shot((d / "shot.jpg").read_bytes(), s["w"], s["h"], s["factor"])


def _shot_box(meta: dict, box: list) -> tuple[float, float, float, float]:
    """A window-relative logical box in screenshot pixels: left, top, right, bottom."""
    f = meta["shot"]["factor"]
    x = (meta["window_at"][0] - meta["monitor"]["x"] + box[0]) * f
    y = (meta["window_at"][1] - meta["monitor"]["y"] + box[1]) * f
    return x, y, x + box[2] * f, y + box[3] * f


def show(path: str):
    d, meta, shot = _load(path)
    px = verify.pixels(shot.jpeg).copy()
    for t in meta["targets"]:
        left, top, right, bottom = (round(v) for v in _shot_box(meta, t["box"]))
        px[top:bottom, [left, right - 1]] = (0, 255, 0)
        px[[top, bottom - 1], left:right] = (0, 255, 0)
    f = meta["shot"]["factor"]
    wl, wt = round((meta["window_at"][0] - meta["monitor"]["x"]) * f), round((meta["window_at"][1] - meta["monitor"]["y"]) * f)
    crop = px[max(0, wt):wt + round(meta["window_size"][1] * f), max(0, wl):wl + round(meta["window_size"][0] * f)]
    big = np.ascontiguousarray(crop.repeat(2, axis=0).repeat(2, axis=1))  # easier to judge by eye
    (d / "boxes.jpg").write_bytes(_encode(big))
    print(d / "boxes.jpg")


def run(path: str, n: int):
    d, meta, shot = _load(path)
    targets = meta["targets"][:n]
    window = f'{meta["class"]} - "{meta["title"]}"'

    def ask(t):
        t0 = time.monotonic()
        try:
            step = Tutor().step(shot, window, f"Where is the {t['name']} button? Point at it.", "guide")
        except Exception as e:  # noqa: BLE001 - one failed call must not lose the rest of the run
            return {"name": t["name"], "error": str(e.__cause__ or e)[:200]}
        left, top, right, bottom = _shot_box(meta, t["box"])
        r = {"name": t["name"], "secs": round(time.monotonic() - t0, 1), "point": step.point,
             "box": [round(v, 1) for v in (left, top, right, bottom)], "size_px": [round(right - left, 1), round(bottom - top, 1)]}
        if step.point is None:
            return r | {"hit": False, "error_px": None}
        x, y = step.point
        cx, cy = (left + right) / 2, (top + bottom) / 2
        # +0.5: the model names a pixel, the box is in continuous coordinates
        return r | {"hit": left <= x + 0.5 <= right and top <= y + 0.5 <= bottom,
                    "error_px": round(((x + 0.5 - cx) ** 2 + (y + 0.5 - cy) ** 2) ** 0.5, 1)}

    with ThreadPoolExecutor(PARALLEL) as pool:
        results = list(pool.map(ask, targets))
    answered = [r for r in results if "error" not in r]
    pointed = [r for r in answered if r["error_px"] is not None]
    hits = sum(r["hit"] for r in answered)
    f = meta["shot"]["factor"]
    who = Tutor().provider
    effort = {"claude-code": config.EFFORT, "api": config.EFFORT, "codex": config.CODEX_EFFORT}.get(who.id, "")
    print(f"{who.id} {who.model or 'default model'} {effort}, screenshot {shot.width}x{shot.height}, 1 screenshot px = {1 / f:.2f} screen px")
    print(f"targets {len(targets)}, answered {len(answered)}, pointed {len(pointed)}, inside the control {hits}")
    if pointed:
        errs = sorted(r["error_px"] for r in pointed)
        print(f"distance from the control's centre, screenshot px: median {statistics.median(errs):.1f}, p90 {errs[int(len(errs) * 0.9) - 1]:.1f}, max {errs[-1]:.1f}")
        print(f"median control size: {statistics.median(r['size_px'][0] for r in pointed):.0f} x {statistics.median(r['size_px'][1] for r in pointed):.0f} screenshot px")
    for r in results:
        if "error" in r:
            print(f"  ERROR {r['name']}: {r['error']}")
        elif not r["hit"]:
            print(f"  MISS  {r['name']}: pointed {r['point']}, control {r['box']}, off by {r['error_px']} px")
    secs = sorted(r["secs"] for r in answered)
    if secs:
        print(f"seconds per answer ({PARALLEL} at a time): median {statistics.median(secs):.1f}, p90 {secs[int(len(secs) * 0.9) - 1]:.1f}, max {secs[-1]:.1f}")
    out = d / f"results-{who.id}-{who.model or 'default'}-{effort}-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps({"backend": who.id, "model": who.model, "effort": effort, "results": results}, indent=1))
    print(f"saved {out}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) >= 2 and args[0] == "collect":
        collect(args[1])
    elif len(args) >= 2 and args[0] == "show":
        show(args[1])
    elif len(args) >= 2 and args[0] == "run":
        run(args[1], int(args[2]) if len(args) > 2 else 30)
    else:
        sys.exit(__doc__)
