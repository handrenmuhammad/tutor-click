"""Every call into macOS, and nothing else: no decisions are made here.

Not run by its author. Each function is small so that `tutor-click doctor` can name the one that fails.
"""
import Quartz
from AppKit import (
    NSApplicationActivateIgnoringOtherApps,
    NSRunningApplication,
    NSWorkspace,
)
from ApplicationServices import AXIsProcessTrusted

FLAGS = {
    "cmd": Quartz.kCGEventFlagMaskCommand, "ctrl": Quartz.kCGEventFlagMaskControl,
    "alt": Quartz.kCGEventFlagMaskAlternate, "shift": Quartz.kCGEventFlagMaskShift,
}
_BUTTON = {"left": Quartz.kCGMouseButtonLeft, "right": Quartz.kCGMouseButtonRight, "middle": Quartz.kCGMouseButtonCenter}
_EVENT = {
    ("left", "down"): Quartz.kCGEventLeftMouseDown, ("left", "up"): Quartz.kCGEventLeftMouseUp, ("left", "drag"): Quartz.kCGEventLeftMouseDragged,
    ("right", "down"): Quartz.kCGEventRightMouseDown, ("right", "up"): Quartz.kCGEventRightMouseUp,
    ("middle", "down"): Quartz.kCGEventOtherMouseDown, ("middle", "up"): Quartz.kCGEventOtherMouseUp,
}


def can_control() -> bool:
    return bool(AXIsProcessTrusted())


def can_see_screen() -> bool:
    return bool(Quartz.CGPreflightScreenCaptureAccess())


def ask_to_see_screen():
    Quartz.CGRequestScreenCaptureAccess()


def windows() -> list[dict]:
    """Every window on screen, front to back. x, y, w, h are in points from the top left of the main screen."""
    listed = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements, Quartz.kCGNullWindowID
    ) or []
    out = []
    for w in listed:
        b = w.get(Quartz.kCGWindowBounds) or {}
        out.append({
            "pid": int(w.get(Quartz.kCGWindowOwnerPID, 0)), "number": int(w.get(Quartz.kCGWindowNumber, 0)),
            "owner": str(w.get(Quartz.kCGWindowOwnerName) or ""), "title": str(w.get(Quartz.kCGWindowName) or ""),
            "layer": int(w.get(Quartz.kCGWindowLayer, 0)),
            "x": float(b.get("X", 0)), "y": float(b.get("Y", 0)), "w": float(b.get("Width", 0)), "h": float(b.get("Height", 0)),
        })
    return out


def frontmost() -> tuple[int, str] | None:
    """(pid, name) of the app that has the keyboard."""
    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    return (int(app.processIdentifier()), str(app.localizedName() or "")) if app is not None else None


def activate(pid: int) -> bool:
    app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
    return bool(app is not None and app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps))


def cursor() -> tuple[float, float]:
    p = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
    return float(p.x), float(p.y)


def _post(event):
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)


def mouse(kind: str, x: float, y: float, button: str = "left", flags: int = 0, clicks: int = 1):
    """kind: move, drag, down or up."""
    etype = Quartz.kCGEventMouseMoved if kind == "move" else _EVENT[(button, kind)]
    event = Quartz.CGEventCreateMouseEvent(None, etype, (x, y), _BUTTON[button])
    Quartz.CGEventSetFlags(event, flags)
    if kind in ("down", "up"):
        Quartz.CGEventSetIntegerValueField(event, Quartz.kCGMouseEventClickState, clicks)
    _post(event)


def wheel(lines: int):
    """Positive scrolls up."""
    _post(Quartz.CGEventCreateScrollWheelEvent(None, Quartz.kCGScrollEventUnitLine, 1, lines))


def key(code: int, flags: int = 0):
    """Press and release one key by its virtual key code."""
    for down in (True, False):
        event = Quartz.CGEventCreateKeyboardEvent(None, code, down)
        Quartz.CGEventSetFlags(event, flags)
        _post(event)


def text(ch: str):
    """Type one character as itself, whatever the keyboard layout."""
    for down in (True, False):
        event = Quartz.CGEventCreateKeyboardEvent(None, 0, down)
        Quartz.CGEventKeyboardSetUnicodeString(event, len(ch.encode("utf-16-le")) // 2, ch)
        _post(event)
