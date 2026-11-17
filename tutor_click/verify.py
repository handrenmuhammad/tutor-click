"""Did the app's window change between two screenshots?

This is the only evidence the tutor has that an action did anything, and it is weak evidence: a
change shows that something happened in the window, not that the right thing happened (a tooltip
or an animation also counts). The useful direction is the other one: no change at all means the
action very probably did not register.
"""
import io
import math
from dataclasses import dataclass

import numpy as np
from PIL import Image

from .platform.base import Monitor, Shot, Window

# Measured on a Pinta window next to a busy one, 8 captures: every differing pixel was within 1 px
# of the window's edge, where downscaling mixes in the neighbour. EDGE px further in, two captures
# of an unchanged window were identical (0 pixels differ). Hovering a toolbar button changed 117.
EDGE = 4  # screenshot px trimmed off each side of the window
PIXEL_TOL = 8  # per-channel difference, of 255, below which a pixel counts as unchanged
MIN_PIXELS = 4  # fewer changed pixels than this is no change


@dataclass
class Change:
    pixels: int  # changed pixels inside the window, in screenshot pixels
    fraction: float  # of the window's area

    @property
    def changed(self) -> bool:
        return self.pixels >= MIN_PIXELS


def pixels(jpeg: bytes) -> np.ndarray:
    """Decode to an array of shape (height, width, 3)."""
    return np.asarray(Image.open(io.BytesIO(jpeg)).convert("RGB"))


def window_rect(win: Window, mon: Monitor, shot: Shot) -> tuple[int, int, int, int]:
    """The window's box in screenshot pixels, EDGE px in from each side: (left, top, right, bottom)."""
    f = shot.factor
    left, top = (win.at[0] - mon.x) * f, (win.at[1] - mon.y) * f
    right, bottom = left + win.size[0] * f, top + win.size[1] * f
    return (max(0, math.ceil(left) + EDGE), max(0, math.ceil(top) + EDGE),
            min(shot.width, math.floor(right) - EDGE), min(shot.height, math.floor(bottom) - EDGE))


def compare(before: Shot, after: Shot, rect: tuple[int, int, int, int]) -> Change:
    left, top, right, bottom = rect
    a = pixels(before.jpeg)[top:bottom, left:right].astype(np.int16)
    b = pixels(after.jpeg)[top:bottom, left:right].astype(np.int16)
    if a.size == 0:  # window off this monitor, or too small to judge
        return Change(0, 0.0)
    differs = (np.abs(a - b) > PIXEL_TOL).any(axis=2)
    n = int(differs.sum())
    return Change(n, n / differs.size)
