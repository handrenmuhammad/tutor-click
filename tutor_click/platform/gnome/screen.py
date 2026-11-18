"""GNOME's monitor layout and screenshots, with coordinates in compositor logical pixels."""
import base64
from io import BytesIO

from PIL import Image

from ..base import Monitor, Shot, Window
from .bridge import request


def state():
    return request("state")


def window(data):
    if not data:
        return None
    return Window(str(data["id"]), data["cls"], data["title"], tuple(data["at"]), tuple(data["size"]))


def focused_monitor():
    m = state()["monitor"]
    return Monitor(str(m["index"]), m["width"], m["height"], m["scale"], m["x"], m["y"])


def active_window():
    w = window(state()["active"])
    return f'{w.cls} - "{w.title}"' if w else "none (desktop)"


def capture(mon, max_edge):
    png = base64.b64decode(request("screenshot", monitor=int(mon.name)))
    with Image.open(BytesIO(png)) as image:
        image = image.convert("RGB")
        image.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
        output = BytesIO()
        image.save(output, "JPEG", quality=85)
        return Shot(output.getvalue(), image.width, image.height, image.width / mon.logical_w)
