"""What every desktop backend deals in. Nothing here touches a desktop."""
from dataclasses import dataclass


@dataclass
class Monitor:
    name: str
    logical_w: float
    logical_h: float
    scale: float
    x: int = 0  # position in the global layout
    y: int = 0


@dataclass
class Shot:
    jpeg: bytes
    width: int  # image pixels
    height: int
    factor: float  # image px = logical px * factor


@dataclass
class Window:
    address: str  # the backend's own handle for the window
    cls: str  # what the one-app rule and NEVER_ACT compare: the app, not the document
    title: str
    at: tuple[int, int] = (0, 0)  # global logical px
    size: tuple[int, int] = (0, 0)


class Refused(Exception):
    """The action was not performed. The message is spoken to the user; code names the reason."""

    def __init__(self, message: str, code: str = "REFUSED"):
        super().__init__(message)
        self.code = code


def jpeg_size(data: bytes) -> tuple[int, int]:
    """Read width/height from the JPEG SOF marker."""
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xC0, 0xC1, 0xC2):
            h = int.from_bytes(data[i + 5 : i + 7], "big")
            w = int.from_bytes(data[i + 7 : i + 9], "big")
            return w, h
        i += 2 + int.from_bytes(data[i + 2 : i + 4], "big")
    raise ValueError("not a JPEG or no SOF marker")
