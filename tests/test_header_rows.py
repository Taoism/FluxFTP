import json
from types import SimpleNamespace
from PySide6.QtCore import Qt, QMimeData, QPointF
from PySide6.QtTest import QTest
from fluxftp.app import Window
from fluxftp.header_rows import DEFAULT_ORDER, MIME
from test_queue import window


def test_move_drop_keyboard_and_restore(window):
    window.show()
    rows = window.header_rows
    assert rows.order() == list(DEFAULT_ORDER)
    original_fields = window.quick_host, window.quick_password
    window.quick_host.setText('example.invalid')
    rows.select('connection')
    assert rows.rows['connection'].property('selected')
    QTest.keyClick(rows.rows['connection'].grip, Qt.Key.Key_Up, Qt.KeyboardModifier.AltModifier)
    assert rows.order()[0] == 'connection'
    data = QMimeData()
    data.setData(MIME, b'tools')
    accepted = []
    target = rows.rows['overview']
    event = SimpleNamespace(source=lambda: rows.rows['tools'].grip, mimeData=lambda: data,
        position=lambda: QPointF(0, target.height()), setDropAction=lambda _: None,
        accept=lambda: accepted.append(True))
    target.dropEvent(event)
    assert accepted and rows.order()[-1] == 'tools'
    assert (window.quick_host, window.quick_password) == original_fields
    assert window.quick_host.text() == 'example.invalid'
    window.apply_display_settings()
    assert rows.order() == json.loads(window.preferences.value('Layout/header_row_order'))
    reopened = Window()
    reopened._smoke_test = True
    try:
        assert reopened.header_rows.order() == rows.order()
        reopened.header_rows.reset()
        assert reopened.header_rows.order() == list(DEFAULT_ORDER)
    finally:
        reopened.close()


def test_bad_saved_order_and_external_drop_rejected(window):
    window.preferences.setValue('Layout/header_row_order', '["tools", "tools"]')
    reopened = Window()
    reopened._smoke_test = True
    try:
        assert reopened.header_rows.order() == list(DEFAULT_ORDER)
        event = SimpleNamespace(source=lambda: None, mimeData=lambda: QMimeData())
        assert not reopened.header_rows.rows['tools'].valid_drag(event)
        reopened.add_workspace()
        reopened.preferences.setValue('Layout/multiple_lines', False)
        reopened.apply_layout_settings()
        bar = reopened.workspace_navigation.native_bar
        bar.setCurrentIndex(0)
        assert reopened.tabs.currentIndex() == 0
    finally:
        reopened.close()

def test_shared_rows_height_and_icons_persist(window):
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QPoint
    window.resize(1400, 900)
    window.show()
    rows = window.header_rows
    original = window.quick_password
    rows.join('tabs', 'tools')
    QApplication.processEvents()
    assert rows.groups[0] == ['tools', 'tabs']
    assert rows.rows['tools'].y() == rows.rows['tabs'].y()
    assert not window.layout_value('multiple_lines')
    band = rows.bands[0]
    band.setSizes([700, 500])
    band.splitterMoved.emit(700, 1)
    overview = rows.rows['overview']
    initial = overview.height()
    grip = overview.height_grip
    QTest.mousePress(grip, Qt.MouseButton.LeftButton, pos=QPoint(30, 3))
    QTest.mouseMove(grip, QPoint(30, 43))
    QTest.mouseRelease(grip, Qt.MouseButton.LeftButton, pos=QPoint(30, 43))
    assert overview.height() > initial
    overview.compact()
    assert overview.height() < initial
    height = overview.height()
    window.set_toolbar_icon_size(24)
    assert window.main_toolbar.iconSize().width() == 24
    assert all(p.file_toolbar.iconSize().width() == 24 for p in window.current_panes())
    rows.join('connection', 'tools')
    assert window.quick_password is original
    reopened = Window()
    reopened._smoke_test = True
    try:
        assert reopened.header_rows.groups == rows.groups
        assert reopened.header_rows.rows['overview'].height() == height
        assert reopened.main_toolbar.iconSize().width() == 24
        reopened.header_rows.detach('connection')
        assert ['connection'] in reopened.header_rows.groups
        assert ['tools', 'tabs'] in reopened.header_rows.groups
        reopened.header_rows.drop('overview', 'connection', True)
        assert ['tools', 'tabs'] in reopened.header_rows.groups
    finally:
        reopened.close()
