"""Lesson records on disk, so a lesson survives a restart.

One JSON file per lesson in config.LESSONS_DIR. It is rewritten whole after every step and swapped
into place, so a kill at any moment leaves the last complete record. Bounded: the newest
LESSON_MAX_STEPS steps per lesson and the newest LESSONS_KEPT lessons.
"""
import json
import os
import secrets
import sys
import time
from dataclasses import asdict, dataclass, field

from . import config
from .brain import Step


@dataclass
class Lesson:
    id: str = field(default_factory=lambda: secrets.token_hex(4))  # trace ids are "<id>-<step_n>"
    started: float = field(default_factory=time.time)
    updated: float = 0.0
    goal: str = ""  # the first thing the user asked for, in their words
    app: str = ""  # window class the tutor may act in; "" until it has acted
    step_n: int = 0  # steps given so far, including any that dropped off `steps`
    closed: bool = False  # the user pressed New lesson
    done: bool = False  # the latest step said the goal was reached
    memory: dict = field(default_factory=dict)  # what the tutor replays to the model (Tutor.memory)
    steps: list[dict] = field(default_factory=list)

    def add_step(self, step: Step, mode: str):
        self.step_n += 1
        self.done = step.done
        self.steps.append({
            "n": self.step_n, "ts": round(time.time(), 3), "mode": mode, "caption": step.caption, "say": step.say,
            "action": step.describe() if step.action != "none" else "", "expect": step.expect, "risk": step.risk,
            "concept": step.concept, "outcome": "", "code": "",
        })
        del self.steps[: -config.LESSON_MAX_STEPS]

    def outcome(self, status: str, code: str = ""):
        """What became of the latest step: verified, uncertain, failed, held, or reported (the user said they did it)."""
        if self.steps:
            self.steps[-1].update(outcome=status, code=code)

    def save(self):
        self.updated = round(time.time(), 3)
        path = config.LESSONS_DIR / f"{self.id}.json"
        try:
            if not path.exists():
                config.LESSONS_DIR.mkdir(parents=True, exist_ok=True)
                for old in _newest_last()[: -(config.LESSONS_KEPT - 1)]:
                    old.unlink(missing_ok=True)
            tmp = path.with_suffix(".tmp")
            with tmp.open("w") as f:
                json.dump(asdict(self), f)
                f.flush()
                os.fsync(f.fileno())  # the rename below must not outrun the data on a hard reset
            os.replace(tmp, path)
        except OSError as e:  # a full disk must not stop a lesson
            print("lesson record:", e, file=sys.stderr)

    def close(self):
        """The user is finished with this lesson; it will not be offered again."""
        if self.step_n:  # a lesson with no steps was never written
            self.closed = True
            self.save()


def _newest_last() -> list:
    return sorted(config.LESSONS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime)


def resumable() -> Lesson | None:
    """The most recent lesson, if it was left unfinished. Older ones are never dug up."""
    try:
        lesson = Lesson(**json.loads(_newest_last()[-1].read_text()))
    except (OSError, IndexError, ValueError, TypeError):  # no records, or one this version cannot read
        return None
    return lesson if lesson.steps and not (lesson.closed or lesson.done) else None
