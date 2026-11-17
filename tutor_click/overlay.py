"""Full-screen, click-through layer-shell overlay: pointer, caption, subtitle, status pill.

Every public method is safe to call from any thread (marshalled onto the GTK loop).
Coordinates are logical pixels relative to the overlay's monitor.
"""
import math
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
import cairo
from gi.repository import Gdk, GLib, Gtk, Pango, PangoCairo
from gi.repository import Gtk4LayerShell as LayerShell

gi.require_foreign("cairo")

MOVE_SECS = 0.7
PULSE_SECS = 4.0
FPS_MS = 33

STATUS = {
    "listening": ("Listening", (0.90, 0.25, 0.25)),
    "thinking": ("Looking at your screen", (0.95, 0.65, 0.15)),
    "speaking": ("Speaking", (0.25, 0.55, 0.95)),
}
ACCENT = (1.0, 0.78, 0.15)


def _ui(fn):
    def wrapper(self, *args):
        GLib.idle_add(lambda: (fn(self, *args), False)[1])

    return wrapper


class Overlay:
    def __init__(self, app: Gtk.Application, monitor_name: str):
        win = Gtk.ApplicationWindow(application=app)
        LayerShell.init_for_window(win)
        LayerShell.set_layer(win, LayerShell.Layer.OVERLAY)
        LayerShell.set_namespace(win, "tutor")
        for edge in (LayerShell.Edge.TOP, LayerShell.Edge.BOTTOM, LayerShell.Edge.LEFT, LayerShell.Edge.RIGHT):
            LayerShell.set_anchor(win, edge, True)
        LayerShell.set_exclusive_zone(win, -1)
        LayerShell.set_keyboard_mode(win, LayerShell.KeyboardMode.NONE)
        mon = self._gdk_monitor(monitor_name)
        if mon is not None:
            LayerShell.set_monitor(win, mon)

        css = Gtk.CssProvider()
        css.load_from_string("window, drawingarea { background: transparent; }")
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        self.area = Gtk.DrawingArea()
        self.area.set_draw_func(self._draw)
        win.set_child(self.area)
        win.connect("realize", self._on_realize)
        self.win = win

        self.hidden = False
        self.status = None
        self.caption = ""
        self.subtitle = ""
        self.pointer = None  # (x, y) current target, or None
        self._from = None
        self._moved_at = 0.0
        self._ticking = False

        win.present()

    # ---- click-through -------------------------------------------------
    def _on_realize(self, win):
        surface = win.get_surface()
        surface.connect("layout", lambda s, w, h: self._clear_input(s))
        self._clear_input(surface)

    @staticmethod
    def _clear_input(surface):
        surface.set_input_region(cairo.Region())  # empty region => clicks pass through

    @staticmethod
    def _gdk_monitor(name):
        monitors = Gdk.Display.get_default().get_monitors()
        for i in range(monitors.get_n_items()):
            m = monitors.get_item(i)
            if m.get_connector() == name:
                return m
        return None

    # ---- public API ------------------------------------------------------
    @_ui
    def set_status(self, status):
        self.status = status
        self.area.queue_draw()

    @_ui
    def set_hidden(self, hidden):
        self.hidden = hidden
        self.area.queue_draw()

    @_ui
    def show_step(self, point, caption, subtitle):
        """point: (x, y) logical px or None."""
        if point is not None:
            self._from = self.pointer or self._default_origin()
            self.pointer = point
            self._moved_at = time.monotonic()
        else:
            self.pointer = None
        self.caption = caption
        self.subtitle = subtitle
        self._start_ticking()
        self.area.queue_draw()

    @_ui
    def show_text(self, subtitle):
        self.subtitle = subtitle
        self.area.queue_draw()

    @_ui
    def clear(self):
        self.pointer = None
        self.caption = ""
        self.subtitle = ""
        self.status = None
        self.area.queue_draw()

    # ---- animation -------------------------------------------------------
    def _default_origin(self):
        return (self.area.get_width() / 2, self.area.get_height() / 2)

    def _start_ticking(self):
        if not self._ticking:
            self._ticking = True
            GLib.timeout_add(FPS_MS, self._tick)

    def _tick(self):
        self.area.queue_draw()
        alive = self.pointer is not None and time.monotonic() - self._moved_at < MOVE_SECS + PULSE_SECS
        self._ticking = alive
        return alive

    def _pointer_pos(self):
        t = min(1.0, (time.monotonic() - self._moved_at) / MOVE_SECS)
        e = 1 - (1 - t) ** 3  # ease-out cubic
        (x0, y0), (x1, y1) = self._from, self.pointer
        arc = math.sin(math.pi * e) * min(120, math.hypot(x1 - x0, y1 - y0) * 0.2)
        return x0 + (x1 - x0) * e, y0 + (y1 - y0) * e - arc, t

    # ---- drawing ---------------------------------------------------------
    def _draw(self, area, cr, width, height):
        cr.set_operator(cairo.OPERATOR_CLEAR)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        if self.hidden:
            return
        if self.status in STATUS:
            self._draw_status(cr, width, *STATUS[self.status])
        if self.pointer is not None:
            x, y, t = self._pointer_pos()
            if t >= 1.0:
                self._draw_ring(cr, x, y)
            self._draw_arrow(cr, x, y)
            if self.caption:
                self._draw_caption(cr, x, y, width, height)
        if self.subtitle:
            self._draw_subtitle(cr, width, height)

    def _draw_ring(self, cr, x, y):
        age = time.monotonic() - self._moved_at - MOVE_SECS
        pulse = (age % 1.2) / 1.2 if age < PULSE_SECS else 0.0
        cr.set_source_rgba(*ACCENT, 0.9)
        cr.set_line_width(3)
        cr.new_path()  # arc() connects from any leftover current point (e.g. after text)
        cr.arc(x, y, 22, 0, 2 * math.pi)
        cr.stroke()
        if pulse:
            cr.set_source_rgba(*ACCENT, 0.6 * (1 - pulse))
            cr.new_path()
            cr.arc(x, y, 22 + 30 * pulse, 0, 2 * math.pi)
            cr.stroke()

    def _draw_arrow(self, cr, x, y):
        # Arrow tip sits just outside the ring, pointing up-left at the target.
        tx, ty = x + 14, y + 14
        pts = [(0, 0), (0, 26), (7, 20), (12, 31), (17, 29), (12, 18), (21, 18)]
        cr.new_path()
        cr.move_to(tx, ty)
        for px, py in pts[1:]:
            cr.line_to(tx + px, ty + py)
        cr.close_path()
        cr.set_source_rgba(*ACCENT, 1)
        cr.fill_preserve()
        cr.set_source_rgba(0, 0, 0, 0.8)
        cr.set_line_width(1.5)
        cr.stroke()

    def _layout(self, cr, text, size, bold=False, width=None):
        layout = PangoCairo.create_layout(cr)
        font = Pango.FontDescription.from_string(f"Sans {'Bold ' if bold else ''}{size}")
        layout.set_font_description(font)
        if width:
            layout.set_width(int(width * Pango.SCALE))
            layout.set_wrap(Pango.WrapMode.WORD_CHAR)
        layout.set_text(text, -1)
        return layout

    def _bubble(self, cr, x, y, w, h, alpha=0.88, border=False):
        r = 10
        cr.new_sub_path()
        cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
        cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
        cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
        cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
        cr.close_path()
        cr.set_source_rgba(0.07, 0.07, 0.09, alpha)
        if border:  # same thin yellow edge as the control panel
            cr.fill_preserve()
            cr.set_source_rgba(*ACCENT, 0.55)
            cr.set_line_width(1.5)
            cr.stroke()
        else:
            cr.fill()

    def _draw_caption(self, cr, x, y, width, height):
        layout = self._layout(cr, self.caption, 15, bold=True, width=420)
        _, ext = layout.get_pixel_extents()
        pad = 12
        w, h = ext.width + 2 * pad, ext.height + 2 * pad
        bx = x + 44 if x + 44 + w < width - 16 else x - 44 - w
        by = min(max(16, y + 30), height - h - 16)
        self._bubble(cr, bx, by, w, h)
        cr.set_source_rgba(*ACCENT, 1)
        cr.move_to(bx + pad - ext.x, by + pad - ext.y)
        PangoCairo.show_layout(cr, layout)

    def _draw_subtitle(self, cr, width, height):
        max_w = min(1100, width * 0.6)
        layout = self._layout(cr, self.subtitle, 16, width=max_w)
        layout.set_alignment(Pango.Alignment.CENTER)
        _, ext = layout.get_pixel_extents()
        pad = 14
        w, h = ext.width + 2 * pad, ext.height + 2 * pad
        bx, by = (width - w) / 2, height - h - 48
        self._bubble(cr, bx, by, w, h, alpha=0.8, border=True)
        cr.set_source_rgba(1, 1, 1, 0.95)
        cr.move_to(bx + pad - ext.x, by + pad - ext.y)
        PangoCairo.show_layout(cr, layout)

    def _draw_status(self, cr, width, label, color):
        layout = self._layout(cr, label, 12, bold=True)
        _, ext = layout.get_pixel_extents()
        pad, dot = 10, 10
        w, h = ext.width + 3 * pad + dot, ext.height + 2 * pad
        bx, by = (width - w) / 2, 14
        self._bubble(cr, bx, by, w, h, alpha=0.85)
        cr.set_source_rgba(*color, 1)
        cr.new_path()
        cr.arc(bx + pad + dot / 2, by + h / 2, dot / 2, 0, 2 * math.pi)
        cr.fill()
        cr.set_source_rgba(1, 1, 1, 0.95)
        cr.move_to(bx + 2 * pad + dot - ext.x, by + pad - ext.y)
        PangoCairo.show_layout(cr, layout)
