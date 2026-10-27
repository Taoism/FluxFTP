from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from fluxftp.app import Window
from fluxftp.config_backup import configuration
from test_queue import window


def test_moved_panels_and_panes_restore_on_next_window(window):
    window.show()
    window.resize(1600, 1100)
    QTest.qWait(20)
    window.work_area.moveSplitter(540, 1)
    window.tabs.widget(0).moveSplitter(420, 1)
    window.log_tabs.setCurrentWidget(window.log_panels['transfer'])
    before = window.work_area.sizes()
    panes = window.tabs.widget(0).sizes()
    window.close()
    assert window.preferences.value('Layout/manual_panel_positions', type=bool)
    configuration(window.preferences, {})  # Saved state remains JSON-backup compatible.
    reopened = Window()
    reopened._smoke_test = True
    try:
        reopened.resize(1600, 1100)
        reopened.show()
        QTest.qWait(30)
        after = reopened.work_area.sizes()
        assert all(abs(a / sum(after) - b / sum(before)) < .03 for a, b in zip(after, before))
        restored = reopened.tabs.widget(0).sizes()
        assert abs(restored[0] / sum(restored) - panes[0] / sum(panes)) < .03
        assert reopened.log_tabs.currentWidget() is reopened.log_panels['transfer']
        reopened.show_transfer_queue()
        assert reopened.work_area.sizes() == after
    finally:
        reopened.close()


def test_panel_order_collapsed_log_and_bad_state(window):
    window.show()
    QTest.qWait(20)
    window.work_area.insertWidget(0, window.queue_panel)
    window.work_area.setSizes([250, 650, 0])
    window.save_panel_layout()
    window.preferences.setValue('Layout/pane_positions/0/horizontal', 'not valid hex')
    reopened = Window()
    reopened._smoke_test = True
    try:
        reopened.show()
        QTest.qWait(20)
        assert reopened.work_area.widget(0) is reopened.queue_panel
        assert reopened.work_area.sizes()[2] == 0
        assert reopened.tabs.widget(0).orientation() == Qt.Orientation.Horizontal
    finally:
        reopened.close()
