"""The app process: GTK loop + control socket + lessons.

One lesson job runs at a time. Any new input (talking, typing, a button) cancels the running job
first, so the user can always interrupt.
"""
import json
import socketserver
import subprocess
import sys
import threading
import time
import traceback
from dataclasses import dataclass

from . import config, consent, lessons, platform, providers, settings, trace, verify
from .brain import Step, Tutor
from .platform.base import Monitor, Refused, Shot, Window
from .providers import ProviderError
from .voice import Recorder, Speaker, Transcriber

NEXT_GUIDE = "I did that. What's next?"
NEXT_AUTO = "Okay, continue."
EXPLAIN = "Stop there. Explain that last step in more detail: what it does and why. Do not move on yet."
DO_IT = "Please do this step for me."
# Guide mode, Next step: the user says they did the step. The model checks that against what it expected.
CHECK = ('(From the app: your last step expected: "{expect}"{measured} Compare the screenshot with that. If it does not '
         "show it, do not move on: say what you see instead and give a hint for the same step.)")
MEASURED = {
    True: " Measured: the window it points at has changed since you gave it, which does not show the step was done right.",
    False: " Measured: the window it points at looks exactly as it did when you gave it, so the step has probably not been done.",
    None: "",  # nothing to measure: the step pointed at no window
}
RESUME = ("(From the app: Tutor Click was closed and has been started again. The lesson carries on, but time has passed and "
          'the screen may have changed. It is not known whether the user did your last step, "{caption}". Look at the '
          "screenshot: if that step is still needed, give it again. If it is already done, give the next one.)")
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
    ("super+alt+space", "ptt-down", False),
    ("super+alt+space", "ptt-up", True),
    ("super+alt+n", "next", False),
    ("super+alt+t", "type", False),
    ("super+alt+m", "mode toggle", False),
    ("super+alt+e", "explain", False),
    ("super+alt+escape", "stop", False),
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
    def __init__(self, desk=None):
        self.desk = desk or platform.load()  # the desktop backend: screen, input, overlay, panel, hotkeys, sound
        self.app = None
        self.tutor = Tutor()
        self.tutor.desktop = getattr(self.desk, "NAME", "")
        self.recorder = Recorder(self.desk.sound)
        self.transcriber = Transcriber()
        self.speaker = Speaker(self.desk.sound)
        self.busy = threading.Lock()  # held by the running job
        self.state = threading.Lock()  # guards job_id
        self.job_id = 0
        self.mode = "guide"
        self.recording = False
        self.rec_id = 0
        self.last: tuple[Step, Shot, float] | None = None  # step on screen, its screenshot, when
        self.record = lessons.Lesson()  # this lesson, as saved to disk after every step
        self.resumable: lessons.Lesson | None = None  # the lesson left unfinished when the app last stopped
        self.trace_id = ""  # id of the step in self.last: "<lesson id>-<step number>"
        self.note = ""  # something the model must hear with the next event (an action that showed no effect)
        self.pending_backend = ""  # a provider the user picked once and has to pick again to confirm
        self.overlay = None
        self.panel = None
        self.hands = None
        self.monitor: Monitor | None = None

    def run(self):
        config.RUNTIME.mkdir(parents=True, exist_ok=True)
        config.LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        if config.LOG_FILE.exists() and config.LOG_FILE.stat().st_size > 1_000_000:
            config.LOG_FILE.unlink()  # bounded log
        self.app = self.desk.App(self._activate)
        try:
            return self.app.run()
        finally:
            self.speaker.stop()
            self._binds(False)
            config.SOCKET.unlink(missing_ok=True)

    def _activate(self, app):
        self.monitor = self.desk.focused_monitor()
        menu = [(pid, cls.menu) for pid, cls in providers.PROVIDERS.items()]
        self.overlay, self.panel = self.desk.make_ui(app, self.monitor, self._from_panel, menu, self.tutor.provider.id)
        self.hands = self.desk.Hands(self.monitor)
        self._show_choices()
        self._find_unfinished()
        threading.Thread(target=self._serve, daemon=True).start()
        threading.Thread(target=self._warm, daemon=True).start()
        self._binds(True)
        p = self.tutor.provider
        log(f"app on {self.monitor.name}, provider {p.id} (model {p.model or 'default'}, effort {p.effort or 'none'}), hotkeys {'on' if config.BINDS else 'off'}")

    def _warm(self):
        """Load the speech models now so the first question is not slow."""
        t0 = time.monotonic()
        self.speaker.warm()
        self.transcriber.warm()
        log(f"voice models loaded ({time.monotonic() - t0:.1f}s)")

    def _binds(self, on: bool):
        """Global hotkeys, registered while the app runs and removed when it quits."""
        if config.BINDS:
            self.desk.binds(on, BINDS, f"{sys.executable} -m tutor_click")

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
            return self._job(self._ask, text.strip())
        elif cmd == "next":
            return self._job(self._next)
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
        elif cmd in ("model", "effort"):
            return self._choose(cmd, text.strip())
        elif cmd == "stop":
            self._stop_everything()
            self.overlay.clear()
        elif cmd == "reset":
            self._stop_everything()
            self.tutor.reset()
            self.hands.reset()
            self.last = None
            for old in (self.resumable, self.record):
                if old:
                    old.close()
            self.record, self.resumable = lessons.Lesson(), None
            self.note = ""
            self.overlay.clear()
            self.panel.set_can_do(False)
            self.panel.set_heard("Tell me what you want to learn.")
        elif cmd == "show":
            self.panel.show()
        elif cmd == "status":
            return json.dumps({"mode": self.mode, "busy": self.busy.locked(), "recording": self.recording, "acting_in": self.hands.target,
                               "backend": self.tutor.provider.id, "model": self.tutor.provider.model, "effort": self.tutor.provider.effort,
                               "lesson": self.record.id, "step": self.record.step_n,
                               "unfinished": self.resumable.id if self.resumable else None})
        elif cmd == "point":  # debug: "x y caption" in logical px
            x, y, *caption = text.split(" ", 2)
            self.overlay.show_step((float(x), float(y)), " ".join(caption), "")
        elif cmd == "quit":
            self._stop_everything()
            self.app.quit()
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
        self._show_choices()  # the menus show what is in use, whatever was clicked
        self.panel.set_backend(self.tutor.provider.id)
        self.panel.set_heard(message)
        return message

    def _models(self, p) -> list[tuple[str, str]]:
        """(id, name) of the models to offer for a provider: its list, plus the one in use if that came from the environment."""
        models = list(p.models)
        if p.model not in [m for m, _ in models]:
            models.append((p.model, p.model or "its own default"))
        return models

    def _show_choices(self):
        p = self.tutor.provider
        self.panel.set_choices(self._models(p), p.model, list(p.efforts), p.effort)

    def _choose(self, key: str, want: str) -> str:
        """Pick the model or the effort of the provider in use. Only the user's own command or panel choice gets here.

        It applies from the next step and is remembered for that provider.
        """
        p = self.tutor.provider
        models = dict(self._models(p))
        offered = list(models) if key == "model" else list(p.efforts)
        if not want:
            return json.dumps({"backend": p.id, key: getattr(p, key), "offered": offered})
        if want not in offered:
            if key == "effort":
                return self._keep(f"{p.name} takes {', '.join(offered)}." if offered else f"{p.name} has no effort setting.")
            return self._keep(f"{p.name} offers {', '.join(offered)}." if p.models else f"{p.name} takes its model from {p.model_env}, set before the app starts.")
        if want != getattr(p, key):
            setattr(p, key, want)
            try:
                settings.save(p.id, key, want)
            except OSError as e:  # it still applies until the app closes
                log("settings not saved:", e)
            log(f"{key}:", want)
        effort = f" at {p.effort} effort" if p.effort else ""
        return self._keep(f"{p.name} answers with {models.get(p.model, p.model)}{effort} from the next step.")

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
        self._ask(my, text)

    def _ask(self, my: int, text: str):
        """The user's own words, typed or spoken."""
        if not self.record.goal:
            self.record.goal = text
        self._lesson(my, text)

    def _next(self, my: int):
        """Next step. After a restart it takes up the unfinished lesson. In guide mode it has the step the user says they did checked."""
        if self.resumable:
            self._resume(my)
            return
        step = self.last[0] if self.last else None
        if self.mode == "auto" or step is None or step.action == "none" or not step.expect:
            self._lesson(my, NEXT_AUTO if self.mode == "auto" else NEXT_GUIDE, False)
            return
        shot = self._capture()
        changed = self._changed(step, self.last[1], shot)
        self._outcome("reported", "NO_VISIBLE_CHANGE" if changed is False else "")
        self._lesson(my, f"{NEXT_GUIDE}\n{CHECK.format(expect=step.expect, measured=MEASURED[changed])}", False, shot=shot)

    def _changed(self, step: Step, before: Shot, after: Shot) -> bool | None:
        """Whether the window under the step's point differs between two screenshots. None if there is no such window."""
        if step.point is None:
            return None
        try:
            win = self.hands.window_at(self.monitor.x + step.point[0] / before.factor, self.monitor.y + step.point[1] / before.factor)
        except (subprocess.CalledProcessError, OSError, ValueError):  # the window list could not be read
            return None
        return win and verify.compare(before, after, verify.window_rect(win, self.monitor, after)).changed

    # ---- lesson record -----------------------------------------------------
    def _find_unfinished(self):
        """At start: offer the lesson that was open when the app last stopped. Nothing is loaded until Next step."""
        self.resumable = r = lessons.resumable()
        if r:
            goal = r.goal or r.steps[-1]["caption"]
            goal = goal if len(goal) <= 45 else goal[:44].rstrip() + "…"  # the panel shows two lines
            self.panel.set_heard(f"Unfinished lesson: “{goal}”, step {r.step_n}. Press Next step to carry on, or ask for something new.")

    def _resume(self, my: int):
        self.record, self.resumable = self.resumable, None
        self.tutor.restore(self.record.memory)
        self.hands.target = self.record.app or None  # the one-app-per-lesson guard carries over
        log(f"resumed lesson {self.record.id} after step {self.record.step_n}")
        trace.write({"ev": "resume", "lesson": self.record.id, "after_step": self.record.step_n})
        self._lesson(my, RESUME.format(caption=self.record.steps[-1]["caption"]), False)

    def _save(self):
        self.record.memory = self.tutor.memory()
        self.record.app = self.hands.target or ""
        self.record.save()

    def _outcome(self, status: str, code: str = ""):
        self.record.outcome(status, code)
        self._save()

    def _capture(self) -> Shot:
        """Screenshot without our own overlay and panel in it."""
        self.overlay.set_hidden(True)
        self.panel.set_hidden(True)
        time.sleep(0.12)  # let the compositor drop our frame before the capture
        try:
            return self.desk.capture(self.monitor, config.MAX_EDGE)
        finally:
            self.overlay.set_hidden(False)
            self.panel.set_hidden(False)

    def _look(self, my: int, event: str, from_user: bool, mode: str, shot: Shot | None = None) -> tuple[Step, Shot]:
        """Screenshot (unless one was just taken), ask the tutor, show the step. Does not speak."""
        self._status("thinking")
        shot = shot or self._capture()
        window = self.desk.active_window()
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
        self.record.add_step(step, mode)
        self._save()
        self.trace_id = f"{self.record.id}-{self.record.step_n}"
        m = self.monitor
        trace.write({
            "ev": "step", "id": self.trace_id, "mode": mode, "from_user": from_user,
            "backend": self.tutor.provider.id, "model": self.tutor.provider.model, "effort": self.tutor.provider.effort,
            "model_secs": round(secs, 2),
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
        except (subprocess.CalledProcessError, OSError, ValueError):  # the window list could not be read
            return None
        return win.cls if win else None

    def _lesson(self, my: int, event: str, from_user: bool = True, acting: str = "mode", shot: Shot | None = None):
        """acting: "mode" follows the current mode, "never" only explains, "once" performs the first step.

        shot: a screenshot taken a moment ago (after an action), used for the first look.
        """
        self.resumable = None  # anything but Next step leaves the unfinished lesson behind
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
                self._outcome("held", step.risk.upper())
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
            self._outcome("held", "IRREVERSIBLE")
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

    def _perform(self, my: int, step: Step, shot: Shot, narrate: bool = True) -> tuple[Outcome, Shot | None]:
        """Speak the step while performing its action, then look at what it did.

        Returns the outcome and the screenshot taken afterwards (None if nothing was sent).
        """
        talk = threading.Thread(target=self._narrate, args=(my, step.say if narrate else ""), daemon=True)
        talk.start()
        before = []  # the screen at the last moment before the input goes out
        try:
            self._sleep(my, self.desk.MOVE_SECS + 0.2 if narrate else 0.1)  # the pointer arrives first, so the user sees where
            self.panel.set_keyboard(False)  # give keyboard focus back to the app
            time.sleep(0.06)
            t0 = time.monotonic()
            win = self._act(step, shot, lambda: before.append(self._capture()))
            trace.write({"ev": "action", "id": self.trace_id, "outcome": "sent", "where": win.cls, "secs": round(time.monotonic() - t0, 3)})
        except Refused as e:
            log("refused:", e, "|", e.__cause__ or "")
            trace.write({"ev": "action", "id": self.trace_id, "outcome": "refused", "code": e.code, "reason": str(e), "detail": str(e.__cause__ or "")[:200]})
            self._outcome("failed", e.code)
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
        self._outcome(outcome.status, outcome.code)
        return outcome, after

    def _narrate(self, my: int, text: str):
        if text and self._live(my):
            self._status("speaking")
            self.speaker.say(text)

    def _act(self, step: Step, shot: Shot, armed=None) -> Window:
        """armed is called once the input is about to go out, just before the press."""
        f = shot.factor
        pt = (step.point[0] / f, step.point[1] / f) if step.point else None
        a = step.action
        if a in ("click", "double_click", "right_click", "middle_click"):
            button = {"right_click": "right", "middle_click": "middle"}.get(a, "left")
            win = self.hands.click(*pt, button=button, count=2 if a == "double_click" else 1, mods=step.keys, armed=armed)
        elif a == "drag":
            win = self.hands.drag(*pt, step.point2[0] / f, step.point2[1] / f, mods=step.keys, armed=armed)
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
