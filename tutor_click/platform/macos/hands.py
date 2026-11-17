"""The tutor's hands on macOS: Quartz events. The guards and the order of each action are in platform/hands.py."""
import time

from .. import hands as base
from ..base import Refused, Window
from . import native
from .screen import app_windows, pid_of

LINES_PER_NOTCH = 3  # what one wheel click scrolls. A guess: not measured on a Mac
MODS = {
    "cmd": "cmd", "command": "cmd", "super": "cmd", "win": "cmd", "meta": "cmd",
    "ctrl": "ctrl", "control": "ctrl", "alt": "alt", "option": "alt", "opt": "alt", "shift": "shift",
}
# Virtual key codes of the ANSI (US) keyboard, from Carbon's Events.h.
_ROWS = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9, "b": 11, "q": 12, "w": 13, "e": 14, "r": 15,
    "y": 16, "t": 17, "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23, "=": 24, "9": 25, "7": 26, "-": 27, "8": 28, "0": 29,
    "]": 30, "o": 31, "u": 32, "[": 33, "i": 34, "p": 35, "l": 37, "j": 38, "'": 39, "k": 40, ";": 41, "\\": 42, ",": 43, "/": 44,
    "n": 45, "m": 46, ".": 47, "`": 50,
}
NAMED = {
    "enter": 36, "return": 36, "tab": 48, "space": 49, "backspace": 51, "esc": 53, "escape": 53,
    "delete": 117, "del": 117, "home": 115, "end": 119, "pageup": 116, "pagedown": 121,
    "left": 123, "right": 124, "down": 125, "up": 126,
    "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97, "f7": 98, "f8": 100, "f9": 101, "f10": 109, "f11": 103, "f12": 111,
    "numpad0": 82, "numpad1": 83, "numpad2": 84, "numpad3": 85, "numpad4": 86, "numpad5": 87, "numpad6": 88, "numpad7": 89,
    "numpad8": 91, "numpad9": 92, "numpad.": 65, "numpad*": 67, "numpad+": 69, "numpad/": 75, "numpadenter": 76, "numpad-": 78,
}
_SHIFTED = dict(zip('~!@#$%^&*()_+{}|:"<>?', "`1234567890-=[]\\;',./", strict=True))


def parse_mods(text: str) -> int:
    """'cmd+shift' -> the event flags to hold during a click, drag or key."""
    flags = 0
    for name in (m for m in text.strip().lower().replace(" ", "+").split("+") if m):
        if name not in MODS:
            raise Refused(f"I don't know the modifier {name}.", "UNKNOWN_KEY")
        flags |= native.FLAGS[MODS[name]]
    return flags


def parse_combo(combo: str) -> tuple[int, int]:
    """'cmd+shift+a' -> (flags, key code)."""
    parts = [p for p in combo.strip().split("+") if p] or [""]
    if combo.strip().endswith("+"):  # e.g. "cmd++"
        parts.append("+")
    *mods, key = parts
    flags = parse_mods("+".join(mods))
    if key.lower() in NAMED:
        return flags, NAMED[key.lower()]
    if len(key) == 1:
        if key.lower() in _ROWS:
            return flags | (native.FLAGS["shift"] if key.isupper() else 0), _ROWS[key.lower()]
        if key in _SHIFTED:
            return flags | native.FLAGS["shift"], _ROWS[_SHIFTED[key]]
    raise Refused(f"I don't know the key {key}.", "UNKNOWN_KEY")


class Hands(base.Hands):
    def __init__(self, monitor):
        super().__init__(monitor)
        self._held = 0  # modifier flags of the button that is down, for the moves of a drag

    # ---- which window ----------------------------------------------------
    def window_at(self, x: float, y: float) -> Window | None:
        return next((w for w in app_windows() if w.at[0] <= x < w.at[0] + w.size[0] and w.at[1] <= y < w.at[1] + w.size[1]), None)

    def _active(self) -> Window | None:
        front = native.frontmost()
        return next((w for w in app_windows() if pid_of(w) == front[0]), None) if front else None

    def _is_open(self, win: Window) -> bool:
        return any(w.address == win.address for w in app_windows())

    def _focus(self, win: Window):
        """Bring the window's app to the front. macOS decides whether it comes; _is_front finds out."""
        pid = pid_of(win)
        native.activate(pid)
        for _ in range(12):  # up to 0.6 s
            if self._is_front(win):
                return
            time.sleep(0.05)

    def _is_front(self, win: Window) -> bool:
        front = native.frontmost()
        return front is not None and front[0] == pid_of(win)

    # ---- input -------------------------------------------------------------
    def _move(self, gx: int, gy: int, dragging: bool = False):
        native.mouse("drag" if dragging else "move", gx, gy, flags=self._held if dragging else 0)

    def _cursor(self) -> tuple[float, float]:
        return native.cursor()

    def _press(self, button: str, down: bool, held: int, gx: int, gy: int, clicks: int = 1):
        self._held = held if down else 0
        native.mouse("down" if down else "up", gx, gy, button, held, clicks)

    def _wheel(self, notches: int):
        native.wheel(-notches * LINES_PER_NOTCH)  # ours: positive is down. Quartz: positive is up

    _mods = staticmethod(parse_mods)
    _combo = staticmethod(parse_combo)

    def _tap(self, combo: tuple[int, int], win: Window):
        flags, code = combo
        native.key(code, flags)

    def _typed(self, text: str) -> str:
        bad = next((ch for ch in text if not ch.isprintable()), None)
        if bad is not None:
            raise Refused(f"I can't type the character {bad!r}. Please type it yourself.", "UNKNOWN_KEY")
        return text

    def _type(self, typed: str, win: Window):
        for ch in typed:
            native.text(ch)
            time.sleep(0.01)
