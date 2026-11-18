"""The tutor's hands: the guards and the order of every action, the same on every desktop.

A backend subclasses Hands and supplies the primitives at the bottom: finding windows, focusing,
moving the cursor, pressing, scrolling, sending keys. Nothing above them knows the desktop.
"""
import re
import time

from .. import config
from .base import Refused, Window

MAX_TYPE_CHARS = 200
MAX_KEYS = 12
CURSOR_TOL = 2  # px the cursor may be off its target when the press is about to go out
DRAG_STEPS = 12
_NUMBER = re.compile(r"-?\d*\.?\d+")  # a value typed into a field or a tool: 0.3, -90, .5
# Names a model may give a symbol key instead of the symbol itself ("s y 0 period 3 enter").
SYMBOL_NAMES = {
    "period": ".", "dot": ".", "comma": ",", "minus": "-", "hyphen": "-", "equal": "=", "equals": "=", "slash": "/",
    "backslash": "\\", "semicolon": ";", "apostrophe": "'", "quote": "'", "grave": "`", "backtick": "`",
    "bracketleft": "[", "bracketright": "]",
}


class Hands:
    keys_go_to_front = False  # True where a key press reaches whichever app is in front, not a window we name

    def __init__(self, monitor):
        self.monitor = monitor
        self.target: str | None = None  # window class this lesson is allowed to act in
        self.last: Window | None = None

    def reset(self):
        self.target = None
        self.last = None

    # ---- which window ----------------------------------------------------
    def _current(self) -> Window | None:
        """For keyboard actions without a point: the window last acted in, else the active one."""
        if self.last and self._is_open(self.last):
            return self.last
        win = self._active()
        return win if win and self._is_open(win) else None

    def _allow(self, win: Window | None, claim: bool = True) -> Window:
        """claim=False checks the window without making its app the lesson's app.

        Keys pressed with no point (a system shortcut such as the app launcher) land in whatever
        is in front, which says nothing about where the lesson is. Only an action aimed at a
        point in a window ties the lesson to that app.
        """
        if win is None:
            raise Refused("There is no app window there for me to use. Please do this step yourself.", "NO_WINDOW")
        if not self._known(win):
            raise Refused("I could not identify that window.")
        cls = win.cls.lower()
        if cls in config.NEVER_ACT:
            raise Refused(f"I never click or type in {win.cls}. Please do this step yourself.", "NEVER_ACT")
        if self.target is None:
            if not claim:
                return win
            self.target = win.cls
        elif cls != self.target.lower():
            raise Refused(f"That is outside {self.target}, where this lesson works. Please do this step yourself.", "OUTSIDE_APP")
        self.last = win
        return win

    # ---- actions -----------------------------------------------------------
    def _to_global(self, x: float, y: float) -> tuple[int, int]:
        return round(self.monitor.x + x), round(self.monitor.y + y)

    def _aim(self, win: Window, gx: int, gy: int, armed=None, focus: bool = True):
        """Focus the window and put the cursor on the point, then check both before any input goes out.

        armed, if given, is called once everything is in place: the caller's last look at the
        screen before the input.
        """
        if focus:
            self._focus(win)
            if not self._is_front(win):
                raise Refused("I could not bring that window to the front. Click it once, then ask me again.", "WINDOW_NOT_FOCUSED")
        self._move(gx, gy)
        time.sleep(0.08)
        if armed:
            armed()
        x, y = self._cursor()
        if abs(x - gx) > CURSOR_TOL or abs(y - gy) > CURSOR_TOL:
            raise Refused("The mouse moved just as I was about to act, so I stopped.", "CURSOR_NOT_AT_TARGET") from RuntimeError(
                f"cursor at ({x}, {y}), sent to ({gx}, {gy})"
            )

    def click(self, x: float, y: float, button: str = "left", count: int = 1, mods: str = "", armed=None) -> Window:
        """x, y: logical px on the monitor. mods: modifiers to hold, as the model writes them ("ctrl+shift")."""
        held = self._mods(mods)
        gx, gy = self._to_global(x, y)
        win = self._allow(self.window_at(gx, gy))
        self._aim(win, gx, gy, armed)
        for n in range(1, count + 1):
            self._press(button, True, held, gx, gy, n)
            time.sleep(0.03)
            self._press(button, False, held, gx, gy, n)
            time.sleep(0.05)
        self._released(gx, gy)
        return win

    def drag(self, x: float, y: float, x2: float, y2: float, mods: str = "", armed=None) -> Window:
        held = self._mods(mods)
        gx, gy = self._to_global(x, y)
        gx2, gy2 = self._to_global(x2, y2)
        win = self._allow(self.window_at(gx, gy))
        end = self.window_at(gx2, gy2)
        if end is None or end.address != win.address:
            raise Refused("That drag would end outside the app window. Please do it yourself.", "OUTSIDE_APP")
        self._aim(win, gx, gy, armed)
        self._press("left", True, held, gx, gy)
        at = (gx, gy)
        try:
            for i in range(1, DRAG_STEPS + 1):
                step = (round(gx + (gx2 - gx) * i / DRAG_STEPS), round(gy + (gy2 - gy) * i / DRAG_STEPS))
                self._move(*step, dragging=True)
                at = step
                time.sleep(0.025)
        finally:  # never leave the button held, even if a move is rejected halfway
            self._press("left", False, held, *at)
        self._released(gx2, gy2)
        return win

    def scroll(self, x: float, y: float, notches: int, armed=None) -> Window:
        """Turn the wheel at x, y. Positive scrolls down, negative up."""
        gx, gy = self._to_global(x, y)
        win = self._allow(self.window_at(gx, gy))
        self._aim(win, gx, gy, armed, focus=False)  # the wheel goes to whatever is under the cursor
        self._wheel(notches)
        return win

    def keys(self, sequence: str, hover: tuple[float, float] | None = None, armed=None) -> Window:
        """sequence: combos separated by spaces, e.g. 'tab' or 'g x 2 enter' or 'ctrl+shift+a'."""
        combos = [combo for token in sequence.split() for combo in self._combos(token)]
        if not 0 < len(combos) <= MAX_KEYS:
            raise Refused("That is too many keys for one step.", "TOO_LONG")
        win = self._hover_window(hover, armed)
        for combo in combos:
            self._tap(combo, win)
            time.sleep(0.06)
        return win

    def _combos(self, token: str) -> list:
        """One word of a key sequence, as the backend's key presses.

        Usually that is one press. A symbol may come by name ("period"), and a value may come in
        one piece ("0.3"), which is its characters pressed in turn. Only numbers are spelled out.
        """
        *mods, key = token.split("+") if not token.endswith("+") else [token]
        if key.lower() in SYMBOL_NAMES:
            token = "+".join([*mods, SYMBOL_NAMES[key.lower()]])
        try:
            return [self._combo(token)]
        except Refused:
            if not _NUMBER.fullmatch(token):  # a word spelled out key by key would fire one shortcut per letter
                raise
            return [self._combo(ch) for ch in token]

    def type_text(self, text: str, hover: tuple[float, float] | None = None, armed=None) -> Window:
        if not 0 < len(text) <= MAX_TYPE_CHARS:
            raise Refused("That text is too long for me to type. Please type it yourself.", "TOO_LONG")
        typed = self._typed(text)  # checks every character before typing any
        win = self._hover_window(hover, armed)
        self._type(typed, win)
        return win

    def _hover_window(self, hover, armed=None) -> Window:
        """Many apps (Blender) send shortcuts to the area under the mouse, so hover there first."""
        if hover is None:
            win = self._allow(self._current(), claim=False)
            if self.keys_go_to_front and not self._is_front(win):  # the lesson's app is open but another one has the keyboard
                self._focus(win)
                if not self._is_front(win):
                    raise Refused("I could not bring that window to the front. Click it once, then ask me again.", "WINDOW_NOT_FOCUSED")
            if armed:
                armed()
            return win
        gx, gy = self._to_global(*hover)
        win = self._allow(self.window_at(gx, gy))
        self._aim(win, gx, gy, armed)
        return win

    # ---- what a backend supplies -------------------------------------------
    def window_at(self, x: float, y: float) -> Window | None:
        """Topmost app window containing the global point."""
        raise NotImplementedError

    def _active(self) -> Window | None:
        """The window that has the keyboard."""
        raise NotImplementedError

    def _is_open(self, win: Window) -> bool:
        raise NotImplementedError

    def _known(self, win: Window) -> bool:
        """Whether the window's handle is one this backend can safely pass on."""
        return True

    def _focus(self, win: Window):
        raise NotImplementedError

    def _is_front(self, win: Window) -> bool:
        raise NotImplementedError

    def _move(self, gx: int, gy: int, dragging: bool = False):
        raise NotImplementedError

    def _cursor(self) -> tuple[float, float]:
        raise NotImplementedError

    def _press(self, button: str, down: bool, held, gx: int, gy: int, clicks: int = 1):
        """button: left, right or middle. held: what _mods returned. clicks: 2 for the second press of a double click."""
        raise NotImplementedError

    def _released(self, gx: int, gy: int):
        """After the last release of a click or drag, for a backend that has to nudge it through."""

    def _wheel(self, notches: int):
        raise NotImplementedError

    def _mods(self, text: str):
        """'ctrl+shift' -> the backend's form. Raises Refused for a modifier it does not know."""
        raise NotImplementedError

    def _combo(self, text: str):
        """'ctrl+z' -> the backend's form of one key press. Raises Refused for a key it does not know."""
        raise NotImplementedError

    def _tap(self, combo, win: Window):
        raise NotImplementedError

    def _typed(self, text: str):
        """The text in the backend's form. Raises Refused for a character it cannot type."""
        raise NotImplementedError

    def _type(self, typed, win: Window):
        raise NotImplementedError
