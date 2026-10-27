from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QHeaderView, QMenu

from test_queue import window


def test_every_pane_and_queue_divider_is_draggable(window):
    window.show()
    window.show_transfer_queue()
    QTest.qWait(50)
    trees = [pane.tree for pane in window.current_panes()] + [window.queue]
    for tree in trees:
        header = tree.header()
        assert not header.stretchLastSection()
        for column in range(tree.columnCount()):
            assert header.sectionResizeMode(column) == QHeaderView.ResizeMode.Interactive
            if tree.isColumnHidden(column): continue
            # Make each divider accessible, including the final Remark edge.
            tree.scrollToItem(tree.topLevelItem(0)) if tree.topLevelItemCount() else None
            header.resizeSection(column, 65)
            tree.horizontalScrollBar().setValue(tree.horizontalScrollBar().maximum())
            QTest.qWait(5)
            x = header.sectionViewportPosition(column) + header.sectionSize(column) - 1
            if x < 0:
                tree.horizontalScrollBar().setValue(max(0, header.sectionPosition(column) - 20))
                x = header.sectionViewportPosition(column) + header.sectionSize(column) - 1
            before = header.sectionSize(column)
            point = QPoint(x, header.height() // 2)
            QTest.mousePress(header.viewport(), Qt.MouseButton.LeftButton, pos=point)
            QTest.mouseMove(header.viewport(), point + QPoint(20, 0), delay=10)
            QTest.mouseRelease(header.viewport(), Qt.MouseButton.LeftButton, pos=point + QPoint(20, 0))
            assert header.sectionSize(column) > before, (tree._view_group, column)


def test_fit_and_manual_width_survive_refresh_and_window_resize(window):
    window.show(); window.show_transfer_queue()
    QTest.qWait(50)
    tree = window.queue
    header = tree.header()
    assert abs(sum(header.sectionSize(i) for i in range(8)) - tree.viewport().width()) <= 1
    # A drag wins over the default auto-fit policy.
    header.resizeSection(5, 321)
    assert not window.view_commands.options.actions['auto_size/queue'].isChecked()
    window.view_commands.options.apply()
    window.resize(window.width() + 100, window.height())
    QTest.qWait(30)
    assert header.sectionSize(5) == 321
    assert window.preferences.value('Display/column_width/queue/5', type=int) == 321
    chosen = []
    def choose_fit():
        menu = QApplication.activePopupWidget()
        if isinstance(menu, QMenu):
            chosen.append(menu.actions()[0].text())
            menu.actions()[0].trigger()
            menu.close()
    QTimer.singleShot(30, choose_fit)
    tree._column_sizing.menu(QPoint(20, header.height() // 2))
    assert chosen == ['Fit columns to window']
    assert sum(header.sectionSize(i) for i in range(8)) == tree.viewport().width()
    window.view_commands.options.actions['auto_size/queue'].trigger()
    window.resize(window.width() + 120, window.height())
    QTest.qWait(30)
    assert sum(header.sectionSize(i) for i in range(8)) == tree.viewport().width()
