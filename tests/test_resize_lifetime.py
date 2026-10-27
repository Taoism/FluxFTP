from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtTest import QTest
from shiboken6 import isValid
from test_queue import window


def test_pending_column_resize_dies_with_closed_workspace(window):
    window.add_workspace()
    pane = window.tabs.widget(0).widget(0)
    sizing = pane.tree._column_sizing
    timer = sizing.timer
    sizing.schedule()
    window.close_tab(0)
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not isValid(timer)
    QTest.qWait(20)
    window.add_workspace()
    QTest.qWait(20)
    assert window.tabs.count() == 2
