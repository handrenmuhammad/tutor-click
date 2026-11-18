"""Mutter virtual devices, behind the shared one-app, focus and cursor guards."""
import time
import ctypes
import ctypes.util


from .. import hands as base
from ..base import Refused
from .bridge import request
from .screen import state, window

MODS = {"ctrl": "Control_L", "control": "Control_L", "shift": "Shift_L", "alt": "Alt_L",
        "super": "Super_L", "win": "Super_L", "meta": "Super_L"}
NAMED = {"enter": "Return", "return": "Return", "esc": "Escape", "escape": "Escape", "tab": "Tab",
         "space": "space", "delete": "Delete", "del": "Delete", "backspace": "BackSpace", "insert": "Insert",
         "up": "Up", "down": "Down", "left": "Left", "right": "Right", "home": "Home", "end": "End",
         "pageup": "Page_Up", "pagedown": "Page_Down", "numpadenter": "KP_Enter", "numpad.": "KP_Decimal",
         "numpad+": "KP_Add", "numpad-": "KP_Subtract", "numpad*": "KP_Multiply", "numpad/": "KP_Divide"}
NAMED.update({f"f{i}": f"F{i}" for i in range(1, 13)})
NAMED.update({f"numpad{i}": f"KP_{i}" for i in range(10)})
_xkb = ctypes.CDLL(ctypes.util.find_library("xkbcommon") or "libxkbcommon.so.0")
_xkb.xkb_keysym_from_name.argtypes = (ctypes.c_char_p, ctypes.c_int)
_xkb.xkb_keysym_from_name.restype = ctypes.c_uint32


def keysym(name):
    return _xkb.xkb_keysym_from_name(name.encode("ascii"), 0)


def parse_mods(text):
    names = [n for n in text.lower().replace(" ", "+").split("+") if n]
    if any(n not in MODS for n in names):
        raise Refused("I don't know that modifier.", "UNKNOWN_KEY")
    return list(dict.fromkeys(keysym(MODS[n]) for n in names))


def char_key(ch):
    if not ch.isascii() or not ch.isprintable():
        raise Refused(f"I can't type the character {ch!r}. Please type it yourself.", "UNKNOWN_KEY")
    return ord(ch)


def parse_combo(text):
    parts = text.strip().split("+")
    if text.endswith("+"):
        parts = [p for p in parts[:-1] if p] + ["+"]
    *mods, key = parts
    held = parse_mods("+".join(mods))
    if key.lower() in NAMED:
        return held, keysym(NAMED[key.lower()])
    if len(key) == 1:
        return held, char_key(key)
    raise Refused(f"I don't know the key {key}.", "UNKNOWN_KEY")


class Hands(base.Hands):
    keys_go_to_front = True

    def _send(self, op, **args):
        try:
            return request(op, **args)
        except RuntimeError as e:
            raise Refused(str(e), "INPUT_REJECTED") from e

    def window_at(self, x, y):
        return window(self._send("window-at", x=x, y=y))

    def _active(self):
        return window(state()["active"])

    def _is_open(self, win):
        return any(str(w["id"]) == win.address for w in state()["windows"])

    def _known(self, win):
        return win.address.isdecimal() and bool(win.cls)

    def _focus(self, win):
        self._send("focus", id=win.address)
        for _ in range(12):
            if self._is_front(win):
                return
            time.sleep(0.05)

    def _is_front(self, win):
        front = self._active()
        return front is not None and front.address == win.address

    def _move(self, gx, gy, dragging=False):
        self._send("move", x=gx, y=gy)

    def _cursor(self):
        return tuple(state()["cursor"])

    def _press(self, button, down, held, gx, gy, clicks=1):
        self._send("button", button=button, down=down, mods=held, id=self.last.address, x=gx, y=gy)

    def _wheel(self, notches):
        if not isinstance(notches, int) or abs(notches) > 10:
            raise Refused("That is too much scrolling for one step.", "TOO_LONG")
        self._send("scroll", notches=notches, id=self.last.address)

    _mods = staticmethod(parse_mods)
    _combo = staticmethod(parse_combo)

    def _tap(self, combo, win):
        self._send("key", id=win.address, mods=combo[0], key=combo[1])

    def _typed(self, text):
        return [char_key(ch) for ch in text]

    def _type(self, typed, win):
        for key in typed:
            self._tap(([], key), win)
            time.sleep(0.01)
