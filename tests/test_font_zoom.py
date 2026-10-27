from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QToolButton
from fluxftp.preferences import PreferencesDialog
from test_queue import window


def wheel(view, delta, control=True):
    event = QWheelEvent(QPointF(10, 10), QPointF(10, 10), QPoint(), QPoint(0, delta),
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.ControlModifier if control else Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(view.viewport(), event)


def test_ctrl_wheel_changes_only_target_pane_and_persists(window):
    left, right = window.current_panes()
    initial = left.tree.font().pointSizeF()
    other = right.tree.font().pointSizeF()
    wheel(left.tree, 120)
    assert left.tree.font().pointSizeF() == initial + 1
    assert right.tree.font().pointSizeF() == other
    assert window.preferences.value('Display/zoom_left', type=float) == initial + 1
    window.apply_fonts()
    window.add_workspace()
    assert window.current_panes()[0].tree.font().pointSizeF() == initial + 1
    wheel(window.current_panes()[0].tree, -120)
    assert left.tree.font().pointSizeF() == initial


def test_queue_and_logs_zoom_independently_and_plain_wheel_does_not_zoom(window):
    queue_size = window.queue.font().pointSizeF()
    log_size = window.ftp_log.font().pointSizeF()
    log_family = window.ftp_log.font().family()
    wheel(window.queue, 240)
    assert window.queue.font().pointSizeF() == queue_size + 2
    assert window.ftp_log.font().pointSizeF() == log_size
    wheel(window.ftp_log, -120)
    assert window.ftp_log.font().pointSizeF() == log_size - 1
    assert window.ftp_log.font().family() == log_family
    wheel(window.queue, 120, control=False)
    assert window.queue.font().pointSizeF() == queue_size + 2


def test_partial_wheel_steps_limits_and_font_reset(window):
    size = window.queue.font().pointSizeF()
    wheel(window.queue, 60)
    assert window.queue.font().pointSizeF() == size
    wheel(window.queue, 60)
    assert window.queue.font().pointSizeF() == size + 1
    window.preferences.setValue('Display/zoom_queue', 1)
    window.apply_fonts()
    wheel(window.queue, -120)
    assert window.queue.font().pointSizeF() == 1
    dialog = PreferencesDialog(window)
    dialog._reset_fonts()
    assert not window.preferences.contains('Display/zoom_queue')
    assert window.queue.font().pointSizeF() == size
    dialog.close()


def resize_wheel(widget, delta=120, modifiers=Qt.KeyboardModifier.ControlModifier, pixels=0):
    event = QWheelEvent(QPointF(10, 10), QPointF(10, 10), QPoint(0, pixels), QPoint(0, delta),
        Qt.MouseButton.NoButton, modifiers, Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(widget, event)


def test_icon_wheel_over_buttons_applies_once_to_all_toolbars_and_new_tabs(window):
    window.set_toolbar_icon_size(24)
    button = window.main_toolbar.findChildren(QToolButton)[0]
    resize_wheel(button)
    assert window.toolbar_icon_size() == 28
    assert window.main_toolbar.iconSize().width() == 28
    for pane in window.current_panes():
        assert pane.file_toolbar.iconSize().width() == 28
    window.add_workspace()
    pane = window.current_panes()[0]
    assert pane.file_toolbar.iconSize().width() == 28
    resize_wheel(pane.file_toolbar.findChildren(QToolButton)[0], -120)
    assert window.toolbar_icon_size() == 24
    assert window.preferences.value('Layout/toolbar_icon_size', type=int) == 24


def test_icon_wheel_options_partial_steps_direction_and_limits(window):
    toolbar = window.main_toolbar
    window.set_toolbar_icon_size(24)
    resize_wheel(toolbar, 60)
    assert window.toolbar_icon_size() == 24
    resize_wheel(toolbar, -60)
    assert window.toolbar_icon_size() == 24
    resize_wheel(toolbar, -60)
    assert window.toolbar_icon_size() == 20
    resize_wheel(toolbar, 0, pixels=40)
    assert window.toolbar_icon_size() == 24
    resize_wheel(toolbar, 120, Qt.KeyboardModifier.NoModifier)
    resize_wheel(toolbar, 120, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
    assert window.toolbar_icon_size() == 24
    window.preferences.setValue('Mouse/wheel_icons', False)
    resize_wheel(toolbar)
    assert window.toolbar_icon_size() == 24
    window.preferences.setValue('Mouse/wheel_icons', True)
    resize_wheel(toolbar, 12000)
    assert window.toolbar_icon_size() == 48
    resize_wheel(toolbar, -12000)
    assert window.toolbar_icon_size() == 16


def test_workspace_text_zoom_and_disabled_text_option(window):
    size = window.window_list.font().pointSizeF()
    wheel(window.window_list, 120)
    assert window.window_list.font().pointSizeF() == size + 1
    assert window.preferences.value('Display/zoom_workspace', type=float) == size + 1
    from fluxftp.font_zoom import reset_zoom
    reset_zoom(window.preferences, 'logs')
    assert window.preferences.contains('Display/zoom_workspace')
    reset_zoom(window.preferences, 'application')
    window.apply_fonts()
    assert window.window_list.font().pointSizeF() == size
    window.preferences.setValue('Mouse/wheel_text', False)
    wheel(window.window_list, 120)
    wheel(window.ftp_log, 120)
    assert window.window_list.font().pointSizeF() == size
    assert not window.preferences.contains('Display/zoom_ftp_log')


def test_workspace_height_wheel_respects_preference_and_layout_lock(window):
    row = window.header_rows.rows['overview']
    row.set_height(140)
    resize_wheel(row.height_grip)
    assert row.height() == 156
    assert window.preferences.value('Layout/workspace_list_height', type=int) == 156
    window.preferences.setValue('Mouse/wheel_panels', False)
    resize_wheel(row.height_grip)
    assert row.height() == 156
    window.preferences.setValue('Mouse/wheel_panels', True)
    window.layout_lock.action.setChecked(True)
    resize_wheel(row.height_grip)
    assert row.height() == 156
    window.layout_lock.action.setChecked(False)
    resize_wheel(row.height_grip, -120)
    assert row.height() == 140
