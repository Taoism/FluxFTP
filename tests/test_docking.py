from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDockWidget, QMessageBox
from test_queue import window


def test_dock_float_tab_hide_restore_and_return_preserves_widgets(window):
    window.show()
    controller = window.docking
    tabs, queue, ftp = window.tabs, window.queue, window.ftp_log
    controller.action.setChecked(True)
    QTest.qWait(100)
    assert controller.active and len(controller.docks) == len(controller.panel_titles)
    assert not controller.host.isWindow()
    assert controller.host.isVisibleTo(window)
    dock = controller.docks['queue']
    dock.setFloating(True)
    QTest.qWait(50)
    assert dock.isFloating()
    dock.hide()
    window.show_transfer_queue()
    assert not dock.isHidden()
    controller.host.tabifyDockWidget(controller.docks['ftp'], controller.docks['system'])
    controller.save()
    saved = window.preferences.value('Layout/dock_state')
    assert saved
    controller.action.setChecked(False)
    assert window.work_area.count() == 3 and window.log_tabs.count() == 4
    assert (window.tabs, window.queue, window.ftp_log) == (tabs, queue, ftp)
    controller.action.setChecked(True)
    QTest.qWait(50)
    assert dock.isFloating()
    assert controller.docks['system'] in controller.host.tabifiedDockWidgets(controller.docks['ftp'])
    controller.action.setChecked(False)


def test_docking_lock_and_reset(window, monkeypatch):
    controller = window.docking
    controller.action.setChecked(True)
    window.layout_lock.action.setChecked(True)
    assert not controller.action.isEnabled()
    assert all(d.features() == QDockWidget.DockWidgetFeature.NoDockWidgetFeatures for d in controller.docks.values())
    window.layout_lock.action.setChecked(False)
    assert controller.docks['queue'].features() & QDockWidget.DockWidgetFeature.DockWidgetMovable
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.StandardButton.Yes)
    window.reset_layout()
    # Custom docking is the default layout; reset restores that default after
    # clearing custom panel positions.
    assert controller.active and controller.action.isChecked() and window.work_area.count() == 1
    assert not window.preferences.value('Layout/manual_panel_positions', False, type=bool)


def test_docking_restores_in_new_window(window):
    from fluxftp.app import Window
    window.docking.action.setChecked(True)
    window.docking.docks['tasks'].hide()
    window.docking.save()
    second = Window()
    try:
        assert second.docking.active
        assert second.docking.docks['tasks'].isHidden()
        second.view_commands.show_panel('tasks')
        assert not second.docking.docks['tasks'].isHidden()
        assert second.tabs.count() == window.tabs.count()
    finally:
        second.close()
        window.docking.action.setChecked(False)


def test_floating_dock_geometry_is_saved_and_restored(window):
    controller = window.docking
    window.show()
    QTest.qWait(50)
    controller.action.setChecked(True)
    dock = controller.docks['queue']
    dock.setFloating(True)
    QTest.qWait(30)
    dock.setGeometry(120, 100, 520, 340)
    QTest.qWait(350)
    saved = window.preferences.value('Layout/dock_geometry/queue', '', type=str)
    assert saved
    dock.setGeometry(20, 30, 300, 200)
    assert controller.restore_floating_geometries() is None
    assert dock.geometry().width() >= 500
    controller.action.setChecked(False)
