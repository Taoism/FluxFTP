from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from fluxftp.app import Window
from fluxftp.config_backup import configuration
from test_queue import window


def test_moved_panels_and_panes_restore_on_next_window(window):
    window.show()
    window.resize(1600, 1100)
    QTest.qWait(20)
    docking = window.docking
    workspace = docking.docks['workspace']
    ftp = docking.docks['ftp']
    transfer = docking.docks['transfer']
    docking.host.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, workspace)
    docking.host.tabifyDockWidget(ftp, transfer)
    transfer.raise_()
    window.tabs.widget(0).moveSplitter(420, 1)
    panes = window.tabs.widget(0).sizes()
    docking.save()
    window.close()
    configuration(window.preferences, {})  # Saved state remains JSON-backup compatible.
    reopened = Window()
    reopened._smoke_test = True
    try:
        reopened.resize(1600, 1100)
        reopened.show()
        QTest.qWait(30)
        docking = reopened.docking
        assert docking.host.dockWidgetArea(docking.docks['workspace']) == Qt.DockWidgetArea.RightDockWidgetArea
        assert docking.docks['transfer'] in docking.host.tabifiedDockWidgets(docking.docks['ftp'])
        assert docking.docks['transfer'].isVisible()
        restored = reopened.tabs.widget(0).sizes()
        assert abs(restored[0] / sum(restored) - panes[0] / sum(panes)) < .03
    finally:
        reopened.close()


def test_panel_order_collapsed_log_and_bad_state(window):
    window.show()
    QTest.qWait(20)
    docking = window.docking
    queue = docking.docks['queue']
    tasks = docking.docks['tasks']
    queue.hide()
    docking.host.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, tasks)
    docking.save()
    window.preferences.setValue('Layout/pane_positions/0/horizontal', 'not valid hex')
    reopened = Window()
    reopened._smoke_test = True
    try:
        reopened.show()
        QTest.qWait(20)
        assert reopened.docking.docks['queue'].isHidden()
        assert reopened.docking.host.dockWidgetArea(reopened.docking.docks['tasks']) == Qt.DockWidgetArea.RightDockWidgetArea
        assert reopened.tabs.widget(0).orientation() == Qt.Orientation.Horizontal
    finally:
        reopened.close()
