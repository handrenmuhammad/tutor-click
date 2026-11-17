"""The app process: GTK loop + control socket + lessons.

One lesson job runs at a time. Any new input (talking, typing, a button) cancels the running job
first, so the user can always interrupt.
"""
import json
import secrets
import signal
import socketserver
import subprocess
import sys
import threading
import time
import traceback
from dataclasses import dataclass

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk

from . import config, consent, providers, screen, trace, verify
from .brain import Step, Tutor
from .hands import Hands, Refused, Window, parse_mods
from .overlay import MOVE_SECS, Overlay
from .panel import Panel
from .providers import ProviderError
from .voice import Recorder, Speaker, Transcriber

NEXT_GUIDE = "I did that. What's next?"
NEXT_AUTO = "Okay, continue."
EXPLAIN = "Stop there. Explain that last step in more detail: what it does and why. Do not move on yet."
DO_IT = "Please do this step for me."
SETTLE_SECS = 0.7  # let the app redraw after an action before the next screenshot
MAX_RECORD_SECS = 60
FRESH_SECS = 60  # "do it for me" reuses the step on screen only if it is this recent
NO_CHANGE = ("I sent that, but nothing changed on the screen, and doing it twice could do harm. "
             "Please check the app, then tell me what you see or press Next step.")
# Steps the tutor does not perform on its own, by the risk the model gave them.
HOLD = {
    "sensitive": "This step reaches outside your document, so I will only do it if you press Do it for me.",
    "irreversible": "This step cannot be undone, so I am leaving it to you.",
}

STATUS = {None: "Ready", "listening": "Listening…", "thinking": "Looking at your screen…", "speaking": "Speaking…", "acting": "Doing the step…"}
MODE_HINT = {"guide": "You do each step. I point and explain.", "auto": "I do the steps and explain. Talk or press Stop to interrupt."}
# (keys, command, on release)
BINDS = [
    ("SUPER + ALT + space", "ptt-down", False),
    ("SUPER + ALT + space", "ptt-up", True),
    ("SUPER + ALT + N", "next", False),
    ("SUPER + ALT + T", "type", False),
    ("SUPER + ALT + M", "mode toggle", False),
    ("SUPER + ALT + E", "explain", False),
    ("SUPER + ALT + Escape", "stop", False),
]


def log(*args):
    line = " ".join(str(a) for a in (time.strftime("%H:%M:%S"), *args))
    print(line, file=sys.stderr, flush=True)
    with config.LOG_FILE.open("a") as f:
        f.write(line + "\n")


class Cancelled(Exception):
    """The user interrupted this job."""


@dataclass
class Outcome:
    """What is known about an action after it was attempted.

    "verified": the input went out with the window focused and the cursor on target, and the
    app's window changed afterwards. That is evidence the app reacted, not that the step did what
    was intended; the model still judges that from the screenshot.
    "uncertain": the input went out but the window shows no change.
    "failed": the input was not sent; code says why.
    """

    status: str
    code: str = ""
    facts: str = ""  # what was measured, in words, for the model


class Daemon:
    def __init__(self):
        self.app = Gtk.Application(application_id="dev.handren.TutorClick", flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.app.connect("activate", self._activate)
        self.tutor = Tutor()
        self.recorder = Recorder()
        self.transcriber = Transcriber()
        self.speaker = Speaker()
        self.busy = threading.Lock()  # held by the running job
        self.state = threading.Lock()  # guards job_id
        self.job_id = 0
        self.mode = "guide"
        self.recording = False
        self.rec_id = 0
        self.last: tuple[Step, screen.Shot, float] | None = None  # step on screen, its screenshot, when
        self.lesson = secrets.token_hex(4)  # trace ids are "<lesson>-<step number>"
        self.step_n = 0
        self.trace_id = ""  # id of the step in self.last
        self.note = ""  # something the model must hear with the next event (an action that showed no effect)
        self.pending_backend = ""  # a provider the user picked once and has to pick again to confirm
        self.overlay: Overlay | None = None
        self.panel: Panel | None = None
        self.hands: Hands | None = None
        self.monitor: screen.Monitor | None = None

    def run(self):
        config.RUNTIME.mkdir(parents=True, exist_ok=True)
        config.LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        if config.LOG_FILE.exists() and config.LOG_FILE.stat().st_size > 1_000_000:
            config.LOG_FILE.unlink()  # bounded log
        for sig in (signal.SIGINT, signal.SIGTERM):  # Ctrl-C / kill end the GTK loop cleanly
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, lambda: (self.app.quit(), False)[1])
        try:
            return self.app.run([])
        finally:
            self.speaker.stop()
            self._binds(False)
            config.SOCKET.unlink(missing_ok=True)

    def _activate(self, app):
        self.monitor = screen.focused_monitor()
        self.overlay = Overlay(app, self.monitor.name)
        menu = [(pid, cls.menu) for pid, cls in providers.PROVIDERS.items()]
        self.panel = Panel(app, Overlay._gdk_monitor(self.monitor.name), self.monitor, self._from_panel, menu, self.tutor.provider.id)
        self.hands = Hands(self.monitor)
        app.hold()
        threading.Thread(target=self._serve, daemon=True).start()
        threading.Thread(target=self._warm, daemon=True).start()
        self._binds(True)
        log(f"app on {self.monitor.name}, provider {self.tutor.provider.id} (model {self.tutor.provider.model or 'default'}), hotkeys {'on' if config.BINDS else 'off'}")

    def _warm(self):
        """Load the speech models now so the first question is not slow."""
        t0 = time.monotonic()
        self.speaker.warm()
        self.transcriber.warm()
        log(f"voice models loaded ({time.monotonic() - t0:.1f}s)")

    def _binds(self, on: bool):
        """Register or remove the global hotkeys in the running Hyprland (not in its config file)."""
        if not config.BINDS:
            return
        cli = f"{sys.executable} -m tutor_click"
        if on:
            code = " ".join(
                f"hl.bind({json.dumps(keys)}, hl.dsp.exec_cmd({json.dumps(f'{cli} {cmd}')}){', { release = true }' if release else ''})"
                for keys, cmd, release in BINDS
            )
        else:
            code = " ".join(f"hl.unbind({json.dumps(keys)})" for keys in dict.fromkeys(k for k, _, _ in BINDS))
        r = subprocess.run(["hyprctl", "eval", code], capture_output=True, text=True, check=False)
        if r.stdout.strip() != "ok":
            print("hotkeys:", r.stdout.strip()[:300], file=sys.stderr)

    # ---- control socket and panel ------------------------------------------
    def _serve(self):
        config.SOCKET.unlink(missing_ok=True)
        daemon = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                try:
                    msg = json.loads(self.rfile.readline())
                    reply = daemon.dispatch(msg.get("cmd", ""), msg.get("text", ""))
                except Exception as e:  # noqa: BLE001 - one bad message must not kill the server
                    reply = f"error: {e}"
                self.wfile.write((reply + "\n").encode())

        with socketserver.ThreadingUnixStreamServer(str(config.SOCKET), Handler) as srv:
            srv.serve_forever()

    def _from_panel(self, cmd, text):
        try:
            reply = self.dispatch(cmd, text)
            if reply not in ("ok", "guide", "auto") and cmd != "backend":
                log(f"panel {cmd}: {reply}")
        except Exception:  # noqa: BLE001 - a panel click must not kill its thread silently
            traceback.print_exc()

    def dispatch(self, cmd: str, text: str) -> str:
        if cmd not in ("status", "point", "show"):
            log(f"> {cmd} {text}".rstrip())
        if cmd == "ptt-down":
            if not self.recording:
                self._cancel()
                self.rec_id += 1
                rec = self.rec_id
                self.recording = True
                self.recorder.start()
                self._status("listening")
                self.panel.set_recording(True)
                # Bounded: a forgotten mic sends itself after MAX_RECORD_SECS.
                timer = threading.Timer(MAX_RECORD_SECS, lambda: self.recording and self.rec_id == rec and self.dispatch("ptt-up", ""))
                timer.daemon = True
                timer.start()
        elif cmd == "ptt-up":
            if not self.recording:
                return "not recording"
            self.recording = False
            wav = self.recorder.stop()
            self.panel.set_recording(False)
            if wav is None:
                self._status(None)
                return "no audio"
            return self._job(self._voice, wav)
        elif cmd == "ask":
            if not text.strip():
                return "nothing to ask"
            self.panel.set_heard(f"You: {text.strip()}")
            return self._job(self._lesson, text.strip())
        elif cmd == "next":
            return self._job(self._lesson, NEXT_AUTO if self.mode == "auto" else NEXT_GUIDE)
        elif cmd == "explain":
            return self._job(self._lesson, EXPLAIN, True, "never")
        elif cmd == "do":
            return self._job(self._do)
        elif cmd == "mode":
            want = text.strip() or "toggle"
            if want == "toggle":
                want = "guide" if self.mode == "auto" else "auto"
            if want not in ("guide", "auto"):
                return "mode must be guide, auto or toggle"
            self.mode = want
            self.panel.set_mode(want)
            self.panel.set_heard(MODE_HINT[want])
            log("mode:", want)
            return want
        elif cmd == "backend":
            return self._switch(text)
        elif cmd == "stop":
            self._stop_everything()
            self.overlay.clear()
        elif cmd == "reset":
            self._stop_everything()
            self.tutor.reset()
            self.hands.reset()
            self.last = None
            self.lesson, self.step_n = secrets.token_hex(4), 0
            self.note = ""
            self.overlay.clear()
            self.panel.set_can_do(False)
            self.panel.set_heard("Tell me what you want to learn.")
        elif cmd == "show":
            self.panel.show()
        elif cmd == "status":
            return json.dumps({"mode": self.mode, "busy": self.busy.locked(), "recording": self.recording, "acting_in": self.hands.target,
                               "backend": self.tutor.provider.id})
        elif cmd == "point":  # debug: "x y caption" in logical px
            x, y, *caption = text.split(" ", 2)
            self.overlay.show_step((float(x), float(y)), " ".join(caption), "")
        elif cmd == "quit":
            self._stop_everything()
            GLib.idle_add(self.app.quit)  # GTK calls must run on the main loop, not this thread
        else:
            return f"unknown command: {cmd}"
        return "ok"

    # ---- jobs --------------------------------------------------------------
    def _cancel(self) -> int:
        """Interrupt the running job (if any). Returns the id for the next one."""
        with self.state:
            self.job_id += 1
            my = self.job_id
        self.tutor.abort()
        self.speaker.stop()
        return my

    def _stop_everything(self):
        self._cancel()
        if self.recording:
            self.recording = False
            self.recorder.stop()
            self.panel.set_recording(False)
        self._status(None)

    def _live(self, my: int) -> bool:
        return my == self.job_id

    def _check(self, my: int):
        if not self._live(my):
            raise Cancelled

    def _sleep(self, my: int, secs: float):
        end = time.monotonic() + secs
        while time.monotonic() < end:
            self._check(my)
            time.sleep(0.05)
        self._check(my)

    def _job(self, fn, *args) -> str:
        my = self._cancel()

        def run():
            with self.busy:  # the interrupted job unwinds first
                if not self._live(my):
                    return
                try:
                    fn(my, *args)
                except Cancelled:
                    pass
                except ProviderError as e:
                    if self._live(my):  # an aborted call also lands here; stay quiet then
                        log(f"provider error ({self.tutor.provider.id}, {e.code}):", e.__cause__ or e)
                        trace.write({"ev": "provider_error", "backend": self.tutor.provider.id, "code": e.code})
                        self._fail(my, self._offer(e))
                except Exception as e:  # noqa: BLE001 - report any failure to the user, keep running
                    traceback.print_exc()
                    if self._live(my):
                        self._fail(my, f"Something went wrong: {e}")
                finally:
                    self.panel.set_keyboard(True)
                    if self._live(my):
                        self._status(None)

        threading.Thread(target=run, daemon=True).start()
        return "ok"

    def _status(self, key):
        self.overlay.set_status(key)
        self.panel.set_status(STATUS[key])

    def _speak(self, my: int, text: str):
        self._check(my)
        self._status("speaking")
        self.speaker.say(text)
        self._check(my)

    def _fail(self, my: int, message: str):
        self.overlay.show_text(message)
        try:
            self._speak(my, message)
        except Cancelled:
            pass

    # ---- providers -------------------------------------------------------------
    def _switch(self, text: str) -> str:
        """Change the AI provider. Only the user's own command or panel choice gets here: nothing switches by itself."""
        want, _, word = text.strip().partition(" ")
        current = self.tutor.provider
        if not want:
            return json.dumps({"backend": current.id, "available": {pid: providers.create(pid).health() or "ok" for pid in providers.PROVIDERS}})
        if want == current.id:
            return f"Already using {current.name}."
        try:
            new = providers.create(want)
        except ProviderError as e:
            return str(e)
        problem = new.health()
        if problem:
            return self._keep(f"I can't switch to {new.name}: {problem}.")
        leaves = new.billing != "local"  # a local model sends nothing off this computer, so there is nothing to agree to
        if leaves and want not in consent.allowed() and word != "yes" and self.pending_backend != want:
            self.pending_backend = want
            return self._keep(f"{new.name} has not been used here before. Screenshots of your screen would go to {new.sends_to}, "
                              f"paid by {new.paid_by}. Choose it again to confirm.")
        self.pending_backend = ""
        self._stop_everything()  # also aborts a call in flight on the old provider
        if leaves:
            consent.allow(want)
        self.tutor.provider = new
        self.panel.set_backend(want)
        log("backend:", want)
        return self._keep(f"Now using {new.name}, paid by {new.paid_by}. The lesson carries on from where it was.")

    def _keep(self, message: str) -> str:
        self.panel.set_backend(self.tutor.provider.id)  # the menu shows what is in use, whatever was clicked
        self.panel.set_heard(message)
        return message

    def _offer(self, e: ProviderError) -> str:
        """A provider failure in words, with what the user can choose. It never chooses for them."""
        now = self.tutor.provider
        said = {
            "rate_limited": f"{now.name} has reached its usage limit for now.",
            "needs_login": f"{now.name} is not signed in.",
            "offline": f"I can't reach {now.name}.",
        }.get(e.code, str(e))
        if e.code in ("declined", "unknown"):
            return said
        others = [p for pid in providers.PROVIDERS if pid != now.id and (p := providers.create(pid)).health() is None]
        if not others:
            return f"{said} Please try again in a moment."
        choices = " or ".join(f"{p.name}, paid by {p.paid_by.split(',')[0]}" for p in others)
        return f"{said} You can try again, or pick {choices} in the provider menu. I will not switch by myself."

    # ---- lessons -------------------------------------------------------------
    def _voice(self, my: int, wav):
        self._status("thinking")
        t0 = time.monotonic()
        text = self.transcriber.transcribe(wav)
        log(f"heard ({time.monotonic() - t0:.1f}s): {text!r}")
        self._check(my)
        if not text:
            self.panel.set_heard("I didn't catch that. Try again, a little closer to the mic.")
            return
        self.panel.set_heard(f"You: {text}")
        self._lesson(my, text)

    def _capture(self) -> screen.Shot:
        """Screenshot without our own overlay and panel in it."""
        self.overlay.set_hidden(True)
        self.panel.set_hidden(True)
        time.sleep(0.12)  # let the compositor drop our frame before grim captures
        try:
            return screen.capture(self.monitor, config.MAX_EDGE)
        finally:
            self.overlay.set_hidden(False)
            self.panel.set_hidden(False)

    def _look(self, my: int, event: str, from_user: bool, mode: str, shot: screen.Shot | None = None) -> tuple[Step, screen.Shot]:
        """Screenshot (unless one was just taken), ask the tutor, show the step. Does not speak."""
        self._status("thinking")
        shot = shot or self._capture()
        window = screen.active_window()
        self._check(my)

        t0 = time.monotonic()
        step = self.tutor.step(shot, window, event, mode, from_user)
        self._check(my)
        secs = time.monotonic() - t0
        log(f"step ({secs:.1f}s, {mode}, {shot.width}x{shot.height}, {len(shot.jpeg) // 1024} KiB): {step}")

        self.last = (step, shot, time.monotonic())
        point = None
        if step.point is not None:
            point = (step.point[0] / shot.factor, step.point[1] / shot.factor)
            self.panel.avoid(*point)
        self.step_n += 1
        self.trace_id = f"{self.lesson}-{self.step_n}"
        m = self.monitor
        trace.write({
            "ev": "step", "id": self.trace_id, "mode": mode, "from_user": from_user,
            "backend": self.tutor.provider.id, "model": self.tutor.provider.model, "model_secs": round(secs, 2),
            "monitor": {"name": m.name, "x": m.x, "y": m.y, "w": m.logical_w, "h": m.logical_h, "scale": m.scale},
            "shot": {"w": shot.width, "h": shot.height, "factor": round(shot.factor, 4), "bytes": len(shot.jpeg)},
            "window": window.split(" - ")[0],  # class only: titles carry file names
            "action": step.action, "point": step.point, "point2": step.point2, "keys": step.keys,
            "text_len": len(step.text), "notches": step.notches, "wait_user": step.wait_user, "done": step.done,
            "risk": step.risk, "repeat_safe": step.repeat_safe, "expect": step.expect,
            "logical": point and [round(point[0], 1), round(point[1], 1)],
            "under_point": point and self._class_at(*point),
        })
        self.overlay.show_step(point, step.caption, step.say)
        self.panel.set_can_do(step.action != "none" and not step.done)
        return step, shot

    def _class_at(self, x: float, y: float) -> str | None:
        """Class of the window under a logical point on our monitor, for the trace."""
        try:
            win = self.hands.window_at(self.monitor.x + x, self.monitor.y + y)
        except (subprocess.CalledProcessError, OSError, ValueError):  # hyprctl gone or bad JSON
            return None
        return win.cls if win else None

    def _lesson(self, my: int, event: str, from_user: bool = True, acting: str = "mode", shot: screen.Shot | None = None):
        """acting: "mode" follows the current mode, "never" only explains, "once" performs the first step.

        shot: a screenshot taken a moment ago (after an action), used for the first look.
        """
        if self.note:
            event, self.note = f"{event}\n(From the app, about your last action: {self.note})", ""
        last_sig, repeats = None, 0
        for n in range(config.AUTO_MAX_STEPS):
            force = acting == "once" and n == 0
            step, shot = self._look(my, event, from_user, "auto" if force else self.mode, shot)
            act = acting != "never" and (force or self.mode == "auto")
            if not (act and step.action != "none" and not step.done and not step.wait_user):
                self._speak(my, step.say)
                return
            if step.risk in HOLD:  # the model's wait_user should have caught this; do not rely on it
                log(f"held ({step.risk}): {step.describe()}")
                trace.write({"ev": "action", "id": self.trace_id, "outcome": "held", "code": step.risk.upper()})
                self._speak(my, step.say)
                self.panel.set_heard(HOLD[step.risk])
                self._speak(my, HOLD[step.risk])
                return
            sig = (step.action, step.point, step.point2, step.keys, step.text, step.notches)
            repeats = repeats + 1 if sig == last_sig else 0
            last_sig = sig
            if repeats >= 2:
                self._fail(my, "I tried that same step three times and it is not working. Please try it yourself, or tell me what you see.")
                return
            outcome, shot = self._perform(my, step, shot)
            event = self._report(my, step, outcome)
            if event is None:
                return
            from_user = False
        self._fail(my, f"I have done {config.AUTO_MAX_STEPS} steps in a row, so I am pausing here. Say continue when you are ready.")

    def _do(self, my: int):
        """Guide mode: perform the one step that is on screen, then carry on.

        Pressing "Do it for me" on a step the user can see is the approval a sensitive step needs.
        """
        if self.last is None or self.last[0].action == "none" or time.monotonic() - self.last[2] > FRESH_SECS:
            self._lesson(my, DO_IT, True, "once")  # nothing recent to reuse: look again
            return
        step, shot, _ = self.last
        if step.risk == "irreversible":
            trace.write({"ev": "action", "id": self.trace_id, "outcome": "held", "code": "IRREVERSIBLE"})
            self._fail(my, HOLD["irreversible"])
            return
        outcome, shot = self._perform(my, step, shot, narrate=False)
        event = self._report(my, step, outcome, "At the user's request. ")
        if event is not None:
            self._lesson(my, event, False, shot=shot)

    def _report(self, my: int, step: Step, outcome: Outcome, lead: str = "") -> str | None:
        """What to tell the model after an action, or None if the lesson stops here."""
        if outcome.status == "failed":
            return None
        if outcome.status == "uncertain" and not step.repeat_safe:
            self.note = outcome.facts  # the model hears this with whatever the user says next
            self._fail(my, NO_CHANGE)
            return None
        expected = f' You expected: "{step.expect}"' if step.expect else ""
        return f"(automatic) {lead}{outcome.facts}{expected} This is the screen now. Judge from it whether the step worked, then continue."

    def _perform(self, my: int, step: Step, shot: screen.Shot, narrate: bool = True) -> tuple[Outcome, screen.Shot | None]:
        """Speak the step while performing its action, then look at what it did.

        Returns the outcome and the screenshot taken afterwards (None if nothing was sent).
        """
        talk = threading.Thread(target=self._narrate, args=(my, step.say if narrate else ""), daemon=True)
        talk.start()
        before = []  # the screen at the last moment before the input goes out
        try:
            self._sleep(my, MOVE_SECS + 0.2 if narrate else 0.1)  # the pointer arrives first, so the user sees where
            self.panel.set_keyboard(False)  # give keyboard focus back to the app
            time.sleep(0.06)
            t0 = time.monotonic()
            win = self._act(step, shot, lambda: before.append(self._capture()))
            trace.write({"ev": "action", "id": self.trace_id, "outcome": "sent", "where": win.cls, "secs": round(time.monotonic() - t0, 3)})
        except Refused as e:
            log("refused:", e, "|", e.__cause__ or "")
            trace.write({"ev": "action", "id": self.trace_id, "outcome": "refused", "code": e.code, "reason": str(e), "detail": str(e.__cause__ or "")[:200]})
            talk.join()
            self._fail(my, str(e))
            return Outcome("failed", e.code), None
        talk.join()
        self._check(my)
        self._status("thinking")
        self._sleep(my, SETTLE_SECS)
        after = self._capture()
        change = verify.compare(before[0] if before else shot, after, verify.window_rect(win, self.monitor, after))
        aimed = " The cursor was on target when it was sent." if step.point and step.action != "type" else ""
        if change.changed:
            outcome = Outcome("verified", "", f"Input sent: {step.describe()}.{aimed} Measured: the {win.cls} window changed afterwards ({change.fraction:.1%} of it).")
        else:
            outcome = Outcome("uncertain", "NO_VISIBLE_CHANGE", f"Input sent: {step.describe()}.{aimed} Measured: the {win.cls} window shows no visible change afterwards, so the action probably did not register.")
        log(f"outcome: {outcome.status} {outcome.code} ({change.pixels} px changed, {change.fraction:.2%} of the window)")
        trace.write({"ev": "outcome", "id": self.trace_id, "status": outcome.status, "code": outcome.code,
                     "changed_px": change.pixels, "changed_frac": round(change.fraction, 5)})
        return outcome, after

    def _narrate(self, my: int, text: str):
        if text and self._live(my):
            self._status("speaking")
            self.speaker.say(text)

    def _act(self, step: Step, shot: screen.Shot, armed=None) -> Window:
        """armed is called once the input is about to go out (see Hands._aim)."""
        f = shot.factor
        pt = (step.point[0] / f, step.point[1] / f) if step.point else None
        a = step.action
        if a in ("click", "double_click", "right_click", "middle_click"):
            button = {"right_click": "right", "middle_click": "middle"}.get(a, "left")
            win = self.hands.click(*pt, button=button, count=2 if a == "double_click" else 1, mods=parse_mods(step.keys), armed=armed)
        elif a == "drag":
            win = self.hands.drag(*pt, step.point2[0] / f, step.point2[1] / f, mods=parse_mods(step.keys), armed=armed)
        elif a == "scroll":
            win = self.hands.scroll(*pt, step.notches, armed=armed)
        elif a == "key":
            win = self.hands.keys(step.keys, hover=pt, armed=armed)
        elif a == "type":
            win = self.hands.type_text(step.text, armed=armed)
        else:
            raise Refused(f"I don't know how to do {a}.")
        log(f"acted in {win.cls}: {step.describe()}")
        return win
