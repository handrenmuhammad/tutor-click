"""The tutor's hands: mouse and keyboard input through Hyprland's own dispatchers.

Nothing here takes free text into Lua: key names come from fixed tables, numbers are ints,
window addresses are checked against a hex pattern.
"""
import json
import re
import subprocess

from .. import hands as base
from ..base import Refused, Window
from ..hands import MAX_KEYS, MAX_TYPE_CHARS  # noqa: F401 - the limits, for whoever reads them from here
from . import wheel

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

_ADDR = re.compile(r"^0x[0-9a-f]+$")
_KEY = re.compile(r"^(code:\d{1,3}|mouse:\d{3}|[A-Za-z0-9_]{1,24})$")


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


class Hands(base.Hands):
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

    def _active(self) -> Window | None:
        w = json.loads(subprocess.check_output(["hyprctl", "activewindow", "-j"]) or "{}")
        if not w:
            return None
        return Window(w.get("address", ""), w.get("class", ""), w.get("title", ""), tuple(w.get("at", (0, 0))), tuple(w.get("size", (0, 0))))

    def _is_open(self, win: Window) -> bool:
        return any(c["address"] == win.address for c in self._windows())

    def _known(self, win: Window) -> bool:
        return bool(_ADDR.match(win.address))  # it goes into Lua

    def _focus(self, win: Window):
        _dispatch(f'focus({{window="address:{win.address}"}})')

    def _is_front(self, win: Window) -> bool:
        active = self._active()
        return active is not None and active.address == win.address

    # ---- input -------------------------------------------------------------
    def _move(self, gx: int, gy: int, dragging: bool = False):
        _dispatch(f"cursor.move({{x={gx},y={gy}}})")

    def _cursor(self) -> tuple[float, float]:
        pos = json.loads(subprocess.check_output(["hyprctl", "cursorpos", "-j"]))
        return pos["x"], pos["y"]

    def _press(self, button: str, down: bool, held: str, gx: int, gy: int, clicks: int = 1):
        _dispatch(f'send_key_state({{mods="{held}",key="mouse:{BUTTONS[button]}",state="{"down" if down else "up"}"}})')

    def _released(self, gx: int, gy: int):
        # Hyprland sends the button release without a pointer frame; a 1px move delivers it.
        self._move(gx + 1, gy)
        self._move(gx, gy)

    def _wheel(self, notches: int):
        try:
            wheel.scroll(notches)
        except wheel.WheelError as e:
            raise Refused("I could not scroll there. Please scroll it yourself.", "INPUT_REJECTED") from e

    _mods = staticmethod(parse_mods)
    _combo = staticmethod(parse_combo)

    def _tap(self, combo: tuple[str, str], win: Window):
        _dispatch(_key_call(*combo, win))

    def _typed(self, text: str) -> list[tuple[str, str]]:
        return [_char(ch) for ch in text]

    def _type(self, typed: list[tuple[str, str]], win: Window):
        calls = [_key_call(mods, key, win) for mods, key in typed]
        for i in range(0, len(calls), 20):
            _dispatch(*calls[i : i + 20])
