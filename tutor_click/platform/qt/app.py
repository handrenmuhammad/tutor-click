"""The Qt main loop, and the way other threads reach it."""
import signal

from PySide6.QtCore import QObject, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QCursor, QGuiApplication
from PySide6.QtWidgets import QApplication

from ..base import Monitor


class _Bridge(QObject):
    """Carries a call from any thread to the thread that owns the windows."""

    call = Signal(object)

    def __init__(self):
        super().__init__()
        self.call.connect(self._run, Qt.ConnectionType.QueuedConnection)

    @Slot(object)
    def _run(self, fn):
        fn()


_bridge: _Bridge | None = None


def on_ui(fn):
    """Decorator for overlay and panel methods: safe to call from any thread, runs on the Qt thread."""

    def wrapper(self, *args):
        _bridge.call.emit(lambda: fn(self, *args))

    return wrapper


def call(fn):
    """Run fn on the Qt thread, from any thread. Does not wait for it."""
    _bridge.call.emit(fn)


class App:
    def __init__(self, on_start):
        """on_start(app) runs once on the Qt thread, when windows can be made."""
        global _bridge
        self.qt = QApplication.instance() or QApplication([])
        self.qt.setQuitOnLastWindowClosed(False)  # the panel can be collapsed or hidden without ending the app
        self._on_start = on_start
        _bridge = _Bridge()

    def run(self) -> int:
        for sig in (signal.SIGINT, signal.SIGTERM):  # Ctrl-C / kill end the loop cleanly
            signal.signal(sig, lambda *_: self.qt.quit())
        self._wake = QTimer()  # Python only sees a signal when its interpreter runs, so give it a turn
        self._wake.timeout.connect(lambda: None)
        self._wake.start(200)
        QTimer.singleShot(0, lambda: self._on_start(self))
        return self.qt.exec()

    def quit(self):
        _bridge.call.emit(self.qt.quit)


def _screen(name: str):
    return next((s for s in QGuiApplication.screens() if s.name() == name), QGuiApplication.primaryScreen())


def focused_monitor() -> Monitor:
    """The screen the mouse is on. Needs the App to exist."""
    screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
    g = screen.geometry()  # logical px, origin at the top left of the main screen
    return Monitor(screen.name(), g.width(), g.height(), screen.devicePixelRatio(), g.x(), g.y())


def make_ui(app: App, monitor: Monitor, on_command, backends, backend):
    from .overlay import Overlay
    from .panel import Panel

    screen = _screen(monitor.name)
    return Overlay(screen), Panel(screen, on_command, backends, backend)
