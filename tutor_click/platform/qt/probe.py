"""An ordinary window that writes down every input event that reaches it. `tutor-click doctor --input` aims at it."""
import threading

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QLineEdit, QVBoxLayout, QWidget

from . import app as qt_app

SIZE = (520, 360)


class Probe(QWidget):
    def __init__(self, screen):
        super().__init__()
        self.events: list[dict] = []
        self.setWindowTitle("Tutor Click doctor")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        layout = QVBoxLayout(self)
        notice = QLabel("Tutor Click is testing its mouse and keyboard here.\nPlease keep your hands off both for a few seconds.")
        notice.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)  # what lands on the text is the window's to record
        layout.addWidget(notice, 1)
        self.field = QLineEdit()
        layout.addWidget(self.field)
        area = screen.availableGeometry()
        self.setGeometry(area.center().x() - SIZE[0] // 2, area.center().y() - SIZE[1] // 2, *SIZE)
        self.show()
        self.raise_()
        self.activateWindow()

    def _note(self, kind, event, **more):
        p = event.globalPosition() if hasattr(event, "globalPosition") else None
        self.events.append({"kind": kind, "x": p.x() if p else None, "y": p.y() if p else None,
                            "mods": int(event.modifiers().value)} | more)

    def mousePressEvent(self, event):
        self._note("press", event, button=event.button().name)

    def mouseDoubleClickEvent(self, event):
        self._note("double", event, button=event.button().name)

    def mouseReleaseEvent(self, event):
        self._note("release", event, button=event.button().name)

    def mouseMoveEvent(self, event):
        if event.buttons() != Qt.MouseButton.NoButton:
            self._note("drag", event)

    def wheelEvent(self, event):
        self._note("wheel", event, dy=event.angleDelta().y())

    def keyPressEvent(self, event):
        self._note("key", event, key=event.key(), text=event.text())

    # ---- for the doctor, which runs on another thread ------------------------
    def spot(self, fx: float, fy: float) -> tuple[float, float]:
        """A point inside the window, as fractions of its size, in global logical px."""
        g = self.geometry()
        return g.x() + g.width() * fx, g.y() + g.height() * fy

    def field_spot(self) -> tuple[float, float]:
        c = self.field.mapToGlobal(self.field.rect().center())
        return c.x(), c.y()

    def typed(self) -> str:
        return self.field.text()

    def clear(self):
        self.events.clear()

    def shut(self):
        """Close the window. Like every change to a window, that has to happen on the Qt thread."""
        qt_app.call(self.close)


def make_probe(app, monitor) -> Probe:
    """Open the probe from any thread and wait until it is up."""
    made, ready = [], threading.Event()

    def make():
        made.append(Probe(qt_app._screen(monitor.name)))
        ready.set()

    qt_app.call(make)
    if not ready.wait(5):
        raise RuntimeError("the test window did not open")
    return made[0]
