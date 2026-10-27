from PySide6.QtCore import Qt
from PySide6.QtCore import QPoint
from PySide6.QtTest import QTest
from fluxftp.core import Entry
from fluxftp.preferences import PreferencesDialog
from fluxftp.sort_options import sorted_entries, date_key
from test_queue import window


def names(entries):
    return [e.name for e in entries]


def test_reference_defaults_and_independent_controls(window):
    dialog = PreferencesDialog(window)
    assert len(dialog.sort_controls) == 4
    for controls in dialog.sort_controls.values():
        assert controls['keys']['Global'].isChecked()
        assert controls['directions']['Ascending'].isChecked()
        assert not controls['direction_box'].isEnabled()
    assert not any(c.isChecked() for c in dialog.sort_checks.values())
    controls = dialog.sort_controls[('remote', 'folder')]
    controls['keys']['Size'].setChecked(True)
    controls['directions']['Descending'].setChecked(True)
    assert controls['direction_box'].isEnabled()
    assert window.preferences.value('Sort/remote_folder_key') == 'Size'
    assert window.preferences.value('Sort/remote_folder_descending', type=bool)
    assert dialog.sort_controls[('local', 'folder')]['keys']['Global'].isChecked()
    controls['keys']['Global'].setChecked(True)
    assert not controls['direction_box'].isEnabled()
    dialog.close()


def test_independent_folder_file_and_location_rules(window):
    entries = [Entry('folder-a', True, 9), Entry('folder-b', True, 1),
               Entry('file-a', False, 1), Entry('file-b', False, 9)]
    settings = window.preferences
    settings.setValue('Sort/remote_folder_key', 'Size')
    settings.setValue('Sort/remote_file_key', 'Name')
    settings.setValue('Sort/remote_file_descending', True)
    assert names(sorted_entries(entries, settings, 'remote')) == ['folder-b', 'folder-a', 'file-b', 'file-a']
    assert names(sorted_entries(entries, settings, 'local')) == ['folder-a', 'folder-b', 'file-a', 'file-b']
    assert names(sorted_entries(entries, settings, 'local', 'Size', True)) == ['folder-a', 'folder-b', 'file-b', 'file-a']


def test_separate_links_and_tie_order(window):
    entries = [Entry('z', False, 2), Entry('a', False, 2), Entry('link', False, is_link=True),
               Entry('dirlink', True, is_link=True), Entry('folder', True)]
    assert names(sorted_entries(entries, window.preferences, 'local')) == ['dirlink', 'folder', 'a', 'link', 'z']
    window.preferences.setValue('Sort/separate_links', True)
    assert names(sorted_entries(entries, window.preferences, 'local', 'Size', True)) == ['folder', 'dirlink', 'link', 'a', 'z']


def test_dates_are_chronological_across_listing_formats(window):
    entries = [Entry('dec', False, modified='Dec 31 2025'), Entry('feb', False, modified='Feb 01 2026'),
               Entry('jan', False, modified='20260101000000'), Entry('mar', False, modified='03-01-26 12:00AM'),
               Entry('apr', False, modified='2026-04-01 00:00 UTC')]
    assert names(sorted_entries(entries, window.preferences, 'remote', 'Date')) == ['dec', 'jan', 'feb', 'mar', 'apr']
    assert date_key('1600000000')[0] == 1
    assert date_key('20260101000000.123')[1].year == 2026
    assert date_key('Jan 01 12:00')[0] == 1
    assert date_key('unknown')[0] == 0


def test_header_global_overrides_and_selection_without_refetch(window, monkeypatch):
    pane = window.current_panes()[0]
    entries = [Entry('a', False, 1, '20260101000000'), Entry('b', False, 9, '20250101000000')]
    pane.show_entries((entries, 'test'))
    row = pane.tree.topLevelItem(0)
    row.setSelected(True)
    pane.tree.setCurrentItem(row)
    monkeypatch.setattr(pane, 'refresh', lambda: (_ for _ in ()).throw(AssertionError('Sorting must not reconnect')))
    window.preferences.setValue('Sort/size_descending', True)
    pane.sort_column(1)
    assert pane.tree.topLevelItem(0).data(0, Qt.ItemDataRole.UserRole).name == 'b'
    assert pane.sort_descending and pane.selected()[0].name == 'a'
    assert pane.tree.currentItem() is row
    pane.sort_column(1)
    assert pane.tree.topLevelItem(0) is row and not pane.sort_descending
    window.preferences.setValue('Sort/date_descending', True)
    pane.sort_column(2)
    assert pane.tree.topLevelItem(0) is row and pane.sort_descending
    window.preferences.setValue('Sort/remote_file_key', 'Name')
    window.preferences.setValue('Sort/remote_file_descending', True)
    pane.resort_rows()
    assert pane.tree.topLevelItem(0).data(0, Qt.ItemDataRole.UserRole).name == 'b'
    pane.filter.setText('a')
    pane.resort_rows()
    assert pane.tree.topLevelItem(0).isHidden()
    assert len(pane.selected()) == 1


def test_clicking_visible_header_sorts_numeric_sizes(window):
    pane = window.current_panes()[0]
    pane.show_entries(([Entry('large', False, 100), Entry('small', False, 9)], 'test'))
    window.show()
    QTest.qWait(20)
    header = pane.tree.header()
    position = QPoint(header.sectionViewportPosition(1) + 15, header.height() // 2)
    QTest.mouseClick(header.viewport(), Qt.MouseButton.LeftButton, pos=position)
    assert pane.tree.topLevelItem(0).data(0, Qt.ItemDataRole.UserRole).name == 'small'
    assert header.sortIndicatorSection() == 1
    QTest.mouseClick(header.viewport(), Qt.MouseButton.LeftButton, pos=position)
    assert pane.tree.topLevelItem(0).data(0, Qt.ItemDataRole.UserRole).name == 'large'
