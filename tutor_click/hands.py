"""The tutor's hands: mouse and keyboard input through Hyprland's own dispatchers.

Nothing here takes free text into Lua: key names come from fixed tables, numbers are ints,
window addresses are checked against a hex pattern.
"""
import json
import re
import subprocess
import time
from dataclasses import dataclass

from . import config, wheel

BUTTONS = {"left": 272, "right": 273, "middle": 274}
MODS = {"ctrl": "CTRL", "control": "CTRL", "shift": "SHIFT", "alt": "ALT", "super": "SUPER", "win": "SUPER", "meta": "SUPER"}
NAMED = {
    "enter": "Return", "return": "Return", "esc": "Escape", "escape": "Escape", "tab": "Tab",
    "space": "space", "delete": "Delete", "del": "Delete", "backspace": "BackSpace",
    "up": "Up", "down": "Down", "left": "Left", "right": "Right", "home": "Home", "end": "End",
    "pageup": "Page_Up", "pagedown": "Page_Down", "insert": "Insert",
    # Numpad by keycode, so it works with NumLock off (Blender's view keys).
    "numpad0": "code:90", "numpad1": "code:87", "numpad2": "code:88", "numpad3": "code:89",
    "numpad4": "code:83", "numpad5": "code:84", "numpad6": "code:85", "numpad7": "code:79",
    "numpad8": "code:80", "numpad9": "code:81", "numpad.": "code:91", "numpad+": "code:86",
    "numpad-": "code:82", "numpad*": "code:63", "numpad/": "code:106", "numpadenter": "code:104",
}
NAMED.update({f"f{i}": f"F{i}" for i in range(1, 13)})
# US layout: character -> (keysym name, needs shift)
SYMBOLS = {
    " ": ("space", False), "`": ("grave", False), "-": ("minus", False), "=": ("equal", False),
    "[": ("bracketleft", False), "]": ("bracketright", False), "\\": ("backslash", False),
    ";": ("semicolon", False), "'": ("apostrophe", False), ",": ("comma", False),
    ".": ("period", False), "/": ("slash", False),
}
for _shifted, _base in zip('~!@#$%^&*()_+{}|:"<>?', r"`1234567890-=[]\;',./", strict=True):
    SYMBOLS[_shifted] = (SYMBOLS[_base][0] if _base in SYMBOLS else _base, True)

MAX_TYPE_CHARS = 200
MAX_KEYS = 12
CURSOR_TOL = 2  # px the cursor may be off its target when the press is about to go out
_ADDR = re.compile(r"^0x[0-9a-f]+$")
_KEY = re.compile(r"^(code:\d{1,3}|mouse:\d{3}|[A-Za-z0-9_]{1,24})$")


class Refused(Exception):
    """The action was not performed. The message is spoken to the user; code names the reason."""

    def __init__(self, message: str, code: str = "REFUSED"):
        super().__init__(message)
        self.code = code


@dataclass
class Window:
    address: str
    cls: str
    title: str
    at: tuple[int, int] = (0, 0)  # global logical px
    size: tuple[int, int] = (0, 0)


def _dispatch(*calls: str):
    """Run dispatcher calls in order inside one Lua eval."""
    code = " ".join(f"hl.dispatch(hl.dsp.{c})" for c in calls)
    out = subprocess.run(["hyprctl", "eval", code], capture_output=True, text=True, check=False).stdout.strip()
    if out != "ok":
        raise Refused("Hyprland rejected that input.", "INPUT_REJECTED") from RuntimeError(f"{code!r}: {out[:200]}")


def _key_call(mods: str, key: str, window: Window | None = None) -> str:
    if not _KEY.match(key) or not re.fullmatch(r"[A-Z ]*", mods):
        raise Refused("I don't know that key.", "UNKNOWN_KEY") from ValueError(f"{mods!r} {key!r}")
    target = f',window="address:{window.address}"' if window else ""
    return f'send_shortcut({{mods="{mods}",key="{key}"{target}}})'


def _char(ch: str) -> tuple[str, str]:
    """Typed character -> (mods, key) on a US layout."""
    if ch.isascii() and ch.isalnum():
        return ("SHIFT" if ch.isupper() else "", ch.upper())
    if ch in SYMBOLS:
        name, shift = SYMBOLS[ch]
        return ("SHIFT" if shift else "", name)
    raise Refused(f"I can't type the character {ch!r}. Please type it yourself.", "UNKNOWN_KEY")


def parse_combo(combo: str) -> tuple[str, str]:
    """'ctrl+shift+a' -> ('CTRL SHIFT', 'A')."""
    parts = [p for p in combo.strip().lower().split("+") if p] or [""]
    if combo.strip().endswith("+"):  # e.g. "ctrl++"
        parts.append("+")
    *mod_names, key = parts
    mods = []
    for m in mod_names:
        if m not in MODS:
            raise Refused(f"I don't know the modifier {m}.", "UNKNOWN_KEY")
        mods.append(MODS[m])
    if key in NAMED:
        return " ".join(mods), NAMED[key]
    if len(key) == 1:
        extra, name = _char(key)
        return " ".join(mods + ([extra] if extra and extra not in mods else [])), name
    raise Refused(f"I don't know the key {key}.", "UNKNOWN_KEY")


def parse_mods(text: str) -> str:
    """'ctrl+shift' -> 'CTRL SHIFT' (modifiers held during a click or drag)."""
    names = [m for m in text.strip().lower().replace(" ", "+").split("+") if m]
    unknown = [m for m in names if m not in MODS]
    if unknown:
        raise Refused(f"I don't know the modifier {unknown[0]}.", "UNKNOWN_KEY")
    return " ".join(dict.fromkeys(MODS[m] for m in names))


class Hands:
    def __init__(self, monitor):
        self.monitor = monitor
        self.target: str | None = None  # window class this lesson is allowed to act in
        self.last: Window | None = None

    def reset(self):
        self.target = None
        self.last = None

    # ---- which window ----------------------------------------------------
    def _windows(self) -> list[dict]:
        clients = json.loads(subprocess.check_output(["hyprctl", "clients", "-j"]))
        mons = json.loads(subprocess.check_output(["hyprctl", "monitors", "-j"]))
        mon = next((m for m in mons if m["name"] == self.monitor.name), None)
        if mon is None:
            return []
        shown = {mon["activeWorkspace"]["id"], mon.get("specialWorkspace", {}).get("id", 0)} - {0}
        return [c for c in clients if c["workspace"]["id"] in shown and c.get("mapped", True) and not c.get("hidden")]

    def window_at(self, x: float, y: float) -> Window | None:
        """Topmost window on this monitor containing the global point."""
        hits = [
            c for c in self._windows()
            if c["at"][0] <= x < c["at"][0] + c["size"][0] and c["at"][1] <= y < c["at"][1] + c["size"][1]
        ]
        if not hits:
            return None
        # A special workspace sits on top, then floating windows, then the most recently focused.
        c = min(hits, key=lambda c: (c["workspace"]["id"] >= 0, not c["floating"], c["focusHistoryID"]))
        return Window(c["address"], c["class"], c["title"], tuple(c["at"]), tuple(c["size"]))

    def _current(self) -> Window | None:
        """For keyboard actions without a point: the window last acted in, else the active one."""
        live = {c["address"]: c for c in self._windows()}
        if self.last and self.last.address in live:
            return self.last
        w = json.loads(subprocess.check_output(["hyprctl", "activewindow", "-j"]) or "{}")
        if not w or w.get("address") not in live:
            return None
        return Window(w["address"], w["class"], w["title"], tuple(w["at"]), tuple(w["size"]))

    def _allow(self, win: Window | None) -> Window:
        if win is None:
            raise Refused("There is no app window there for me to use. Please do this step yourself.", "NO_WINDOW")
        if not _ADDR.match(win.address):
            raise Refused("I could not identify that window.")
        cls = win.cls.lower()
        if cls in config.NEVER_ACT:
            raise Refused(f"I never click or type in {win.cls}. Please do this step yourself.", "NEVER_ACT")
        if self.target is None:
            self.target = win.cls
        elif cls != self.target.lower():
            raise Refused(f"That is outside {self.target}, where this lesson works. Please do this step yourself.", "OUTSIDE_APP")
        self.last = win
        return win

    # ---- actions -----------------------------------------------------------
    def _to_global(self, x: float, y: float) -> tuple[int, int]:
        return round(self.monitor.x + x), round(self.monitor.y + y)

    def _move(self, gx: int, gy: int):
        _dispatch(f"cursor.move({{x={gx},y={gy}}})")

    def _button(self, button: int, state: str, mods: str = ""):
        _dispatch(f'send_key_state({{mods="{mods}",key="mouse:{button}",state="{state}"}})')

    def _flush(self, gx: int, gy: int):
        # Hyprland sends the button release without a pointer frame; a 1px move delivers it.
        self._move(gx + 1, gy)
        self._move(gx, gy)

    def _aim(self, win: Window, gx: int, gy: int, armed=None, focus: bool = True):
        """Focus the window and put the cursor on the point, then check both before any input goes out.

        armed, if given, is called once everything is in place: the caller's last look at the
        screen before the input.
        """
        if focus:
            _dispatch(f'focus({{window="address:{win.address}"}})')
            active = json.loads(subprocess.check_output(["hyprctl", "activewindow", "-j"]) or "{}")
            if active.get("address") != win.address:
                raise Refused("I could not bring that window to the front. Click it once, then ask me again.", "WINDOW_NOT_FOCUSED")
        self._move(gx, gy)
        time.sleep(0.08)
        if armed:
            armed()
        pos = json.loads(subprocess.check_output(["hyprctl", "cursorpos", "-j"]))
        if abs(pos["x"] - gx) > CURSOR_TOL or abs(pos["y"] - gy) > CURSOR_TOL:
            raise Refused("The mouse moved just as I was about to act, so I stopped.", "CURSOR_NOT_AT_TARGET") from RuntimeError(
                f"cursor at ({pos['x']}, {pos['y']}), sent to ({gx}, {gy})"
            )

    def click(self, x: float, y: float, button: str = "left", count: int = 1, mods: str = "", armed=None) -> Window:
        """x, y: logical px on the monitor."""
        gx, gy = self._to_global(x, y)
        win = self._allow(self.window_at(gx, gy))
        self._aim(win, gx, gy, armed)
        for _ in range(count):
            self._button(BUTTONS[button], "down", mods)
            time.sleep(0.03)
            self._button(BUTTONS[button], "up", mods)
            time.sleep(0.05)
        self._flush(gx, gy)
        return win

    def drag(self, x: float, y: float, x2: float, y2: float, mods: str = "", armed=None) -> Window:
        gx, gy = self._to_global(x, y)
        gx2, gy2 = self._to_global(x2, y2)
        win = self._allow(self.window_at(gx, gy))
        if self.window_at(gx2, gy2) is None or self.window_at(gx2, gy2).address != win.address:
            raise Refused("That drag would end outside the app window. Please do it yourself.", "OUTSIDE_APP")
        self._aim(win, gx, gy, armed)
        self._button(BUTTONS["left"], "down", mods)
        try:
            steps = 12
            for i in range(1, steps + 1):
                self._move(round(gx + (gx2 - gx) * i / steps), round(gy + (gy2 - gy) * i / steps))
                time.sleep(0.025)
        finally:  # never leave the button held, even if a move is rejected halfway
            self._button(BUTTONS["left"], "up", mods)
        self._flush(gx2, gy2)
        return win

    def scroll(self, x: float, y: float, notches: int, armed=None) -> Window:
        """Turn the wheel at x, y. Positive scrolls down, negative up."""
        gx, gy = self._to_global(x, y)
        win = self._allow(self.window_at(gx, gy))
        self._aim(win, gx, gy, armed, focus=False)  # the wheel goes to whatever is under the cursor
        try:
            wheel.scroll(notches)
        except wheel.WheelError as e:
            raise Refused("I could not scroll there. Please scroll it yourself.", "INPUT_REJECTED") from e
        return win

    def keys(self, sequence: str, hover: tuple[float, float] | None = None, armed=None) -> Window:
        """sequence: combos separated by spaces, e.g. 'tab' or 'g x 2 enter' or 'ctrl+shift+a'."""
        combos = [parse_combo(c) for c in sequence.split()]
        if not 0 < len(combos) <= MAX_KEYS:
            raise Refused("That is too many keys for one step.", "TOO_LONG")
        win = self._hover_window(hover, armed)
        for mods, key in combos:
            _dispatch(_key_call(mods, key, win))
            time.sleep(0.06)
        return win

    def type_text(self, text: str, hover: tuple[float, float] | None = None, armed=None) -> Window:
        if not 0 < len(text) <= MAX_TYPE_CHARS:
            raise Refused("That text is too long for me to type. Please type it yourself.", "TOO_LONG")
        pairs = [_char(ch) for ch in text]  # validates every char before typing any
        win = self._hover_window(hover, armed)
        calls = [_key_call(mods, key, win) for mods, key in pairs]
        for i in range(0, len(calls), 20):
            _dispatch(*calls[i : i + 20])
        return win

    def _hover_window(self, hover, armed=None) -> Window:
        """Many apps (Blender) send shortcuts to the area under the mouse, so hover there first."""
        if hover is None:
            win = self._allow(self._current())
            if armed:
                armed()
            return win
        gx, gy = self._to_global(*hover)
        win = self._allow(self.window_at(gx, gy))
        self._aim(win, gx, gy, armed)
        return win
