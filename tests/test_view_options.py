from types import SimpleNamespace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHeaderView
from PySide6.QtTest import QTest

from fluxftp.core import Entry, Site, Connection, parse_list_entry
from fluxftp.view_options import GRID_MODES, GridDelegate
from test_queue import window
from test_integration import servers


def entries(pane):
    pane.show_entries(([Entry('long_filename_for_column_sizing.txt', False, 150, '2026-09-24', False,
                              '-rw-r--r--', 'owner', 'team')], 'Test'))


def test_reference_defaults_and_live_column_visibility(window):
    options = window.view_commands.options
    menus = options.menus
    assert [(a.text(), a.isChecked()) for a in menus['Remote columns'].actions()] == [
        ('Size', True), ('Date', True), ('Attrib', True), ('User', False), ('Group', False)]
    assert [(a.text(), a.isChecked()) for a in menus['Local columns'].actions()] == [('Size', True), ('Date', True)]
    assert [(a.text(), a.isChecked()) for a in menus['Window list columns'].actions()] == [('FTP', True), ('Action', True)]
    assert [a.text() for a in menus['Grid Lines'].actions()] == list(GRID_MODES)
    assert [a.isChecked() for a in menus['Grid Lines'].actions()] == [True, False, False, False]
    assert [(a.text(), a.isChecked()) for a in menus['Column Auto Size'].actions()] == [('Remote', True), ('Local', True), ('Queue', True)]
    pane = window.current_panes()[0]
    entries(pane)
    row = pane.tree.topLevelItem(0)
    row.setSelected(True)
    row.setCheckState(0, Qt.CheckState.Checked)
    options.actions['remote_columns/user'].trigger()
    options.actions['remote_columns/group'].trigger()
    options.actions['remote_columns/size'].trigger()
    assert pane.tree.isColumnHidden(1) and not pane.tree.isColumnHidden(4)
    assert [row.text(i) for i in (3, 4, 5)] == ['-rw-r--r--', 'owner', 'team']
    assert row.isSelected() and row.checkState(0) == Qt.CheckState.Checked
    assert window.preferences.value('Display/remote_columns/user', type=bool)
    pane.change_mode('Local')
    assert not pane.tree.isColumnHidden(1)
    assert all(pane.tree.isColumnHidden(i) for i in (3, 4, 5))
    options.actions['local_columns/date'].trigger()
    window.add_workspace()
    local = window.current_panes()[0]
    assert local.mode == 'Local' and local.tree.isColumnHidden(2)


def test_auto_size_manual_width_and_mode_restoration(window):
    options = window.view_commands.options
    pane = window.current_panes()[0]
    entries(pane)
    window.show()
    QTest.qWait(20)
    header = pane.tree.header()
    assert header.sectionResizeMode(0) == QHeaderView.ResizeMode.Interactive
    options.actions['auto_size/remote'].trigger()
    assert header.sectionResizeMode(0) == QHeaderView.ResizeMode.Interactive
    header.resizeSection(0, 333)
    assert window.preferences.value('Display/column_width/remote/0', type=int) == 333
    options.actions['auto_size/remote'].trigger()
    options.actions['auto_size/remote'].trigger()
    assert header.sectionSize(0) == 333
    pane.change_mode('Local')
    assert header.sectionResizeMode(0) == QHeaderView.ResizeMode.Interactive
    pane.change_mode('Remote')
    assert header.sectionSize(0) == 333
    options.actions['auto_size/queue'].trigger()
    assert window.queue.header().sectionResizeMode(5) == QHeaderView.ResizeMode.Interactive
    assert window.current_panes()[1].tree.header().sectionResizeMode(0) == QHeaderView.ResizeMode.Interactive


def test_workspace_columns_selection_and_live_status(window):
    options = window.view_commands.options
    window.add_workspace()
    tree = window.window_list
    assert tree.topLevelItemCount() == 2
    tree.setCurrentItem(tree.topLevelItem(0))
    assert window.tabs.currentIndex() == 0
    pane = window.current_panes()[0]
    pane.site = Site(name='Example', host='example.test')
    pane.busy = True
    options.refresh_windows()
    assert 'Example' in tree.topLevelItem(0).text(1)
    assert tree.topLevelItem(0).text(2) == 'Browsing…'
    pane.busy = False
    options.actions['windows_columns/ftp'].trigger()
    options.actions['windows_columns/action'].trigger()
    assert tree.isColumnHidden(1) and tree.isColumnHidden(2) and not tree.isColumnHidden(0)
    window.close_tab(1)
    options.refresh_windows()
    assert tree.topLevelItemCount() == 1


def test_grid_modes_are_exclusive_and_repaint_all_lists(window):
    options = window.view_commands.options
    pane = window.current_panes()[0]
    entries(pane)
    window.show()
    QTest.qWait(30)
    pictures = []
    for action in options.menus['Grid Lines'].actions():
        action.trigger()
        QTest.qWait(10)
        assert sum(a.isChecked() for a in options.grid_group.actions()) == 1
        assert window.preferences.value('Display/grid_lines') == action.text()
        pictures.append(pane.tree.viewport().grab().toImage())
    assert all(pictures[0] != picture for picture in pictures[1:])
    for tree in (pane.tree, window.queue, window.window_list):
        assert isinstance(tree.itemDelegate(), GridDelegate)


def test_listing_metadata_unix_mlsd_and_missing_facts(window, servers):
    parsed = parse_list_entry('-rw-r----- 1 alice staff 12 Sep 24 2026 sample.txt')
    assert (parsed.attributes, parsed.user, parsed.group) == ('-rw-r-----', 'alice', 'staff')
    assert parse_list_entry('09-23-26  01:30PM       456 file.txt').user == ''
    root, site = servers()
    (root / 'sample.txt').write_text('sample')
    with Connection(site, 'password') as connection:
        connection.use_mlsd = True
        connection.ftp.mlsd = lambda path: iter([
            ('owned', {'type': 'file', 'unix.mode': '0640', 'unix.ownername': 'alice', 'unix.groupname': 'staff'}),
            ('unknown', {'type': 'file'})])
        owned, unknown = connection.list('/')
    assert (owned.attributes, owned.user, owned.group) == ('0640', 'alice', 'staff')
    assert unknown.user == unknown.group == ''


def test_sftp_numeric_ownership(window):
    connection = Connection(Site(name='SSH', host='example.test'), '')
    connection.sftp = SimpleNamespace(listdir_iter=lambda path, read_aheads: [SimpleNamespace(
        filename='sample', st_mode=0o100640, st_size=2, st_mtime=0, st_uid=1001, st_gid=1002)])
    entry = connection.list('/')[0]
    assert (entry.attributes, entry.user, entry.group) == ('-rw-r-----', '1001', '1002')


def test_settings_survive_new_window_and_configuration_backup(window):
    from fluxftp.app import Window
    from fluxftp.config_backup import configuration
    options = window.view_commands.options
    options.actions['remote_columns/user'].trigger()
    options.actions['local_columns/date'].trigger()
    options.actions['windows_columns/action'].trigger()
    options.actions['auto_size/queue'].trigger()
    options.menus['Grid Lines'].actions()[2].trigger()
    window.preferences.sync()
    restored = Window()
    try:
        again = restored.view_commands.options
        assert again.actions['remote_columns/user'].isChecked()
        assert not again.actions['local_columns/date'].isChecked()
        assert not again.actions['windows_columns/action'].isChecked()
        assert not again.actions['auto_size/queue'].isChecked()
        assert again.menus['Grid Lines'].actions()[2].isChecked()
        assert not restored.current_panes()[0].tree.isColumnHidden(4)
        assert restored.window_list.isColumnHidden(2)
        backup = configuration(window.preferences, {})
        assert backup['settings']['Display/grid_lines'] == 'Vertical'
        assert backup['settings']['Display/remote_columns/user'] is True
    finally:
        restored.close()
