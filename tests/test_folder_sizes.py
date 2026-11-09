from fluxftp.core import Entry
from fluxftp.listing_view import PARENT_DIRECTORY_ROLE
from PySide6.QtCore import Qt
from test_queue import window


def test_empty_directory_has_parent_row_that_navigates_up(window, tmp_path):
    folder = tmp_path / 'empty'
    folder.mkdir()
    pane = window.current_panes()[0]
    pane.mode = 'Local'
    pane.path = str(folder)
    pane.local_path = str(folder)
    pane.show_entries(([], 'Local files'))
    row = pane.tree.topLevelItem(0)
    assert row.text(0) == 'Parent directory'
    assert row.data(0, PARENT_DIRECTORY_ROLE)
    assert row.data(0, Qt.ItemDataRole.UserRole) is None
    pane.tree.setCurrentItem(row)
    assert pane.selected() == []
    pane.open_item(row)
    assert pane.path == str(tmp_path)


def test_directory_size_column_shows_reported_bytes_immediately(window, tmp_path):
    pane = window.current_panes()[0]
    pane.mode = 'Local'
    pane.path = str(tmp_path)
    pane.local_path = str(tmp_path)
    pane.show_entries(([Entry('folder', True, 24576)], 'Local files'))
    row = next(pane.tree.topLevelItem(i) for i in range(pane.tree.topLevelItemCount())
               if getattr(pane.tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole), 'name', '') == 'folder')
    assert row.text(1) == window.file_size(24576)
