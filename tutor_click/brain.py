"""One tutoring step: screenshot + what the user said -> what to say, where to point."""
from dataclasses import dataclass

from . import config, providers
from .providers import Provider, ProviderError
from .screen import Shot

SYSTEM = """You are Tutor Click, a patient teacher who shows the user how to use desktop \
applications - Blender, Unity, Unreal Engine, GIMP, office apps, IDEs, anything on their screen - \
by watching their screen and guiding them one small step at a time.

Each turn you get a screenshot of the user's screen, its pixel size, the focused window, the mode, \
what the user asked for, the recent conversation, and what just happened.

There are two modes:
- "guide": the user performs every step. You explain and point. Still fill in "action" with what \
the step is, because the user can ask you to do that one step for them.
- "auto": the app performs your "action" right after you start speaking, then sends you a new \
screenshot. You are demonstrating: say what you are doing and why as you do it, including the \
shortcut or menu path, so the user learns to do it alone. Keep going step by step until the goal is \
reached. With each new screenshot the app tells you what it measured about your last action: \
whether the input was sent and whether the app's window changed afterwards. Sent does not mean it \
worked. Check the screenshot yourself before moving on. If the app reports no visible change, the \
action probably did not register: say so, and try a different way rather than repeating it.

How to teach:
- One step per turn. A step is a single action: one click, one shortcut, one menu item, one value to type.
- In "say", one to three short sentences. It is read aloud, so plain spoken words: no markdown, no \
lists, no symbols.
- Point at the exact UI element for this step: its center in screenshot pixel coordinates. For a \
keyboard shortcut, point where the mouse must hover for it to work (for example inside the 3D \
viewport) and put the keys in the caption.
- "caption" is at most eight words naming the action, e.g. "Press Tab - Edit Mode" or \
"File > Export > FBX".
- If the user asks a question or asks for more explanation, answer it properly with action "none", \
and continue the lesson on the next turn.
- If the needed element is not visible (wrong app focused, panel hidden), say how to bring it on \
screen and set point to false.
- If the user has not said what they want to learn yet, ask them, with point false and action "none".
- When the goal is reached, say so and set done to true.

The "action" field:
- "click", "double_click", "right_click", "middle_click": at x, y. "keys" may hold modifiers to \
hold, e.g. "shift" or "ctrl+shift", else "".
- "drag": press the left button at x, y and release at x2, y2. "keys" may hold modifiers.
- "key": press the keys in "keys", a space-separated sequence of combos, e.g. "tab", "shift+a", \
"g x 2 enter", "ctrl+z", "numpad1", "f2", "delete". With point true the mouse hovers at x, y first.
- "type": type the "text" (plain ASCII, no Enter) into the field that already has keyboard focus. \
Click the field in an earlier step. Use a "key" step with "enter" to confirm.
- "none": nothing to perform this turn.
- "scroll": turn the mouse wheel at x, y by "notches" wheel clicks, from -10 to 10. Negative is \
wheel up (scrolls a list up, zooms in on most 3D viewports and canvases); positive is wheel down. \
Holding a modifier while scrolling is not available: use a keyboard or menu alternative for that.
- Set "wait_user" to true when the next move must be the user's: you asked a question, they need to \
choose something, or the step is one you must not do. In auto mode this pauses you.
- "expect": one short sentence saying what will look different on screen right after the action, \
e.g. "The Rectangle tool button is highlighted". Empty when action is "none".
- "repeat_safe": true if doing this exact action a second time by mistake would be harmless \
(selecting a tool, opening a menu, clicking into a field, pressing Escape, scrolling). False if a \
second time would add, duplicate, toggle, submit or remove something (drawing a shape, typing text, \
pressing Enter in a dialog, clicking Add or OK, undo).
- "risk": what the action can cost the user.
  "read_only": only looks or navigates: opening a menu, switching a tab or tool, scrolling.
  "reversible": changes the document or scene in a way Undo takes back.
  "sensitive": reaches outside the document: saving, exporting or overwriting a file, changing \
settings or preferences, installing something, running a command, sending or publishing.
  "irreversible": cannot be taken back: deleting files, discarding unsaved work, spending money, \
entering a password.
- The app enforces risk. A "sensitive" action is performed only after the user presses Do it for \
me: explain what it will do, point at it, fill in "action" and set wait_user true. An \
"irreversible" action is never performed by the app: explain the step, set action "none" and \
wait_user true, and let the user do it."""

ACTIONS = ("none", "click", "double_click", "right_click", "middle_click", "drag", "scroll", "key", "type")
RISKS = ("read_only", "reversible", "sensitive", "irreversible")

SCHEMA = {
    "type": "object",
    "properties": {
        "say": {"type": "string"},
        "caption": {"type": "string"},
        "point": {"type": "boolean"},
        "x": {"type": "integer"},
        "y": {"type": "integer"},
        "action": {"type": "string", "enum": list(ACTIONS)},
        "x2": {"type": "integer"},
        "y2": {"type": "integer"},
        "keys": {"type": "string"},
        "text": {"type": "string"},
        "notches": {"type": "integer"},
        "expect": {"type": "string"},
        "repeat_safe": {"type": "boolean"},
        "risk": {"type": "string", "enum": list(RISKS)},
        "wait_user": {"type": "boolean"},
        "done": {"type": "boolean"},
    },
    "required": ["say", "caption", "point", "x", "y", "action", "x2", "y2", "keys", "text", "notches",
                 "expect", "repeat_safe", "risk", "wait_user", "done"],
    "additionalProperties": False,
}


_JSON_TYPES = {"string": str, "integer": int, "boolean": bool}


def check_answer(data) -> dict:
    """The provider's answer must have exactly the schema's fields, each of the right type."""
    props = SCHEMA["properties"]
    ok = isinstance(data, dict) and data.keys() == props.keys() and all(
        isinstance(data[k], _JSON_TYPES[spec["type"]]) and not (spec["type"] == "integer" and isinstance(data[k], bool))
        for k, spec in props.items()
    )
    if not ok:
        raise ProviderError("The answer was not in the form I expected.", "invalid_answer") from ValueError(repr(data)[:300])
    return data


@dataclass
class Step:
    say: str
    caption: str
    point: tuple[int, int] | None  # screenshot pixels
    done: bool
    action: str = "none"
    point2: tuple[int, int] | None = None  # drag end, screenshot pixels
    keys: str = ""
    text: str = ""
    notches: int = 0  # scroll: wheel clicks, negative = up
    wait_user: bool = False
    expect: str = ""  # what should look different afterwards
    repeat_safe: bool = False  # a second, accidental run of this action would be harmless
    risk: str = "reversible"  # one of RISKS

    def describe(self) -> str:
        """Short text of the action, for the transcript sent back to the model."""
        at = f" at {self.point}" if self.point else ""
        if self.action == "key":
            return f"pressed {self.keys}{at}"
        if self.action == "type":
            return f"typed {self.text!r}"
        if self.action == "scroll":
            return f"scrolled {'up' if self.notches < 0 else 'down'} {abs(self.notches)} notches{at}"
        if self.action == "drag":
            return f"dragged from {self.point} to {self.point2}"
        return f"{self.action.replace('_', ' ')}{at}" + (f" holding {self.keys}" if self.keys else "")


class Tutor:
    """The lesson's memory and the one place a provider is asked. The provider can be swapped mid-lesson."""

    def __init__(self, provider: Provider | None = None):
        self.provider = provider or providers.create(config.BACKEND)
        self.history: list[tuple[str, str]] = []  # (what happened, tutor said + caption)
        self.asks: list[str] = []  # what the user themself said, kept apart so the goal never scrolls out

    def reset(self):
        self.history.clear()
        self.asks.clear()

    def abort(self):
        """Stop a call that is in flight (the user interrupted)."""
        self.provider.abort()

    def step(self, shot: Shot, window: str, event: str, mode: str, from_user: bool = True) -> Step:
        """event: what the user said, or what the app just did for them."""
        if from_user:
            self.asks.append(event)
            del self.asks[: -config.ASKS_KEPT]
        transcript = "\n".join(f"Then: {u}\nYou: {t}" for u, t in self.history) or "(none - new lesson)"
        asks = "\n".join(f"- {a}" for a in self.asks) or "(nothing yet)"
        prompt = (
            f"Screenshot size: {shot.width}x{shot.height} px.\n"
            f"Focused window: {window}\n"
            f"Mode: {mode}\n\n"
            f"What the user has asked for, oldest first:\n{asks}\n\n"
            f"Recent conversation:\n{transcript}\n\n"
            f"Now: {event}"
        )
        data = check_answer(self.provider.ask(SYSTEM, SCHEMA, shot.jpeg, prompt))

        def inside(x, y):
            return 0 <= x < shot.width and 0 <= y < shot.height

        step = Step(
            say=data["say"],
            caption=data["caption"],
            point=(data["x"], data["y"]) if data["point"] and inside(data["x"], data["y"]) else None,
            done=data["done"],
            action=data["action"] if data["action"] in ACTIONS else "none",
            point2=(data["x2"], data["y2"]) if inside(data["x2"], data["y2"]) else None,
            keys=data["keys"].strip(),
            text=data["text"],
            notches=max(-10, min(10, data["notches"])),
            wait_user=data["wait_user"],
            expect=data["expect"].strip(),
            repeat_safe=data["repeat_safe"],
            risk=data["risk"] if data["risk"] in RISKS else "sensitive",  # unknown label: treat as needing approval
        )
        if step.action in ("click", "double_click", "right_click", "middle_click", "drag", "scroll") and step.point is None:
            step.action = "none"  # nowhere valid to click
        if (step.action == "drag" and step.point2 is None) or (step.action == "scroll" and step.notches == 0):
            step.action = "none"
        shown = f" [caption: {step.caption}]" + (f" [action: {step.describe()}]" if step.action != "none" else "")
        self.history.append((event, step.say + shown))
        del self.history[: -config.HISTORY_TURNS]
        return step
