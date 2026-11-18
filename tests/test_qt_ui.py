"""The Qt overlay and panel, drawn off screen: what the controls send and what gets painted."""
import os
import queue
import threading
import time
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_NO_GLIB", "1")  # the other tests load GTK into this process; keep Qt off its main loop
pytest.importorskip("PySide6")

from tutor_click.platform import qt
from tutor_click.platform.qt import overlay as qt_overlay

BACKENDS = [("claude-code", "Claude"), ("codex", "Codex")]
MODELS = [("claude-opus-5-5", "Opus 5.5"), ("claude-haiku-5-5", "Haiku 5.5")]


@pytest.fixture(scope="module")
def app():
    return qt.App(lambda _app: None)


@pytest.fixture
def ui(app):
    sent = queue.Queue()
    overlay, panel = qt.make_ui(app, qt.focused_monitor(), lambda cmd, text: sent.put((cmd, text, threading.current_thread() is threading.main_thread())),
                                BACKENDS, "claude-code")

    def pump(secs=0.03):
        end = time.monotonic() + secs
        while time.monotonic() < end:
            app.qt.processEvents()

    def got() -> list:
        pump()
        out = []
        while True:
            try:
                out.append(sent.get(timeout=0.3)[:2])
            except queue.Empty:
                return out

    yield overlay, panel, pump, got, sent
    overlay.close()
    panel.close()


def buttons(panel) -> dict:
    from PySide6.QtWidgets import QPushButton

    return {b.text(): b for b in panel.findChildren(QPushButton)}


def test_every_button_sends_its_command_from_a_worker_thread(ui):
    _, panel, pump, _, sent = ui
    panel.set_can_do(True)
    pump()
    b = buttons(panel)
    for label in ("Next step", "Explain more", "Do it for me", "Stop", "New lesson", "Tutor clicks", "I click", "\u2715"):
        b[label].click()
    pump()
    seen = [sent.get(timeout=1) for _ in range(8)]
    assert sorted(s[:2] for s in seen) == sorted([("next", ""), ("explain", ""), ("do", ""), ("stop", ""), ("reset", ""), ("mode", "auto"), ("mode", "guide"), ("quit", "")])
    assert not any(on_qt_thread for _, _, on_qt_thread in seen)  # a slow command must not freeze the panel


def test_typed_text_is_sent_once_and_the_box_is_cleared(ui):
    _, panel, _, got, _ = ui
    panel.entry.setText("  how do I crop?  ")
    buttons(panel)["Send"].click()
    assert got() == [("ask", "how do I crop?")]
    assert panel.entry.text() == ""
    buttons(panel)["Send"].click()  # nothing typed: nothing sent
    panel.entry.setText("and rotate?")
    panel.entry.returnPressed.emit()
    assert got() == [("ask", "and rotate?")]


def test_talk_starts_and_stops_a_recording(ui):
    _, panel, _, got, _ = ui
    panel.mic.click()
    assert (got(), panel.mic.text()) == ([("ptt-down", "")], "Send voice")
    panel.mic.click()
    assert (got(), panel.mic.text()) == ([("ptt-up", "")], "Talk")


def test_the_menus_show_what_is_on_offer_and_send_the_users_pick(ui):
    _, panel, pump, got, _ = ui
    panel.set_choices(MODELS, "claude-haiku-5-5", ["low", "medium", "high"], "medium")
    pump()
    assert [panel.model.itemText(i) for i in range(panel.model.count())] == ["Opus 5.5", "Haiku 5.5"]
    assert (panel.model.currentText(), panel.effort.currentText()) == ("Haiku 5.5", "medium")
    assert got() == []  # filling the menus is not a choice
    panel.model.activated.emit(0)
    panel.effort.activated.emit(2)
    panel.backend.activated.emit(1)
    assert sorted(got()) == [("backend", "codex"), ("effort", "high"), ("model", "claude-opus-5-5")]


def test_a_menu_with_nothing_to_choose_is_greyed_out(ui):
    _, panel, pump, _, _ = ui
    panel.set_choices([("", "its own default")], "", [], "")
    pump()
    assert (panel.model.isEnabled(), panel.effort.isEnabled()) == (False, False)
    assert (panel.model.currentText(), panel.effort.currentText()) == ("its own default", "none")


def test_what_the_app_sets_is_shown_and_sends_nothing(ui):
    _, panel, pump, got, _ = ui
    panel.set_mode("auto")
    panel.set_backend("codex")
    panel.set_recording(True)
    panel.set_status("Speaking\u2026")
    panel.set_heard("You: hello")
    panel.set_can_do(True)
    pump()
    assert (panel.auto_btn.isChecked(), panel.guide_btn.isChecked(), panel.backend.currentText()) == (True, False, "Codex")
    assert (panel.mic.isChecked(), panel.mic.text(), panel.status.text(), panel.heard.text()) == (True, "Send voice", "Speaking\u2026", "You: hello")
    assert panel.do_btn.isEnabled()
    assert got() == []


def test_the_panel_moves_away_from_a_point_under_it_and_hides_for_a_screenshot(ui):
    _, panel, pump, _, _ = ui
    pump()
    before = panel.x()
    panel.avoid(panel.x() + 10, panel.y() + 10)
    pump()
    assert panel.x() < before  # it started in the right corner
    panel.avoid(5, 5)  # nowhere near
    pump()
    assert panel.x() < before
    panel.set_hidden(True)
    pump()
    assert panel.windowOpacity() == 0.0
    panel.set_hidden(False)
    pump()
    assert panel.windowOpacity() == 1.0


def painted(overlay) -> int:
    """How many pixels of the overlay are not fully transparent."""
    image = overlay.grab().toImage()
    return sum(1 for y in range(0, image.height(), 2) for x in range(0, image.width(), 2) if image.pixelColor(x, y).alpha() > 0)


def test_the_overlay_draws_the_step_and_nothing_at_all_when_hidden(ui, monkeypatch):
    overlay, _, pump, _, _ = ui
    pump()
    assert painted(overlay) == 0  # nothing to show yet: fully see-through
    overlay.show_step((300, 200), "Click the brush", "Pick the brush in the toolbox.")
    overlay.set_status("thinking")
    pump()
    arrived = time.monotonic() + qt_overlay.MOVE_SECS + 0.1  # a clock for the overlay alone, in which the pointer has arrived
    monkeypatch.setattr(qt_overlay, "time", SimpleNamespace(monotonic=lambda: arrived))
    image = overlay.grab().toImage()
    assert image.pixelColor(300 + 22, 200).alpha() > 0  # the ring around the target
    assert image.pixelColor(300, 200).alpha() == 0  # the target itself stays visible through the ring
    assert painted(overlay) > 2000  # caption, subtitle and status pill as well
    overlay.set_hidden(True)
    pump()
    assert painted(overlay) == 0
    overlay.set_hidden(False)
    overlay.clear()
    pump()
    assert painted(overlay) == 0


def test_overlay_and_panel_calls_from_another_thread_run_on_the_qt_thread(ui, monkeypatch):
    overlay, _, pump, _, _ = ui
    ran_on = []
    monkeypatch.setattr(overlay, "update", lambda: ran_on.append(threading.current_thread() is threading.main_thread()))
    worker = threading.Thread(target=lambda: overlay.show_text("from a worker"))
    worker.start()
    worker.join()
    assert ran_on == []  # queued, not run on the worker
    pump()
    assert ran_on == [True]
    assert overlay.subtitle == "from a worker"


# ---- the doctor's mouse and keyboard check, against the test window ---------------
class QtHands:
    """Stands in for a backend's hands: delivers each action to the test window as the Qt events a real one would cause."""

    def __init__(self, probe, monitor, broken=()):
        self._probe, self.m, self.broken = probe, monitor, set(broken)

    @property
    def probe(self):
        return self._probe()  # the doctor makes its hands before the window is up

    def _on_qt(self, fn):
        from tutor_click.platform.qt import app as qt_app

        done = threading.Event()
        qt_app.call(lambda: (fn(), done.set()))
        assert done.wait(5)

    def _local(self, x, y):
        from PySide6.QtCore import QPoint

        g = self.probe.geometry()
        return QPoint(round(self.m.x + x - g.x()), round(self.m.y + y - g.y()))

    def click(self, x, y, button="left", count=1, mods="", armed=None):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest

        if "click" in self.broken:
            return
        pos = self._local(x, y)
        btn = Qt.MouseButton.RightButton if button == "right" else Qt.MouseButton.LeftButton

        def do():
            if self.probe.field.geometry().contains(pos):
                self.probe.field.setFocus()
            elif count == 2:
                QTest.mouseDClick(self.probe, btn, Qt.KeyboardModifier.NoModifier, pos)
            else:
                QTest.mouseClick(self.probe, btn, Qt.KeyboardModifier.NoModifier, pos)

        self._on_qt(do)

    def drag(self, x, y, x2, y2, mods="", armed=None):
        from PySide6.QtCore import QEvent, QPointF, Qt
        from PySide6.QtGui import QMouseEvent
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication

        a, b = self._local(x, y), self._local(x2, y2)

        def do():
            QTest.mousePress(self.probe, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, a)
            for i in range(1, 6):
                p = QPointF(a.x() + (b.x() - a.x()) * i / 5, a.y() + (b.y() - a.y()) * i / 5)
                QApplication.sendEvent(self.probe, QMouseEvent(QEvent.Type.MouseMove, p, QPointF(self.probe.mapToGlobal(p.toPoint())),
                                                               Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
            QTest.mouseRelease(self.probe, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, b)

        self._on_qt(do)

    def scroll(self, x, y, notches, armed=None):
        from PySide6.QtCore import QPoint, QPointF, Qt
        from PySide6.QtGui import QWheelEvent
        from PySide6.QtWidgets import QApplication

        pos = self._local(x, y)
        down = -1 if "scroll" not in self.broken else 1  # a wheel turned the wrong way
        event = QWheelEvent(QPointF(pos), QPointF(self.probe.mapToGlobal(pos)), QPoint(0, 0), QPoint(0, down * 120 * notches),
                            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
        self._on_qt(lambda: QApplication.sendEvent(self.probe, event))

    def keys(self, sequence, hover=None, armed=None):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest

        held = Qt.KeyboardModifier.ControlModifier if "+" in sequence else Qt.KeyboardModifier.NoModifier
        self._on_qt(lambda: (self.probe.setFocus(), QTest.keyClick(self.probe, Qt.Key.Key_A, held)))

    def type_text(self, text, hover=None, armed=None):
        from PySide6.QtTest import QTest

        if "typing" in self.broken:
            text = text[:-1]
        self._on_qt(lambda: QTest.keyClicks(self.probe.field, text))


def run_input_check(app, monkeypatch, broken=()):
    from tutor_click import doctor
    from tutor_click.platform.qt import probe as qt_probe

    nap = time.sleep
    monkeypatch.setattr(doctor, "time", SimpleNamespace(sleep=lambda secs: nap(min(secs, 0.05)), monotonic=time.monotonic))
    monitor = qt.focused_monitor()
    made = []

    def make_probe(app_, mon):
        made.append(qt_probe.make_probe(app_, mon))
        return made[0]

    class Desk:
        NAME = "macOS"

        @staticmethod
        def Hands(_monitor):
            return QtHands(lambda: made[0], monitor, broken)

    Desk.make_probe = staticmethod(make_probe)
    seen, out = {"mon": monitor}, {}

    def work():
        try:
            out["detail"] = doctor._input(Desk, app, seen, True)
        except doctor.Problem as e:
            out["problem"] = str(e)

    worker = threading.Thread(target=work)
    worker.start()
    end = time.monotonic() + 20
    while worker.is_alive() and time.monotonic() < end:
        app.qt.processEvents()
    worker.join(1)
    return out


def test_the_doctor_passes_input_that_arrives_as_sent(app, monkeypatch):
    out = run_input_check(app, monkeypatch)
    assert out.get("detail") == ("click worked; double click worked; right click worked; drag worked; scroll down worked; a key worked; "
                                 "a shortcut (cmd+a) worked; typing worked"), out


def test_the_doctor_names_each_kind_of_input_that_did_not_arrive(app, monkeypatch):
    out = run_input_check(app, monkeypatch, broken=("click", "scroll", "typing"))
    problem = out["problem"]
    assert "click DID NOT ARRIVE as sent: []" in problem
    assert "double click DID NOT ARRIVE" in problem and "right click DID NOT ARRIVE" in problem
    assert "scroll down DID NOT ARRIVE as sent: [{'kind': 'wheel'" in problem  # it arrived, turned the wrong way
    assert "drag worked" in problem and "a key worked" in problem
    assert "typing DID NOT ARRIVE" in problem and problem.endswith("the text field holds 'Hello 123'")


def test_without_the_flag_or_without_a_test_window_the_input_check_is_left_out():
    from tutor_click import doctor

    with pytest.raises(doctor.Skip, match="add --input"):
        doctor._input(SimpleNamespace(), None, {}, False)
    with pytest.raises(doctor.Skip, match="tutor-click bench"):
        doctor._input(SimpleNamespace(), None, {}, True)


def test_no_button_label_is_cut_off_whatever_the_desktop_font(app):
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QPushButton

    before = app.qt.font()
    try:
        for family, size in (("Adwaita Sans", 11), ("DejaVu Sans", 14), ("Sans", 9)):  # GNOME's own font, a wide one, a small one
            app.qt.setFont(QFont(family, size))
            _, panel = qt.make_ui(app, qt.focused_monitor(), lambda *_: None, BACKENDS, "claude-code")
            end = time.monotonic() + 0.1
            while time.monotonic() < end:
                app.qt.processEvents()
            narrow = [(b.text(), b.width(), b.sizeHint().width()) for b in panel.findChildren(QPushButton) if b.width() < b.sizeHint().width()]
            assert narrow == [], (family, size, narrow)
            assert panel.width() >= 470
            panel.close()
    finally:
        app.qt.setFont(before)
