from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from test_queue import window


def test_copy_names_for_one_folder_and_select_all_in_right_pane(window, tmp_path):
    pane = window.current_panes()[1]
    pane.mode = 'Local'
    pane.path = str(tmp_path)
    (tmp_path / 'folder').mkdir()
    (tmp_path / 'file.txt').write_text('test')
    pane.refresh()
    rows = [pane.tree.topLevelItem(i) for i in range(pane.tree.topLevelItemCount())
            if pane.tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole) is not None]
    folder = next(row for row in rows if row.text(0) == 'folder')
    pane.tree.clearSelection()
    folder.setSelected(True)
    for select_all in (False, True):
        if select_all:
            pane.tree.selectAll()
        expected = '\n'.join(entry.name for entry in pane.selected())
        menu = window.default_file_menu(pane)
        action = next(a for a in menu.actions() if a.text() == 'Copy Names')
        assert action.isEnabled()
        action.trigger()
        assert QApplication.clipboard().text() == expected
    pane.tree.clearSelection()
    menu = window.default_file_menu(pane)
    assert not next(a for a in menu.actions() if a.text() == 'Copy Names').isEnabled()
