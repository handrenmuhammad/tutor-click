"""Small JPEG screenshots for tests."""
from functools import lru_cache

import gi
import numpy as np

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib


@lru_cache(maxsize=64)
def jpeg(w: int, h: int, blocks: tuple = ()) -> bytes:
    """Grey image with a white square for each (x, y, size) in blocks."""
    a = np.full((h, w, 3), 60, np.uint8)
    for x, y, size in blocks:
        a[y : y + size, x : x + size] = 255
    pb = GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(a.tobytes()), GdkPixbuf.Colorspace.RGB, False, 8, w, h, w * 3)
    return bytes(pb.save_to_bufferv("jpeg", ["quality"], ["85"])[1])
