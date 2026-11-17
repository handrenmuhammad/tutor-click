"""The control panel: a small always-on-top box to type, talk, switch modes and stop.

It is a layer-shell surface, so it floats over every app without becoming a tiled window.
Public methods are safe to call from any thread.
"""
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gdk, GLib, Gtk, Pango
from gi.repository import Gtk4LayerShell as LayerShell

MARGIN = 24
WIDTH = 470

CSS = """
window.tp-panel { background: transparent; }
.tp-box { background: rgba(18, 18, 23, 0.94); border-radius: 14px; border: 1px solid rgba(255, 200, 40, 0.35); padding: 10px 12px; }
.tp-box label { color: #f2f2f2; }
.tp-title { font-weight: bold; color: #ffc828; }
.tp-status { color: #a8a8b3; font-size: 12px; }
.tp-heard { color: #c9c9d4; font-size: 12px; }
.tp-box button { padding: 3px 9px; min-height: 26px; border-radius: 8px; background: #2a2a33; color: #f2f2f2; border: 1px solid #3a3a46; box-shadow: none; text-shadow: none; }
.tp-box button:hover { background: #363642; }
.tp-box button:checked { background: #ffc828; color: #14141a; border-color: #ffc828; }
.tp-box button:checked label { color: #14141a; }
.tp-box button.tp-stop { background: #7a2323; border-color: #a03030; }
.tp-box button.tp-rec:checked { background: #e04040; border-color: #e04040; }
.tp-box button.tp-rec:checked label { color: #ffffff; }
.tp-box button:disabled { opacity: 0.45; }
.tp-box entry { min-height: 30px; border-radius: 8px; background: #101014; color: #f2f2f2; border: 1px solid #3a3a46; }
"""


def _ui(fn):
    def wrapper(self, *args):
        GLib.idle_add(lambda: (fn(self, *args), False)[1])

    return wrapper


class Panel:
    def __init__(self, app: Gtk.Application, gdk_monitor, monitor, on_command, backends, backend):
        """on_command(cmd, text) runs on a worker thread, never on the GTK loop.

        backends: (id, menu text) of every provider; backend: the id in use.
        """
        self.monitor = monitor
        self.backends = [pid for pid, _ in backends]
        self.on_command = on_command
        self.models: list[str] = []  # ids behind the model menu's rows
        self.efforts: list[str] = []
        self.right = True
        self._setting = False

        win = Gtk.ApplicationWindow(application=app)
        win.add_css_class("tp-panel")
        LayerShell.init_for_window(win)
        LayerShell.set_layer(win, LayerShell.Layer.OVERLAY)
        LayerShell.set_namespace(win, "tutor-click-panel")
        LayerShell.set_exclusive_zone(win, -1)  # ignore bars, so our position is exactly the margins
        LayerShell.set_keyboard_mode(win, LayerShell.KeyboardMode.ON_DEMAND)
        if gdk_monitor is not None:
            LayerShell.set_monitor(win, gdk_monitor)
        self.win = win
        self._place()

        css = Gtk.CssProvider()
        css.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.add_css_class("tp-box")
        box.set_size_request(WIDTH, -1)

        head = Gtk.Box(spacing=8)
        title = Gtk.Label(label="Tutor Click")
        title.add_css_class("tp-title")
        self.status = Gtk.Label(label="Ready", xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        self.status.add_css_class("tp-status")
        self.guide_btn = Gtk.ToggleButton(label="I click", active=True, tooltip_text="You do every step yourself")
        self.auto_btn = Gtk.ToggleButton(label="Tutor clicks", group=self.guide_btn, tooltip_text="The tutor performs the steps while explaining")
        self.auto_btn.connect("toggled", self._mode_toggled)
        modes = Gtk.Box()
        modes.add_css_class("linked")
        modes.append(self.guide_btn)
        modes.append(self.auto_btn)
        for w in (title, self.status, modes,
                  self._button("⇄", "Move to the other corner", lambda: self._flip()),
                  self._button("–", "Collapse", lambda: self._collapse()),
                  self._button("✕", "Quit Tutor Click", lambda: self._send("quit"))):
            head.append(w)
        box.append(head)

        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.heard = Gtk.Label(label="Tell me what you want to learn.", xalign=0, wrap=True, lines=2,
                               ellipsize=Pango.EllipsizeMode.END, max_width_chars=60)
        self.heard.add_css_class("tp-heard")
        self.body.append(self.heard)

        row = Gtk.Box(spacing=6)
        self.entry = Gtk.Entry(hexpand=True, placeholder_text="Type a question or what to learn…")
        self.entry.connect("activate", lambda e: self._ask())
        self.mic = Gtk.ToggleButton(label="Talk", tooltip_text="Click, speak, click again to send (or hold Super+Alt+Space)")
        self.mic.add_css_class("tp-rec")
        self.mic.connect("toggled", self._mic_toggled)
        row.append(self.entry)
        row.append(self.mic)
        row.append(self._button("Send", "Send the text", self._ask))
        self.body.append(row)

        row = Gtk.Box(spacing=6, homogeneous=True)
        self.do_btn = self._button("Do it for me", "The tutor performs this one step", lambda: self._send("do"))
        self.do_btn.set_sensitive(False)
        stop = self._button("Stop", "Stop talking and stop acting", lambda: self._send("stop"))
        stop.add_css_class("tp-stop")
        for w in (self._button("Next step", "I did that, what's next?", lambda: self._send("next")),
                  self._button("Explain more", "Stop and explain this step in more detail", lambda: self._send("explain")),
                  self.do_btn, stop,
                  self._button("New lesson", "Forget this lesson", lambda: self._send("reset"))):
            row.append(w)
        self.body.append(row)

        row = Gtk.Box(spacing=6)
        who = Gtk.Label(label="Answers come from", xalign=0)
        who.add_css_class("tp-status")
        self.backend = Gtk.DropDown.new_from_strings([text for _, text in backends])
        self.backend.set_tooltip_text("Which AI looks at your screen, and who pays for it")
        self.backend.set_selected(self.backends.index(backend))
        self.backend.connect("notify::selected", self._backend_chosen)
        row.append(who)
        row.append(self.backend)
        self.body.append(row)

        row = Gtk.Box(spacing=6)
        self.model = Gtk.DropDown.new_from_strings([])
        self.model.set_tooltip_text("Which model answers. A smaller one is faster and uses less of your plan")
        self.model.connect("notify::selected", lambda d, _p: self._choice("model", self.models, d))
        self.effort = Gtk.DropDown.new_from_strings([])
        self.effort.set_tooltip_text("How hard the model thinks before it answers. Higher is slower")
        self.effort.connect("notify::selected", lambda d, _p: self._choice("effort", self.efforts, d))
        for text, menu in (("Model", self.model), ("Effort", self.effort)):
            label = Gtk.Label(label=text, xalign=0)
            label.add_css_class("tp-status")
            row.append(label)
            row.append(menu)
        self.body.append(row)
        box.append(self.body)

        win.set_child(box)
        win.present()

    # ---- widgets -----------------------------------------------------------
    def _button(self, label, tip, fn):
        b = Gtk.Button(label=label, tooltip_text=tip)
        b.connect("clicked", lambda _b: fn())
        return b

    def _send(self, cmd, text=""):
        threading.Thread(target=self.on_command, args=(cmd, text), daemon=True).start()

    def _ask(self):
        text = self.entry.get_text().strip()
        if text:
            self.entry.set_text("")
            self._send("ask", text)

    def _mode_toggled(self, btn):
        if not self._setting:
            self._send("mode", "auto" if btn.get_active() else "guide")

    def _backend_chosen(self, dropdown, _param):
        if not self._setting:
            self._send("backend", self.backends[dropdown.get_selected()])

    def _choice(self, cmd, ids, dropdown):
        i = dropdown.get_selected()
        if not self._setting and i < len(ids):
            self._send(cmd, ids[i])

    def _mic_toggled(self, btn):
        btn.set_label("Send voice" if btn.get_active() else "Talk")
        if not self._setting:
            self._send("ptt-down" if btn.get_active() else "ptt-up")

    def _collapse(self):
        self.body.set_visible(not self.body.get_visible())
        self.win.set_default_size(-1, -1)  # let the surface shrink or grow to fit

    def _place(self):
        for edge, on in ((LayerShell.Edge.BOTTOM, True), (LayerShell.Edge.RIGHT, self.right), (LayerShell.Edge.LEFT, not self.right)):
            LayerShell.set_anchor(self.win, edge, on)
            LayerShell.set_margin(self.win, edge, MARGIN)

    def _flip(self):
        self.right = not self.right
        self._place()

    def _rect(self):
        """Panel rectangle in logical px on the monitor."""
        w, h = self.win.get_width(), self.win.get_height()
        x0 = self.monitor.logical_w - MARGIN - w if self.right else MARGIN
        y0 = self.monitor.logical_h - MARGIN - h
        return x0, y0, x0 + w, y0 + h

    # ---- public API ----------------------------------------------------------
    @_ui
    def set_status(self, text):
        self.status.set_label(text)

    @_ui
    def set_heard(self, text):
        self.heard.set_label(text)

    @_ui
    def set_mode(self, mode):
        self._setting = True
        (self.auto_btn if mode == "auto" else self.guide_btn).set_active(True)
        self._setting = False

    @_ui
    def set_backend(self, backend):
        self._setting = True
        self.backend.set_selected(self.backends.index(backend))
        self._setting = False

    @_ui
    def set_choices(self, models, model, efforts, effort):
        """models: (id, name) of each model offered; efforts: the levels offered. A menu with nothing to choose is greyed out."""
        self._setting = True
        self.models, self.efforts = [m for m, _ in models], list(efforts)
        for menu, ids, names, current in ((self.model, self.models, [n for _, n in models], model),
                                          (self.effort, self.efforts, self.efforts or ["none"], effort)):
            menu.set_model(Gtk.StringList.new(names))
            if current in ids:
                menu.set_selected(ids.index(current))
            menu.set_sensitive(len(ids) > 1)
        self._setting = False

    @_ui
    def set_recording(self, on):
        self._setting = True
        self.mic.set_active(on)
        self._setting = False

    @_ui
    def set_can_do(self, on):
        self.do_btn.set_sensitive(on)

    @_ui
    def set_hidden(self, hidden):
        """Invisible for screenshots, without unmapping the surface."""
        self.win.set_opacity(0.0 if hidden else 1.0)

    @_ui
    def set_keyboard(self, on):
        """Off hands keyboard focus back to the app (before the tutor types); on lets the entry take it again."""
        mode = LayerShell.KeyboardMode.ON_DEMAND if on else LayerShell.KeyboardMode.NONE
        if LayerShell.get_keyboard_mode(self.win) != mode:
            LayerShell.set_keyboard_mode(self.win, mode)

    @_ui
    def avoid(self, x, y):
        """Jump to the other corner if (x, y) in logical px is under the panel."""
        x0, y0, x1, y1 = self._rect()
        if x0 - 40 <= x <= x1 + 40 and y0 - 40 <= y <= y1 + 40:
            self._flip()

    @_ui
    def show(self):
        self.body.set_visible(True)
        self.win.set_default_size(-1, -1)
        self.win.present()
