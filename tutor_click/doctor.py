"""`tutor-click doctor`: checks each thing the app needs, one at a time, and prints a report to send back.

Nothing leaves the computer unless --ask is given. The report names apps, never window titles.
"""
import importlib.util
import math
import platform as system
import struct
import sys
import threading
import time
import traceback
import wave

from . import config, providers, verify
from .brain import Tutor
from .platform.base import Refused
from .voice import Recorder

FLAGS = ("--input", "--ask", "--no-sound")
RING = 22  # radius of the overlay's pointer ring, logical px
POINTS = 16  # places on the ring that are looked at


class Problem(Exception):
    """A check found something wrong. The message says what, and what to do about it."""


class Skip(Exception):
    """A check was not run. The message says why."""


class Report:
    def __init__(self):
        self.lines: list[str] = []
        self.failed = 0

    def note(self, name: str, detail: str):
        self.lines.append(f"         {name}: {detail}")

    def check(self, name: str, fn):
        t0 = time.monotonic()
        try:
            status, detail = "ok", fn()
        except Skip as e:
            status, detail = "skipped", str(e)
        except Problem as e:
            status, detail = "FAILED", str(e)
        except Exception as e:  # noqa: BLE001 - whatever went wrong belongs in the report
            where = traceback.extract_tb(e.__traceback__)[-1]
            status, detail = "FAILED", f"{type(e).__name__}: {e} (at {where.filename.rsplit('/', 1)[-1]}:{where.lineno})"
        self.failed += status == "FAILED"
        self.lines.append(f"{status:<8} {name}: {detail} [{time.monotonic() - t0:.1f} s]")

    def text(self) -> str:
        verdict = f"{self.failed} check(s) FAILED" if self.failed else "every check that ran passed"
        return "\n".join([*self.lines, "", verdict])


def ring_points(pixels, cx: float, cy: float, factor: float) -> int:
    """How many of POINTS places on the pointer ring around (cx, cy), in logical px, are the overlay's yellow."""
    h, w, _ = pixels.shape
    hits = 0
    for k in range(POINTS):
        a = 2 * math.pi * k / POINTS
        x, y = round((cx + RING * math.cos(a)) * factor), round((cy + RING * math.sin(a)) * factor)
        near = pixels[max(0, y - 1):min(h, y + 2), max(0, x - 1):min(w, x + 2)].reshape(-1, 3).astype(int)
        hits += any(r > 150 and g > 110 and r - b > 70 and g - b > 40 for r, g, b in near)  # the line is thin: take the best of 3 x 3
    return hits


def tone(rate: int, secs: float = 0.35, hz: float = 440.0) -> bytes:
    n = int(rate * secs)
    return b"".join(struct.pack("<h", int(9000 * math.sin(2 * math.pi * hz * i / rate) * min(1.0, i / 400, (n - i) / 400))) for i in range(n))


def run(desk, args: list[str]) -> int:
    if set(args) - set(FLAGS):
        sys.exit(f"usage: tutor-click doctor {' '.join(f'[{f}]' for f in FLAGS)}")
    report, seen = Report(), {}
    report.note("Tutor Click doctor", time.strftime("%Y-%m-%d %H:%M:%S"))
    report.note("system", f"{system.system()} {system.mac_ver()[0] or system.release()}, {system.machine()}, Python {system.python_version()}")
    report.note("desktop backend", getattr(desk, "NAME", desk.__name__))

    def start(app):  # on the main loop: windows have to be made here
        try:
            seen["mon"] = desk.focused_monitor()
            menu = [(pid, cls.menu) for pid, cls in providers.PROVIDERS.items()]
            seen["ui"] = desk.make_ui(app, seen["mon"], lambda *_: None, menu, config.BACKEND)
        except Exception as e:  # noqa: BLE001 - reported by the check that needs it
            seen["ui_error"] = e
        threading.Thread(target=_checks, args=(desk, app, report, seen, set(args)), daemon=True).start()

    app = desk.App(start)
    app.run()
    text = report.text()
    print(text)
    try:
        config.LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        out = config.LOG_FILE.parent / f"doctor-{time.strftime('%Y%m%d-%H%M%S')}.txt"
        out.write_text(text + "\n")
        print(f"saved {out}")
    except OSError as e:
        print("could not save the report:", e)
    return 1 if report.failed else 0


def _checks(desk, app, report: Report, seen: dict, flags: set):
    try:
        report.check("permissions", lambda: _permissions(desk))
        report.check("screen", lambda: _screen(seen))
        report.check("screenshot", lambda: _screenshot(desk, seen))
        report.check("windows", lambda: _windows(desk, seen))
        report.check("overlay and panel", lambda: _overlay(desk, seen))
        report.check("mouse and keyboard", lambda: _input(desk, app, seen, "--input" in flags))
        report.check("microphone", lambda: _microphone(desk, "--no-sound" in flags))
        report.check("speaker", lambda: _speaker(desk, "--no-sound" in flags))
        report.check("voice models", _voices)
        report.check("AI provider", lambda: _provider(seen))
        report.check("one real question to the AI", lambda: _ask(seen, "--ask" in flags))
    finally:
        app.quit()


def _permissions(desk) -> str:
    wanted = desk.permissions()
    missing = [f"{what} is OFF. {how}" for what, granted, how in wanted if not granted]
    if missing:
        raise Problem(" | ".join(missing))
    return "granted: " + ", ".join(what for what, _, _ in wanted) if wanted else "this desktop asks for none"


def _screen(seen) -> str:
    if "mon" not in seen:
        raise Problem(f"could not read the screen: {seen.get('ui_error')!r}")
    m = seen["mon"]
    return f"{m.name or 'screen'} {m.logical_w:g}x{m.logical_h:g} at ({m.x}, {m.y}), scale {m.scale:g}"


def _screenshot(desk, seen) -> str:
    shot = seen["shot"] = desk.capture(seen["mon"], config.MAX_EDGE)
    contrast = float(verify.pixels(shot.jpeg).std())
    if contrast < 3:
        raise Problem(f"the screenshot is one flat colour ({shot.width}x{shot.height}): the screen is not being captured")
    return f"{shot.width}x{shot.height}, {len(shot.jpeg) // 1024} KiB, 1 screenshot px = {1 / shot.factor:.2f} screen px, contrast {contrast:.0f}"


def _windows(desk, seen) -> str:
    m = seen["mon"]
    front = desk.active_window().split(" - ")[0]
    seen["hands"] = hands = desk.Hands(m)
    under = hands.window_at(m.x + m.logical_w / 2, m.y + m.logical_h / 2)
    return f"in front: {front}; under the middle of the screen: {under.cls if under else 'no app window'}"


def _overlay(desk, seen) -> str:
    if "ui" not in seen:
        raise Problem(f"the overlay and panel could not be made: {seen.get('ui_error')!r}")
    overlay, panel = seen["ui"]
    m = seen["mon"]
    cx, cy = m.logical_w / 2, m.logical_h / 2
    time.sleep(0.5)
    overlay.show_step((cx, cy), "Doctor", "Tutor Click doctor is checking that it can draw on your screen.")
    time.sleep(desk.MOVE_SECS + 0.5)

    def count() -> int:
        shot = desk.capture(m, config.MAX_EDGE)
        return ring_points(verify.pixels(shot.jpeg), cx, cy, shot.factor)

    shown = count()
    overlay.set_hidden(True)
    panel.set_hidden(True)
    time.sleep(0.2)
    hidden = count()
    overlay.set_hidden(False)
    panel.set_hidden(False)
    overlay.clear()
    if shown < POINTS // 2:
        raise Problem(f"the pointer ring is not in a screenshot of the screen ({shown} of {POINTS} points): the overlay is not showing, "
                      "or the screenshot does not include other windows")
    if hidden > POINTS // 4:
        raise Problem(f"the overlay was still in the screenshot after it was told to hide ({hidden} of {POINTS} points)")
    return f"pointer ring seen in a screenshot ({shown} of {POINTS} points), gone when hidden ({hidden})"


def _input(desk, app, seen, wanted: bool) -> str:
    if not wanted:
        raise Skip("add --input to test it. That takes over the mouse and keyboard for a few seconds")
    if not hasattr(desk, "make_probe"):
        raise Skip("on this desktop use: tutor-click bench 20")
    m = seen["mon"]
    hands = seen.get("hands") or desk.Hands(m)
    probe = desk.make_probe(app, m)
    time.sleep(0.8)
    results = []

    def local(point):
        return point[0] - m.x, point[1] - m.y

    def near(event, point) -> bool:
        return event["x"] is not None and abs(event["x"] - point[0]) <= 3 and abs(event["y"] - point[1]) <= 3

    def step(name, act, passed):
        probe.clear()
        try:
            act()
            time.sleep(0.4)
            ok = passed(list(probe.events))
            results.append(f"{name} {'worked' if ok else 'DID NOT ARRIVE as sent: ' + str(list(probe.events))[:300]}")
        except Refused as e:
            results.append(f"{name} was REFUSED ({e.code}): {e}")

    a, b, c = probe.spot(0.5, 0.35), probe.spot(0.25, 0.5), probe.spot(0.75, 0.5)
    step("click", lambda: hands.click(*local(a)), lambda ev: any(e["kind"] == "press" and e["button"] == "LeftButton" and near(e, a) for e in ev))
    step("double click", lambda: hands.click(*local(a), count=2), lambda ev: any(e["kind"] == "double" for e in ev))
    step("right click", lambda: hands.click(*local(a), button="right"), lambda ev: any(e["kind"] == "press" and e["button"] == "RightButton" for e in ev))
    step("drag", lambda: hands.drag(*local(b), *local(c)),
         lambda ev: any(e["kind"] == "press" and near(e, b) for e in ev) and any(e["kind"] == "release" and near(e, c) for e in ev)
         and sum(e["kind"] == "drag" for e in ev) >= 3)
    step("scroll down", lambda: hands.scroll(*local(a), 2), lambda ev: any(e["kind"] == "wheel" and e["dy"] < 0 for e in ev))
    step("a key", lambda: hands.keys("a", hover=local(a)), lambda ev: any(e["kind"] == "key" and e["text"] == "a" for e in ev))
    combo = "cmd+a" if getattr(desk, "NAME", "") == "macOS" else "ctrl+a"
    step(f"a shortcut ({combo})", lambda: hands.keys(combo, hover=local(a)), lambda ev: any(e["kind"] == "key" and e["key"] == ord("A") and e["mods"] for e in ev))

    def type_it():
        hands.click(*local(probe.field_spot()))
        time.sleep(0.3)
        hands.type_text("Hello 123!")

    step("typing", type_it, lambda _ev: probe.typed() == "Hello 123!")
    typed = probe.typed()
    probe.shut()
    if any("worked" not in r for r in results):
        raise Problem("; ".join(results) + f" | the text field holds {typed!r}")
    return "; ".join(results)


def _microphone(desk, quiet: bool) -> str:
    if quiet:
        raise Skip("--no-sound")
    config.RUNTIME.mkdir(parents=True, exist_ok=True)
    recorder = Recorder(desk.sound)
    recorder.start()
    time.sleep(1.2)
    path = recorder.stop()
    if path is None:
        raise Problem("nothing was recorded: the recorder did not start, or it wrote no sound")
    with wave.open(str(path)) as wav:
        frames = wav.readframes(wav.getnframes())
        secs = wav.getnframes() / wav.getframerate()
    path.unlink()
    peak = max((abs(s[0]) for s in struct.iter_unpack("<h", frames)), default=0)
    if peak == 0:
        raise Problem(f"{secs:.1f} s recorded, all of it silence: the microphone is muted or not allowed for this app")
    return f"{secs:.1f} s recorded, loudest moment {peak / 32768:.0%} of full scale"


def _speaker(desk, quiet: bool) -> str:
    if quiet:
        raise Skip("--no-sound")
    player = desk.sound.play_pcm(24000)
    player.stdin.write(tone(24000))
    player.stdin.close()
    code = player.wait(timeout=10)
    if code != 0:
        raise Problem(f"the player exited with {code}")
    return "a short beep went to the speaker without an error. Whether it was heard, only a person can say"


def _voices() -> str:
    kokoro = all((config.KOKORO_DIR / name).exists() for name in ("kokoro-v1.0.int8.onnx", "voices-v1.0.bin"))
    missing = [name for name in ("faster_whisper", "kokoro_onnx") if importlib.util.find_spec(name) is None]
    if missing:
        raise Problem(f"not installed: {', '.join(missing)}")
    return f"speech-to-text and the voice engine are installed; Kokoro voice files {'found' if kokoro else 'MISSING, so the system voice is used'} in {config.KOKORO_DIR}"


def _provider(seen) -> str:
    seen["provider"] = p = providers.create(config.BACKEND)
    problem = p.health()
    if problem:
        raise Problem(f"{p.name}: {problem}")
    return f"{p.name} can be used (model {p.model or 'its default'}, effort {p.effort or 'none'})"


def _ask(seen, wanted: bool) -> str:
    if not wanted:
        raise Skip("add --ask to try it. That sends one screenshot of this screen to the AI provider")
    t0 = time.monotonic()
    step = Tutor(seen["provider"]).step(seen["shot"], "doctor", "This is a self-test. Say hello in one short sentence. Set point to false and action to none.", "guide")
    return f"answered in {time.monotonic() - t0:.1f} s: {step.say!r}"
