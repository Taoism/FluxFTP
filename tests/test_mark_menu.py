from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QInputDialog

from fluxftp.core import Entry, Site
from test_queue import window
from test_mouse import local_pair


def setup_panes(window, tmp_path):
    left, right, a, b = local_pair(window, tmp_path)
    for pane in (left, right):
        pane.show_entries(([Entry('one.zip', False, 1), Entry('two.TXT', False, 2), Entry('folder', True)], 'Test'))
    window.show()
    window.activateWindow()
    left.tree.setFocus()
    QTest.qWait(20)
    return left, right


def test_menu_and_keyboard_marks_are_independent_of_selection(window, tmp_path):
    left, right = setup_panes(window, tmp_path)
    actions = window.mark_menu.actions()
    assert [a.text() if not a.isSeparator() else '---' for a in actions] == [
        'Mark/Unmark', 'Unmark All', 'Select all Marked items', 'Export Mark List to Clipboard', '---', 'Select', 'Select All']
    assert [a.shortcut().toString() for a in actions] == ['Space', 'Ctrl+Shift+C', 'Ctrl+Shift+S', '', '', 'Ctrl+S', 'Ctrl+A']
    row = left.tree.topLevelItem(1)
    left.tree.setCurrentItem(row)
    QTest.keyClick(left.tree, Qt.Key.Key_Space)
    assert row in window.mark_commands.marked()
    assert row.font(0).bold()
    assert row.data(0, Qt.ItemDataRole.CheckStateRole) is None
    left.tree.clearSelection()
    QTest.keyClick(left.tree, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
    assert left.tree.selectedItems() == [row]
    window.mark_commands.export()
    assert QApplication.clipboard().text() == left.join(row.data(0, Qt.ItemDataRole.UserRole).name)
    right.tree.setFocus()
    QTest.keyClick(right.tree, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    assert len(right.selected()) == 3 and left.tree.selectedItems() == [row]
    assert not window.mark_commands.marked()
    left.tree.setFocus()
    QTest.keyClick(left.tree, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
    assert not window.mark_commands.marked()


def test_filter_clear_export_sort_and_refresh(window, tmp_path):
    left, _ = setup_panes(window, tmp_path)
    window.mark_commands.select_all()
    window.mark_commands.toggle()
    left.filter.setText('.zip')
    window.mark_commands.select_marked()
    assert [e.name for e in left.selected()] == ['one.zip']
    window.mark_commands.export()
    assert len(QApplication.clipboard().text().splitlines()) == 3
    left.sort_column(1)
    assert len(window.mark_commands.marked(False)) == 3
    assert [entry.name for entry in left.selected()] == ['one.zip']
    window.mark_commands.clear()
    assert not window.mark_commands.marked(False)
    left.filter.clear()
    window.mark_commands.select_all()
    window.mark_commands.toggle()
    left.refresh()
    assert not window.mark_commands.marked(False)


def test_mask_cancel_and_text_shortcuts(window, tmp_path, monkeypatch):
    left, right = setup_panes(window, tmp_path)
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **kw: ('*.ZIP;*.txt', True))
    QTest.keyClick(left.tree, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
    assert {e.name for e in left.selected()} == {'one.zip', 'two.TXT'}
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **kw: ('*', False))
    window.mark_commands.select_mask()
    assert len(left.selected()) == 2
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **kw: ('*.*', True))
    window.mark_commands.select_mask()
    assert len(left.selected()) == 3
    left.address.setFocus()
    left.address.setText('test text')
    QTest.keyClick(left.address, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    assert left.address.selectedText() == 'test text'
    QTest.keyClick(left.address, Qt.Key.Key_Space)
    assert left.address.text() == ' '
    assert not window.mark_commands.marked(False)


def test_workspace_and_busy_guards(window, tmp_path):
    left, right = setup_panes(window, tmp_path)
    right.tree.setFocus()
    window.mark_commands.select_all()
    window.mark_commands.toggle()
    original = window.tabs.currentWidget()
    window.add_workspace()
    window.mark_commands.update()
    assert not window.mark_commands.marked(False)
    window.tabs.setCurrentWidget(original)
    assert window.mark_commands.pane() is right
    right.busy = True
    window.mark_commands.update()
    assert all(not a.isEnabled() for a in window.mark_commands.actions)
    window.mark_commands.clear()
    right.busy = False
    assert len(window.mark_commands.marked(False)) == 3


def test_remote_paths_and_mask_navigation_guard(window, tmp_path, monkeypatch):
    left, _ = setup_panes(window, tmp_path)
    left.mode = 'Remote'
    left.site = Site(name='Test', host='example.test', username='private-user')
    left.password = 'private-password'
    left.path = '/incoming'
    left.show_entries(([Entry('日本語.zip', False, 1)], 'Test'))
    window.mark_commands.select_all()
    window.mark_commands.toggle()
    window.mark_commands.export()
    assert QApplication.clipboard().text() == '/incoming/日本語.zip'
    left.tree.clearSelection()
    def navigate_during_dialog(*args, **kwargs):
        left.path = '/elsewhere'
        left.generation += 1
        return '*', True
    monkeypatch.setattr(QInputDialog, 'getText', navigate_during_dialog)
    window.mark_commands.select_mask()
    assert not left.selected()
