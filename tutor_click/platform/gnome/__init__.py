"""GNOME Wayland: Shell helper for the desktop, Qt/Xwayland panel, PipeWire audio."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .. import linux_sound as sound
from . import bridge
from .hands import Hands
from .screen import active_window, capture, focused_monitor

NAME = "Linux (GNOME)"
MOVE_SECS = 0.7


def preflight(argv):
    try:
        bridge.request("connect")
    except RuntimeError as e:
        sys.exit(str(e))
    # Xwayland supports absolute panel positioning and always-on-top; native Wayland does not.
    os.environ["QT_QPA_PLATFORM"] = "xcb"
    os.environ["QT_NO_GLIB"] = "1"
    # Qt's XI2 path drops Mutter's virtual wheel axes on this GNOME/Xwayland combination.
    os.environ["QT_XCB_NO_XI2"] = "1"


def permissions():
    try:
        bridge.request("connect")
        return [("Tutor Click GNOME helper", True, "")]
    except RuntimeError:
        return [("Tutor Click GNOME helper", False, bridge.SETUP)]


class App:
    def __init__(self, on_start):
        from ..qt.app import App as QtApp
        self._app = QtApp(lambda _: on_start(self))
        self._app.qt.setDesktopFileName("tutor-click")

    def run(self):
        try:
            return self._app.run()
        finally:
            bridge.close()

    def quit(self):
        self._app.quit()


class Overlay:
    def __init__(self, mon):
        self.mon = int(mon.name)
        self._set(monitor=self.mon, clear=True)

    def _set(self, **fields):
        bridge.request("overlay", **fields)

    def set_status(self, status):
        self._set(status=status)

    def set_hidden(self, hidden):
        self._set(hidden=hidden)

    def show_step(self, point, caption, subtitle):
        self._set(point=point, caption=caption, subtitle=subtitle)

    def show_text(self, subtitle):
        self._set(subtitle=subtitle)

    def clear(self):
        self._set(clear=True)


def make_ui(app, monitor, on_command, backends, backend):
    from ..qt.app import _screen
    from ..qt.panel import Panel
    panel = Panel(_screen_at(monitor, _screen), on_command, backends, backend)
    panel.setWindowTitle("Tutor Click")
    return Overlay(monitor), panel


def _screen_at(monitor, fallback):
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QGuiApplication
    return QGuiApplication.screenAt(QPoint(monitor.x, monitor.y)) or fallback(monitor.name)


def make_probe(app, monitor):
    from ..qt.probe import make_probe as qt_probe
    return qt_probe(app, monitor)


def binds(on, keys, cli):
    # The helper emits commands to our private D-Bus connection; it never launches shell text.
    try:
        bridge.request("binds", keys=keys if on else [])
    except RuntimeError:
        if on:
            raise


def prompt():
    from ...__main__ import send
    send("show")
    return ""


def install():
    from gi.repository import Gio
    share = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    source = Path(__file__).with_name("extension")
    target = share / "gnome-shell/extensions" / bridge.UUID
    target.mkdir(parents=True, exist_ok=True)
    for name in ("metadata.json", "extension.js"):
        shutil.copyfile(source / name, target / name)
    settings = Gio.Settings.new("org.gnome.shell")
    enabled = settings.get_strv("enabled-extensions")
    if bridge.UUID not in enabled:
        settings.set_strv("enabled-extensions", [*enabled, bridge.UUID])
    settings.set_strv("disabled-extensions", [u for u in settings.get_strv("disabled-extensions") if u != bridge.UUID])
    settings.set_boolean("disable-user-extensions", False)
    Gio.Settings.sync()
    exe = Path(sys.executable).with_name("tutor-click")
    desktop = share / "applications/tutor-click.desktop"
    icon = share / "icons/hicolor/scalable/apps/tutor-click.svg"
    desktop.parent.mkdir(parents=True, exist_ok=True)
    icon.parent.mkdir(parents=True, exist_ok=True)
    desktop.write_text('[Desktop Entry]\nType=Application\nName=Tutor Click\nGenericName=Screen Tutor\n'
                       f'Comment=A tutor that watches your screen and teaches you any app\nExec="{exe}" app\n'
                       'Icon=tutor-click\nTerminal=false\nCategories=Education;\n')
    icon.write_bytes((Path(__file__).parents[3] / "assets/tutor-click.svg").read_bytes())
    link = Path.home() / ".local/bin/tutor-click"
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.is_symlink() or not link.exists():
        link.unlink(missing_ok=True)
        link.symlink_to(exe)
    subprocess.run(["gnome-extensions", "enable", bridge.UUID], capture_output=True, check=False)
    print(f"Installed launcher and GNOME helper: {target}\nSign out of GNOME and sign back in once, then open Tutor Click.")


def run_bench(args):
    sys.exit("On GNOME run: tutor-click doctor --input")
