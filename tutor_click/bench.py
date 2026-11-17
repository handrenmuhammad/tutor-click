"""`tutor-click bench`: measure whether the tutor's input actually arrives.

Opens a probe window that records every input event it receives, drives tutor_click.hands against it,
and reports received versus sent for each kind of action: once with the probe focused, once with
another window focused first. It takes over the mouse and keyboard while it runs, so keep your
hands off until it finishes. Ctrl-C in the terminal stops it.
"""
import fcntl
import json
import os
import random
import signal
import statistics
import string
import subprocess
import sys
import threading
import time
from pathlib import Path

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk

from . import config, hands, screen
from .hands import Hands, Refused, Window, parse_combo

APP_ID = "dev.handren.TutorClick.probe"
PROBE, DECOY = "Tutor probe", "Tutor probe decoy"
KINDS = ("click", "double", "right", "drag", "key", "type", "scroll")
COMBOS = ("a", "shift+a", "ctrl+z", "ctrl+shift+s", "g", "2", "enter", "tab", "escape", "f2",
          "numpad1", "delete", "alt+a", "ctrl+a", "space", "shift+tab")
TYPE_CHARS = string.ascii_letters + string.digits + " -=[];',./!@#$%^&*()_+{}:<>?"
TYPE_LEN = 20
MARGIN = 20  # px kept clear of the probe's edges
POS_TOL = 1.5  # px between where the cursor was sent and where the press arrived
WAIT_SECS = 0.6  # how long an action's events may take to arrive
LINGER_SECS = 0.05  # then listen a little longer for duplicates
MAX_REFUSALS = 10  # in a row: the probe is covered or gone
SAMPLES_KEPT = 10  # failure details kept per row
MOD_BITS = {"CTRL": Gdk.ModifierType.CONTROL_MASK, "SHIFT": Gdk.ModifierType.SHIFT_MASK, "ALT": Gdk.ModifierType.ALT_MASK}
ALL_MODS = int(Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK | Gdk.ModifierType.ALT_MASK)
BENCH_DIR = config.LOG_FILE.parent / "bench"


# ---- judging: pure, tested in tests/test_bench.py -----------------------------
def _near(ev: dict, x: float, y: float) -> bool:
    return abs(ev["x"] - x) <= POS_TOL and abs(ev["y"] - y) <= POS_TOL


def judge(kind: str, expect: dict, events: list[dict]) -> tuple[str, str]:
    """Compare what the probe received with what was sent.

    Returns (verdict, detail). Verdicts: "ok", "missing" (nothing or too little arrived),
    "wrong" (arrived but not what was sent), "extra" (more arrived than was sent).
    """
    presses = [e for e in events if e["type"] == "press"]
    releases = [e for e in events if e["type"] == "release"]
    keys = [e for e in events if e["type"] == "key"]
    scrolls = [e for e in events if e["type"] == "scroll"]

    if kind in ("click", "double", "right"):
        want = expect["presses"]
        if len(presses) < want or len(releases) < want:
            return "missing", f"{len(presses)} presses, {len(releases)} releases of {want}"
        if len(presses) > want or len(releases) > want:
            return "extra", f"{len(presses)} presses, {len(releases)} releases of {want}"
        if any(e["button"] != expect["button"] for e in presses + releases):
            return "wrong", f"button {presses[0]['button']} instead of {expect['button']}"
        if not all(_near(e, expect["x"], expect["y"]) for e in presses):
            return "wrong", f"pressed at ({presses[0]['x']:.1f}, {presses[0]['y']:.1f}), sent to ({expect['x']}, {expect['y']})"
        return "ok", ""

    if kind == "drag":
        if not presses or not releases:
            return "missing", f"{len(presses)} presses, {len(releases)} releases of 1"
        if len(presses) > 1 or len(releases) > 1:
            return "extra", f"{len(presses)} presses, {len(releases)} releases of 1"
        if not _near(presses[0], expect["x"], expect["y"]):
            return "wrong", f"pressed at ({presses[0]['x']:.1f}, {presses[0]['y']:.1f}), sent to ({expect['x']}, {expect['y']})"
        if not _near(releases[0], expect["x2"], expect["y2"]):
            return "wrong", f"released at ({releases[0]['x']:.1f}, {releases[0]['y']:.1f}), sent to ({expect['x2']}, {expect['y2']})"
        return "ok", ""

    if kind == "key":
        if not keys:
            return "missing", "no key press"
        if len(keys) > 1:
            return "extra", f"{len(keys)} key presses of 1"
        if keys[0]["keycode"] not in expect["keycodes"]:
            return "wrong", f"keycode {keys[0]['keycode']}, wanted one of {sorted(expect['keycodes'])}"
        if keys[0]["state"] & ALL_MODS != expect["mods"]:
            return "wrong", f"modifiers {keys[0]['state'] & ALL_MODS:#x}, wanted {expect['mods']:#x}"
        return "ok", ""

    if kind == "type":
        got = "".join(e["char"] for e in keys)
        if got == expect["text"]:
            return "ok", ""
        if not got:
            return "missing", "no characters"
        if len(got) < len(expect["text"]):
            return "missing", f"{len(got)} of {len(expect['text'])} characters: {got!r}"
        if len(got) > len(expect["text"]):
            return "extra", f"{len(got)} of {len(expect['text'])} characters: {got!r}"
        return "wrong", f"got {got!r}, sent {expect['text']!r}"

    if kind == "scroll":
        got = sum(e["notches"] for e in scrolls)
        if got == expect["notches"]:
            return "ok", ""
        if not scrolls:
            return "missing", "no wheel event"
        if abs(got) < abs(expect["notches"]):
            return "missing", f"{got:g} of {expect['notches']} notches"
        if abs(got) > abs(expect["notches"]) and got * expect["notches"] > 0:
            return "extra", f"{got:g} of {expect['notches']} notches"
        return "wrong", f"{got:g} notches, sent {expect['notches']}"

    raise ValueError(kind)


def summarize(results: list[dict]) -> list[dict]:
    """One row per (focus state, kind), in the order they ran."""
    rows: dict[tuple[str, str], dict] = {}
    for r in results:
        row = rows.setdefault((r["state"], r["kind"]), {
            "state": r["state"], "kind": r["kind"], "sent": 0, "ok": 0, "missing": 0, "wrong": 0,
            "extra": 0, "refused": 0, "not_focused_after": 0, "secs": [], "samples": [],
        })
        row["sent"] += 1
        row[r["verdict"]] += 1
        row["secs"].append(r["secs"])
        if r.get("focused_after") is False:
            row["not_focused_after"] += 1
        if r["verdict"] != "ok" and len(row["samples"]) < SAMPLES_KEPT:
            row["samples"].append(r["detail"])
    for row in rows.values():
        row["median_secs"] = round(statistics.median(row.pop("secs")), 3)
    return list(rows.values())


def table(rows: list[dict]) -> str:
    head = f"{'focus':<10} {'kind':<7} {'sent':>5} {'ok':>5} {'missing':>8} {'wrong':>6} {'extra':>6} {'refused':>8} {'median s':>9}"
    lines = [head, "-" * len(head)]
    for r in rows:
        lines.append(f"{r['state']:<10} {r['kind']:<7} {r['sent']:>5} {r['ok']:>5} {r['missing']:>8} {r['wrong']:>6} {r['extra']:>6} {r['refused']:>8} {r['median_secs']:>9.3f}")
        lines.extend(f"    e.g. {s}" for s in r["samples"][:3])
    return "\n".join(lines)


# ---- the probe window --------------------------------------------------------
class Probe:
    def __init__(self, n: int, kinds: tuple[str, ...]):
        self.n, self.kinds = n, kinds
        self.app = Gtk.Application(application_id=APP_ID, flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.app.connect("activate", self._activate)
        self.events: list[dict] = []
        self.arrived = threading.Condition()
        self.stop = threading.Event()
        self.report: dict | None = None

    def run(self) -> dict | None:
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, lambda: (self.stop.set(), False)[1])
        self.app.run([])
        return self.report

    def _activate(self, app):
        for title in (PROBE, DECOY):
            win = Gtk.ApplicationWindow(application=app, title=title)
            win.set_decorated(False)  # so event coordinates start at the window's corner
            win.set_default_size(900, 600)
            win.set_child(Gtk.Label(label=f"{title}\n\nMeasuring the tutor's input. Hands off until it closes."))
            if title == PROBE:
                ctrl = Gtk.EventControllerLegacy()
                ctrl.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
                ctrl.connect("event", self._on_event)
                win.add_controller(ctrl)
            win.present()
        threading.Thread(target=self._work, daemon=True).start()

    def _on_event(self, ctrl, _ev):
        ev = ctrl.get_current_event()  # PyGObject passes the signal's event argument as None
        if ev is None:
            return False
        kind = ev.get_event_type()
        rec = None
        if kind in (Gdk.EventType.BUTTON_PRESS, Gdk.EventType.BUTTON_RELEASE):
            _, x, y = ev.get_position()
            rec = {"type": "press" if kind == Gdk.EventType.BUTTON_PRESS else "release", "button": ev.get_button(), "x": x, "y": y}
        elif kind == Gdk.EventType.KEY_PRESS:
            code = Gdk.keyval_to_unicode(ev.get_keyval())
            rec = {"type": "key", "keycode": ev.get_keycode(), "state": int(ev.get_modifier_state()), "char": chr(code) if code else ""}
            if ev.is_modifier():
                rec = None  # a Shift or Ctrl going down on its own is not a key of the combo
        elif kind == Gdk.EventType.SCROLL:
            direction = ev.get_direction()
            if direction == Gdk.ScrollDirection.SMOOTH:
                notches = ev.get_deltas()[1]
            else:
                notches = {Gdk.ScrollDirection.UP: -1, Gdk.ScrollDirection.DOWN: 1}.get(direction, 0)
            rec = {"type": "scroll", "notches": notches}
        if rec:
            with self.arrived:
                self.events.append(rec)
                self.arrived.notify_all()
        return False

    # ---- worker thread ---------------------------------------------------
    def _work(self):
        try:
            self.report = self._bench()
        except Exception as e:  # noqa: BLE001 - report it and still close the windows
            print(f"bench failed: {e!r}", file=sys.stderr)
        finally:
            GLib.idle_add(self.app.quit)

    def _clients(self) -> dict[str, dict]:
        clients = json.loads(subprocess.check_output(["hyprctl", "clients", "-j"]))
        return {c["title"]: c for c in clients if c["class"] == APP_ID}

    def _take(self, kind: str, expect: dict) -> tuple[str, str]:
        """Wait for the action's events, listen a little longer for duplicates, then judge."""
        end = time.monotonic() + WAIT_SECS
        with self.arrived:
            while judge(kind, expect, self.events)[0] == "missing" and (left := end - time.monotonic()) > 0:
                self.arrived.wait(left)
        time.sleep(LINGER_SECS)
        with self.arrived:
            events, self.events = self.events, []
        return judge(kind, expect, events)

    def _bench(self) -> dict | None:
        end = time.monotonic() + 5
        while len(self._clients()) < 2:
            if time.monotonic() > end:
                raise RuntimeError("the probe windows did not appear in hyprctl clients")
            time.sleep(0.1)
        time.sleep(0.5)  # let the layout settle
        mon = screen.focused_monitor()
        h = Hands(mon)
        h.target = APP_ID
        rng = random.Random(1)  # same points every run, so runs compare
        display = Gdk.Display.get_default()
        results = []

        def focus(title):
            hands._dispatch(f'focus({{window="address:{self._clients()[title]["address"]}"}})')

        for state in ("focused", "unfocused"):
            for kind in self.kinds:
                refusals = 0
                for _ in range(self.n):
                    if self.stop.is_set() or refusals >= MAX_REFUSALS:
                        break
                    probe = self._clients()[PROBE]
                    w, hgt = probe["size"]
                    # probe-local px -> logical px on the monitor, which is what Hands takes
                    ox, oy = probe["at"][0] - mon.x, probe["at"][1] - mon.y
                    focus(PROBE if state == "focused" else DECOY)
                    h.last = Window(probe["address"], APP_ID, PROBE)  # as after any click in a lesson
                    time.sleep(0.05)
                    with self.arrived:
                        self.events.clear()
                    x, y = rng.randint(MARGIN, w - MARGIN), rng.randint(MARGIN, hgt - MARGIN)
                    t0 = time.monotonic()
                    try:
                        if kind in ("click", "double", "right"):
                            expect = {"button": 3 if kind == "right" else 1, "presses": 2 if kind == "double" else 1, "x": x, "y": y}
                            h.click(ox + x, oy + y, button="right" if kind == "right" else "left", count=expect["presses"])
                        elif kind == "drag":
                            x2, y2 = rng.randint(MARGIN, w - MARGIN), rng.randint(MARGIN, hgt - MARGIN)
                            expect = {"x": x, "y": y, "x2": x2, "y2": y2}
                            h.drag(ox + x, oy + y, ox + x2, oy + y2)
                        elif kind == "key":
                            combo = rng.choice(COMBOS)
                            mods, key = parse_combo(combo)
                            if key.startswith("code:"):
                                codes = {int(key[5:])}
                            else:
                                codes = {k.keycode for k in display.map_keyval(Gdk.keyval_from_name(key))[1]}
                            expect = {"combo": combo, "keycodes": codes, "mods": sum(int(MOD_BITS[m]) for m in mods.split())}
                            h.keys(combo)
                        elif kind == "type":
                            expect = {"text": "".join(rng.choice(TYPE_CHARS) for _ in range(TYPE_LEN))}
                            h.type_text(expect["text"])
                        else:
                            expect = {"notches": rng.choice((-5, -4, -3, -2, -1, 1, 2, 3, 4, 5))}
                            h.scroll(ox + x, oy + y, expect["notches"])
                    except Refused as e:
                        refusals += 1
                        results.append({"state": state, "kind": kind, "verdict": "refused", "detail": f"{e} {e.__cause__ or ''}".strip(), "secs": time.monotonic() - t0})
                        continue
                    refusals = 0
                    verdict, detail = self._take(kind, expect)
                    result = {"state": state, "kind": kind, "verdict": verdict, "secs": time.monotonic() - t0 - LINGER_SECS,
                              "detail": f"{expect.get('combo', '')} {detail}".strip()}
                    if kind in ("click", "double", "right", "drag"):
                        active = json.loads(subprocess.check_output(["hyprctl", "activewindow", "-j"]) or "{}")
                        result["focused_after"] = active.get("address") == probe["address"]
                    results.append(result)
                if refusals >= MAX_REFUSALS:
                    print(f"{state} {kind}: stopped after {MAX_REFUSALS} refusals in a row", file=sys.stderr)

        version = subprocess.run(["hyprctl", "version", "-j"], capture_output=True, text=True, check=False).stdout
        return {
            "when": time.strftime("%Y-%m-%d %H:%M:%S"),
            "n": self.n,
            "stopped_early": self.stop.is_set(),
            "hyprland": (json.loads(version) if version.strip().startswith("{") else {}).get("tag", "?"),
            "monitor": {"name": mon.name, "x": mon.x, "y": mon.y, "w": mon.logical_w, "h": mon.logical_h, "scale": mon.scale},
            "probe": {"at": probe["at"], "size": probe["size"], "xwayland": probe["xwayland"]},
            "rows": summarize(results),
        }


def main(args: list[str]):
    n, kinds = 100, KINDS
    for a in args:
        if a.isdigit():
            n = int(a)
        elif all(k in KINDS for k in a.split(",")):
            kinds = tuple(a.split(","))
        else:
            sys.exit(f"usage: tutor-click bench [N] [{','.join(KINDS)}]")
    if not 0 < n <= 1000:
        sys.exit("N must be between 1 and 1000")
    # Two runs at once would fight over the cursor and each other's probe windows.
    config.RUNTIME.mkdir(parents=True, exist_ok=True)
    lock = (config.RUNTIME / "bench.lock").open("w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit("another tutor-click bench is already running")
    report = Probe(n, kinds).run()
    if report is None:
        sys.exit(1)
    m = report["monitor"]
    print(f"Hyprland {report['hyprland']}, {m['name']} {m['w']:g}x{m['h']:g} scale {m['scale']:g} at ({m['x']}, {m['y']}), "
          f"probe {report['probe']['size'][0]}x{report['probe']['size'][1]} at {tuple(report['probe']['at'])}, n={report['n']}"
          + (" (stopped early)" if report["stopped_early"] else ""))
    print(table(report["rows"]))
    unfocused = sum(r["not_focused_after"] for r in report["rows"])
    if unfocused:
        print(f"{unfocused} pointer actions left the probe unfocused afterwards")
    BENCH_DIR.mkdir(parents=True, exist_ok=True)
    out = BENCH_DIR / f"bench-{time.strftime('%Y%m%d-%H%M%S')}.json"
    with out.open("w") as f:
        f.write(json.dumps(report, indent=1))
        f.flush()
        os.fsync(f.fileno())  # this machine has lost unsynced reports to a hard reset
    print(f"saved {out}")
    for old in sorted(Path(BENCH_DIR).glob("bench-*.json"))[:-20]:  # bounded: keep the last 20 reports
        old.unlink()
