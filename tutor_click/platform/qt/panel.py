"""The control panel: a small always-on-top box to type, talk, switch modes and stop.

Public methods are safe to call from any thread. It has the controls of the Wayland panel.
"""
import threading

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .app import on_ui

MARGIN = 24
WIDTH = 470

CSS = """
#box { background: rgba(18, 18, 23, 240); border-radius: 14px; border: 1px solid rgba(255, 200, 40, 90); }
QLabel { color: #f2f2f2; }
#title { font-weight: bold; color: #ffc828; }
#status, #small { color: #a8a8b3; font-size: 12px; }
#heard { color: #c9c9d4; font-size: 12px; }
QPushButton { padding: 3px 9px; min-height: 22px; border-radius: 8px; background: #2a2a33; color: #f2f2f2; border: 1px solid #3a3a46; }
QPushButton:hover { background: #363642; }
QPushButton:checked { background: #ffc828; color: #14141a; border-color: #ffc828; }
QPushButton:disabled { color: #77777f; }
#stop { background: #7a2323; border-color: #a03030; }
#mic:checked { background: #e04040; border-color: #e04040; color: #ffffff; }
QLineEdit, QComboBox { min-height: 26px; border-radius: 8px; background: #101014; color: #f2f2f2; border: 1px solid #3a3a46; padding: 0 6px; }
QComboBox:disabled { color: #77777f; }
QComboBox QAbstractItemView { background: #101014; color: #f2f2f2; selection-background-color: #363642; }
"""


class Panel(QWidget):
    def __init__(self, screen, on_command, backends, backend):
        """on_command(cmd, text) runs on a worker thread, never on the Qt thread.

        backends: (id, menu text) of every provider; backend: the id in use.
        """
        super().__init__()
        self.screen_ = screen
        self.backends = [pid for pid, _ in backends]
        self.models: list[str] = []  # ids behind the model menu's rows
        self.efforts: list[str] = []
        self.on_command = on_command
        self.right = True

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow)  # stay up while another app is in front
        self.setStyleSheet(CSS)

        box = QWidget(self, objectName="box")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(box)
        rows = QVBoxLayout(box)
        rows.setContentsMargins(12, 10, 12, 10)
        rows.setSpacing(8)
        self.setFixedWidth(WIDTH)

        head = QHBoxLayout()
        self.status = QLabel("Ready", objectName="status")
        self.guide_btn = QPushButton("I click", checkable=True, checked=True, toolTip="You do every step yourself")
        self.auto_btn = QPushButton("Tutor clicks", checkable=True, toolTip="The tutor performs the steps while explaining")
        modes = QButtonGroup(self)
        modes.addButton(self.guide_btn)
        modes.addButton(self.auto_btn)
        self.guide_btn.clicked.connect(lambda: self._send("mode", "guide"))
        self.auto_btn.clicked.connect(lambda: self._send("mode", "auto"))
        head.addWidget(QLabel("Tutor Click", objectName="title"))
        head.addWidget(self.status, 1)
        for w in (self.guide_btn, self.auto_btn,
                  self._button("\u21c4", "Move to the other corner", self._flip),
                  self._button("\u2013", "Collapse", self._collapse),
                  self._button("\u2715", "Quit Tutor Click", lambda: self._send("quit"))):
            head.addWidget(w)
        rows.addLayout(head)

        self.body = QWidget()
        body = QVBoxLayout(self.body)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(8)
        self.heard = QLabel("Tell me what you want to learn.", objectName="heard", wordWrap=True)
        self.heard.setFixedHeight(2 * self.heard.fontMetrics().lineSpacing() + 2)  # two lines, like the Wayland panel
        body.addWidget(self.heard)

        row = QHBoxLayout()
        self.entry = QLineEdit(placeholderText="Type a question or what to learn\u2026")
        self.entry.returnPressed.connect(self._ask)
        self.mic = QPushButton("Talk", objectName="mic", checkable=True, toolTip="Click, speak, click again to send")
        self.mic.clicked.connect(self._mic_clicked)
        row.addWidget(self.entry, 1)
        row.addWidget(self.mic)
        row.addWidget(self._button("Send", "Send the text", self._ask))
        body.addLayout(row)

        row = QHBoxLayout()
        self.do_btn = self._button("Do it for me", "The tutor performs this one step", lambda: self._send("do"))
        self.do_btn.setEnabled(False)
        stop = self._button("Stop", "Stop talking and stop acting", lambda: self._send("stop"))
        stop.setObjectName("stop")
        for w in (self._button("Next step", "I did that, what's next?", lambda: self._send("next")),
                  self._button("Explain more", "Stop and explain this step in more detail", lambda: self._send("explain")),
                  self.do_btn, stop,
                  self._button("New lesson", "Forget this lesson", lambda: self._send("reset"))):
            row.addWidget(w, 1)
        body.addLayout(row)

        row = QHBoxLayout()
        self.backend = QComboBox(toolTip="Which AI looks at your screen, and who pays for it")
        self.backend.addItems([text for _, text in backends])
        self.backend.setCurrentIndex(self.backends.index(backend))
        self.backend.activated.connect(lambda i: self._send("backend", self.backends[i]))  # fires for the user's choice only
        row.addWidget(QLabel("Answers come from", objectName="small"))
        row.addWidget(self.backend, 1)
        body.addLayout(row)

        row = QHBoxLayout()
        self.model = QComboBox(toolTip="Which model answers. A smaller one is faster and uses less of your plan")
        self.model.activated.connect(lambda i: i < len(self.models) and self._send("model", self.models[i]))
        self.effort = QComboBox(toolTip="How hard the model thinks before it answers. Higher is slower")
        self.effort.activated.connect(lambda i: i < len(self.efforts) and self._send("effort", self.efforts[i]))
        row.addWidget(QLabel("Model", objectName="small"))
        row.addWidget(self.model, 1)
        row.addWidget(QLabel("Effort", objectName="small"))
        row.addWidget(self.effort, 1)
        body.addLayout(row)
        rows.addWidget(self.body)

        # Fonts differ per desktop, so the panel is as wide as its labels need and never narrower than WIDTH.
        # A button only knows its real size once the style (padding, border) has been applied to it.
        for button in self.findChildren(QPushButton):
            button.ensurePolished()
            button.setMinimumWidth(button.sizeHint().width())
        self.layout().activate()
        self.setFixedWidth(max(WIDTH, self.layout().minimumSize().width()))
        self.show()
        self._place()

    # ---- widgets -----------------------------------------------------------
    def _button(self, label, tip, fn):
        b = QPushButton(label, toolTip=tip)
        b.clicked.connect(lambda: fn())
        return b

    def _send(self, cmd, text=""):
        threading.Thread(target=self.on_command, args=(cmd, text), daemon=True).start()

    def _ask(self):
        text = self.entry.text().strip()
        if text:
            self.entry.clear()
            self._send("ask", text)

    def _mic_clicked(self, on):
        self.mic.setText("Send voice" if on else "Talk")
        self._send("ptt-down" if on else "ptt-up")

    def _collapse(self):
        self.body.setVisible(not self.body.isVisible())
        self._place()

    def _flip(self):
        self.right = not self.right
        self._place()

    def _place(self):
        """Bottom corner of the screen's usable area (clear of a dock or menu bar)."""
        self.adjustSize()
        area = self.screen_.availableGeometry()
        x = area.right() - MARGIN - self.width() if self.right else area.left() + MARGIN
        self.move(x, area.bottom() - MARGIN - self.height())

    # ---- public API ----------------------------------------------------------
    @on_ui
    def set_status(self, text):
        self.status.setText(text)

    @on_ui
    def set_heard(self, text):
        self.heard.setText(text)

    @on_ui
    def set_mode(self, mode):
        (self.auto_btn if mode == "auto" else self.guide_btn).setChecked(True)

    @on_ui
    def set_backend(self, backend):
        self.backend.setCurrentIndex(self.backends.index(backend))

    @on_ui
    def set_choices(self, models, model, efforts, effort):
        """models: (id, name) of each model offered; efforts: the levels offered. A menu with nothing to choose is greyed out."""
        self.models, self.efforts = [m for m, _ in models], list(efforts)
        for menu, ids, names, current in ((self.model, self.models, [n for _, n in models], model),
                                          (self.effort, self.efforts, self.efforts or ["none"], effort)):
            menu.clear()
            menu.addItems(names)
            if current in ids:
                menu.setCurrentIndex(ids.index(current))
            menu.setEnabled(len(ids) > 1)

    @on_ui
    def set_recording(self, on):
        self.mic.setChecked(on)
        self.mic.setText("Send voice" if on else "Talk")

    @on_ui
    def set_can_do(self, on):
        self.do_btn.setEnabled(on)

    @on_ui
    def set_hidden(self, hidden):
        """Invisible for screenshots, without closing the window."""
        self.setWindowOpacity(0.0 if hidden else 1.0)

    @on_ui
    def set_keyboard(self, on):
        """Nothing to do here: the hands bring the lesson's app to the front before they type."""

    @on_ui
    def avoid(self, x, y):
        """Jump to the other corner if (x, y), in logical px on this screen, is under the panel."""
        g, origin = self.geometry(), self.screen_.geometry().topLeft()
        x0, y0 = g.left() - origin.x(), g.top() - origin.y()
        if x0 - 40 <= x <= x0 + g.width() + 40 and y0 - 40 <= y <= y0 + g.height() + 40:
            self._flip()

    @on_ui
    def show(self):
        self.body.setVisible(True)
        super().show()
        self._place()
        self.raise_()
