"""Full-screen, click-through overlay: pointer, caption, subtitle, status pill.

Every public method is safe to call from any thread. Coordinates are logical pixels relative to
the overlay's screen. It draws what the Wayland overlay draws.
"""
import math
import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
)
from PySide6.QtWidgets import QWidget

from .app import on_ui

MOVE_SECS = 0.7
PULSE_SECS = 4.0
FPS_MS = 33

STATUS = {
    "listening": ("Listening", (0.90, 0.25, 0.25)),
    "thinking": ("Looking at your screen", (0.95, 0.65, 0.15)),
    "speaking": ("Speaking", (0.25, 0.55, 0.95)),
}
ACCENT = (1.0, 0.78, 0.15)
ARROW = [(0, 0), (0, 26), (7, 20), (12, 31), (17, 29), (12, 18), (21, 18)]
WRAP = Qt.TextFlag.TextWordWrap


def _color(rgb, alpha=1.0) -> QColor:
    return QColor.fromRgbF(*rgb, alpha)


class Overlay(QWidget):
    def __init__(self, screen):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput | Qt.WindowType.WindowDoesNotAcceptFocus | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)  # clicks pass through
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow)  # stay up while another app is in front
        self.setGeometry(screen.geometry())

        self.hidden = False
        self.status = None
        self.caption = ""
        self.subtitle = ""
        self.pointer = None  # (x, y) current target, or None
        self._from = None
        self._moved_at = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self.show()

    # ---- public API ------------------------------------------------------
    @on_ui
    def set_status(self, status):
        self.status = status
        self.update()

    @on_ui
    def set_hidden(self, hidden):
        """Draw nothing, so a screenshot shows the screen without us."""
        self.hidden = hidden
        self.repaint()  # now, not at the next frame: the screenshot follows at once

    @on_ui
    def show_step(self, point, caption, subtitle):
        """point: (x, y) logical px or None."""
        if point is not None:
            self._from = self.pointer or (self.width() / 2, self.height() / 2)
            self.pointer = point
            self._moved_at = time.monotonic()
            self._timer.start(FPS_MS)
        else:
            self.pointer = None
        self.caption = caption
        self.subtitle = subtitle
        self.update()

    @on_ui
    def show_text(self, subtitle):
        self.subtitle = subtitle
        self.update()

    @on_ui
    def clear(self):
        self.pointer = None
        self.caption = ""
        self.subtitle = ""
        self.status = None
        self.update()

    # ---- animation -------------------------------------------------------
    def _tick(self):
        self.update()
        if self.pointer is None or time.monotonic() - self._moved_at >= MOVE_SECS + PULSE_SECS:
            self._timer.stop()

    def _pointer_pos(self):
        t = min(1.0, (time.monotonic() - self._moved_at) / MOVE_SECS)
        e = 1 - (1 - t) ** 3  # ease-out cubic
        (x0, y0), (x1, y1) = self._from, self.pointer
        arc = math.sin(math.pi * e) * min(120, math.hypot(x1 - x0, y1 - y0) * 0.2)
        return x0 + (x1 - x0) * e, y0 + (y1 - y0) * e - arc, t

    # ---- drawing ---------------------------------------------------------
    def paintEvent(self, _event):
        if self.hidden:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        width, height = self.width(), self.height()
        if self.status in STATUS:
            self._draw_status(p, width, *STATUS[self.status])
        if self.pointer is not None:
            x, y, t = self._pointer_pos()
            if t >= 1.0:
                self._draw_ring(p, x, y)
            self._draw_arrow(p, x, y)
            if self.caption:
                self._draw_caption(p, x, y, width, height)
        if self.subtitle:
            self._draw_subtitle(p, width, height)
        p.end()

    def _draw_ring(self, p, x, y):
        age = time.monotonic() - self._moved_at - MOVE_SECS
        pulse = (age % 1.2) / 1.2 if age < PULSE_SECS else 0.0
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(_color(ACCENT, 0.9), 3))
        p.drawEllipse(QPointF(x, y), 22, 22)
        if pulse:
            p.setPen(QPen(_color(ACCENT, 0.6 * (1 - pulse)), 3))
            p.drawEllipse(QPointF(x, y), 22 + 30 * pulse, 22 + 30 * pulse)

    def _draw_arrow(self, p, x, y):
        # The arrow's tip sits just outside the ring, pointing up-left at the target.
        p.setBrush(_color(ACCENT))
        p.setPen(QPen(QColor.fromRgbF(0, 0, 0, 0.8), 1.5))
        p.drawPolygon(QPolygonF([QPointF(x + 14 + px, y + 14 + py) for px, py in ARROW]))

    def _font(self, size, bold=False) -> QFont:
        font = QFont(self.font())
        font.setPointSizeF(size)
        font.setBold(bold)
        return font

    def _text_size(self, font, text, width=None, flags=0):
        box = QRectF(0, 0, width or 10_000, 10_000)
        r = QFontMetricsF(font).boundingRect(box, int(flags | (WRAP if width else 0)), text)
        return math.ceil(r.width()), math.ceil(r.height())

    def _bubble(self, p, x, y, w, h, alpha=0.88, border=False):
        path = QPainterPath()
        path.addRoundedRect(QRectF(x, y, w, h), 10, 10)
        p.fillPath(path, QColor.fromRgbF(0.07, 0.07, 0.09, alpha))
        if border:  # same thin yellow edge as the control panel
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(_color(ACCENT, 0.55), 1.5))
            p.drawPath(path)

    def _draw_caption(self, p, x, y, width, height):
        font, pad = self._font(15, bold=True), 12
        tw, th = self._text_size(font, self.caption, 420)
        w, h = tw + 2 * pad, th + 2 * pad
        bx = x + 44 if x + 44 + w < width - 16 else x - 44 - w
        by = min(max(16, y + 30), height - h - 16)
        self._bubble(p, bx, by, w, h)
        p.setFont(font)
        p.setPen(_color(ACCENT))
        p.drawText(QRectF(bx + pad, by + pad, tw, th), int(WRAP), self.caption)

    def _draw_subtitle(self, p, width, height):
        font, pad = self._font(16), 14
        centred = Qt.AlignmentFlag.AlignHCenter
        tw, th = self._text_size(font, self.subtitle, min(1100, width * 0.6), centred)
        w, h = tw + 2 * pad, th + 2 * pad
        bx, by = (width - w) / 2, height - h - 48
        self._bubble(p, bx, by, w, h, alpha=0.8, border=True)
        p.setFont(font)
        p.setPen(QColor.fromRgbF(1, 1, 1, 0.95))
        p.drawText(QRectF(bx + pad, by + pad, tw, th), int(WRAP | centred), self.subtitle)

    def _draw_status(self, p, width, label, color):
        font, pad, dot = self._font(12, bold=True), 10, 10
        tw, th = self._text_size(font, label)
        w, h = tw + 3 * pad + dot, th + 2 * pad
        bx, by = (width - w) / 2, 14
        self._bubble(p, bx, by, w, h, alpha=0.85)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_color(color))
        p.drawEllipse(QPointF(bx + pad + dot / 2, by + h / 2), dot / 2, dot / 2)
        p.setFont(font)
        p.setPen(QColor.fromRgbF(1, 1, 1, 0.95))
        p.drawText(QRectF(bx + 2 * pad + dot, by + pad, tw, th), 0, label)
