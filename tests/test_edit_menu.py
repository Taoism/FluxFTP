import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox
from fluxftp.edit_menu import rename_plan
from test_queue import window


def test_reference_and_active_pane(window, tmp_path, monkeypatch):
    commands = window.edit_commands
    assert [a.text() if not a.isSeparator() else '---' for a in window.edit_menu.actions()] == [
        'Search', '---', 'Rename', 'Delete', '---', 'Make Directory', '---',
        'Rename Multiple Files…', '---', 'Copy Selected Names', '---', 'Toolbars']
    assert [a.shortcut().toString() for a in commands.actions.values()] == [
        'Ctrl+F', 'F2', 'Del', 'Ins', 'Ctrl+Shift+M', '']
    pane = window.current_panes()[1]
    pane.mode = 'Local'
    pane.path = str(tmp_path)
    (tmp_path / 'old.txt').write_text('preserved')
    pane.refresh()
    window.show()
    window.activateWindow()
    pane.tree.setFocus()
    QTest.qWait(20)
    row = next(pane.tree.topLevelItem(i) for i in range(pane.tree.topLevelItemCount())
               if pane.tree.topLevelItem(i).text(0) == 'old.txt')
    pane.tree.setCurrentItem(row)
    commands.copy_names()
    assert QApplication.clipboard().text() == 'old.txt'
    calls = []
    monkeypatch.setattr(pane, 'rename', lambda: calls.append('rename'))
    QTest.keyClick(pane.tree, Qt.Key.Key_F2)
    assert calls == ['rename']
    answers = iter([('old', True), ('new', True)])
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **k: next(answers))
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.StandardButton.Ok)
    commands.rename_multiple()
    assert (tmp_path / 'new.txt').read_text() == 'preserved'
    assert not (tmp_path / 'old.txt').exists()
    pane.busy = True
    commands.update()
    assert not commands.actions['Delete'].isEnabled()
    pane.busy = False
    commands.search()
    assert pane.filter.hasFocus()


def test_rename_preflight():
    assert rename_plan(['old.txt'], 'old', 'new', ['old.txt']) == [('old.txt', 'new.txt')]
    with pytest.raises(ValueError):
        rename_plan(['old.txt'], 'old', 'new', ['old.txt', 'NEW.txt'])
    with pytest.raises(ValueError):
        rename_plan(['ab', 'aab'], 'a', '', ['ab', 'aab'])
    with pytest.raises(ValueError):
        rename_plan(['old'], 'old', '../outside', ['old'])
