from datetime import datetime, timezone
from threading import Event
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QInputDialog, QMessageBox, QFileDialog, QProgressDialog

from fluxftp.core import Connection, Site, Entry, Cancelled
from fluxftp.directory_cache import DirectoryCache
from fluxftp.remote_tasks import scan_directory, move_items
from test_queue import window
from test_integration import servers
from test_ui import settle


def connect(window, site):
    pane = window.current_panes()[0]
    pane.set_remote(site, 'password', '/')
    pane.refresh()
    settle(window)
    return pane


def select(pane, name):
    pane.tree.clearSelection()
    for i in range(pane.tree.topLevelItemCount()):
        row = pane.tree.topLevelItem(i)
        if row.data(0, Qt.ItemDataRole.UserRole).name == name:
            pane.tree.setCurrentItem(row)
            row.setSelected(True)
            return
    raise AssertionError(name)


def test_menu_reference_and_quick_dialog(window, monkeypatch):
    actions = window.ftp_menu.actions()
    assert [a.text() if not a.isSeparator() else '---' for a in actions] == [
        'Quick Connect Dialog', '---', 'Attributes(CHMOD)', 'Move to…', 'Modify Date of FTP files…',
        '---', 'Calculate FTP Space Used…', 'Export Directory Tree List…', '---', 'Find Files at Site…',
        '---', 'Clean Current Site Cache', 'Clean All Sites Cache']
    assert actions[0].shortcut().toString() == 'F11'
    assert actions[2].shortcut().toString() == 'Ctrl+O'
    assert not actions[2].isEnabled() and not actions[6].isEnabled()
    assert actions[-1].isEnabled()
    calls = []
    right = window.current_panes()[1]
    monkeypatch.setattr(right, 'connect_site', lambda: calls.append('right'))
    window.show()
    window.activateWindow()
    right.tree.setFocus()
    QTest.qWait(20)
    QTest.keyClick(right.tree, Qt.Key.Key_F11)
    assert calls == ['right']


@pytest.mark.parametrize('tls', [False, True])
def test_real_scan_move_and_date(servers, tls):
    root, site = servers(tls)
    (root / 'sub').mkdir()
    (root / 'target').mkdir()
    (root / 'sub' / 'file.txt').write_bytes(b'12345')
    stamp = datetime(2024, 2, 3, 4, 5, 6, tzinfo=timezone.utc).timestamp()
    with Connection(site, 'password') as connection:
        records = scan_directory(connection, '/', Event())
        assert sum(entry.size for _, entry in records if not entry.is_dir) == 5
        assert move_items(connection, '/sub', [Entry('file.txt', False)], '/target', Event()) == 1
        connection.set_modified('/target/file.txt', stamp)
    assert not (root / 'sub' / 'file.txt').exists()
    assert (root / 'target' / 'file.txt').read_bytes() == b'12345'
    assert abs((root / 'target' / 'file.txt').stat().st_mtime - stamp) < 2


def test_move_conflicts_preflight_cancel_and_partial_failure():
    calls = []
    connection = SimpleNamespace(list=lambda path: [Entry('taken', False)], rename=lambda *args: calls.append(args))
    with pytest.raises(ValueError, match='nothing was moved'):
        move_items(connection, '/', [Entry('first', False), Entry('taken', False)], '/dest', Event())
    assert calls == []
    with pytest.raises(ValueError, match='itself'):
        move_items(connection, '/', [Entry('folder', True)], '/folder/inside', Event())
    cancel = Event()
    cancel.set()
    with pytest.raises(Cancelled):
        move_items(connection, '/', [Entry('first', False)], '/dest', cancel)
    assert calls == []
    def rename(source, target):
        if calls:
            raise OSError('server refused')
        calls.append((source, target))
    connection.rename = rename
    with pytest.raises(RuntimeError, match='1 item.*before failure'):
        move_items(connection, '/', [Entry('first', False), Entry('second', False)], '/dest', Event())


def test_scan_links_limits_cancellation_and_permission_failure():
    visited = []
    def listing(path):
        visited.append(path)
        return [Entry('link', True, is_link=True), Entry('file', False, 3)]
    connection = SimpleNamespace(list=listing)
    assert len(scan_directory(connection, '/', Event())) == 2 and visited == ['/']
    with pytest.raises(ValueError, match='exceeded'):
        scan_directory(connection, '/', Event(), limit=1)
    cancelled = Event()
    cancelled.set()
    with pytest.raises(Cancelled):
        scan_directory(connection, '/', cancelled)
    connection.list = lambda path: (_ for _ in ()).throw(PermissionError('refused'))
    with pytest.raises(PermissionError):
        scan_directory(connection, '/', Event())


def test_chmod_and_sftp_timestamp_dispatch():
    connection = Connection(Site(), '')
    commands = []
    connection.ftp = SimpleNamespace(sendcmd=lambda command: commands.append(command))
    connection.chmod('/file with spaces', 0o755)
    assert commands == ['SITE CHMOD 0755 /file with spaces']
    with pytest.raises(ValueError):
        connection.chmod('/file\r\nDELE other', 0o755)
    with pytest.raises(ValueError):
        connection.chmod('/file', 0o10000)
    changed = []
    connection.sftp = SimpleNamespace(chmod=lambda *args: changed.append(args),
        stat=lambda path: SimpleNamespace(st_atime=123), utime=lambda *args: changed.append(args))
    connection.chmod('/file', 0o640)
    connection.set_modified('/file', 456)
    assert changed == [('/file', 0o640), ('/file', (123, 456))]


def test_working_scan_dialogs_export_and_mutation(window, servers, tmp_path, monkeypatch):
    root, site = servers()
    (root / 'sub').mkdir()
    (root / 'one.txt').write_bytes(b'1234')
    (root / 'sub' / 'two.bin').write_bytes(b'12')
    pane = connect(window, site)
    commands = window.ftp_commands
    reports = []
    monkeypatch.setattr(commands, 'report', lambda title, text: reports.append((title, text)))
    commands.scan('space')
    settle(window)
    assert '6 bytes' in reports[-1][1] and '2 files' in reports[-1][1]
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **kw: ('*.TXT', True))
    commands.scan('find')
    settle(window)
    assert '/one.txt' in reports[-1][1] and '/sub/two.bin' not in reports[-1][1]
    export = tmp_path / 'tree.txt'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **kw: (str(export), ''))
    commands.scan('export')
    settle(window)
    assert 'one.txt (4 bytes)' in export.read_text('utf-8') and 'two.bin (2 bytes)' in export.read_text('utf-8')
    select(pane, 'one.txt')
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **kw: ('/sub', True))
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **kw: QMessageBox.StandardButton.Yes)
    commands.move()
    settle(window)
    assert (root / 'sub' / 'one.txt').exists() and not (root / 'one.txt').exists()


def test_invalid_or_cancelled_chmod_and_stale_dialog_do_not_mutate(window, servers, monkeypatch):
    root, site = servers()
    (root / 'one.txt').write_bytes(b'1234')
    pane = connect(window, site)
    select(pane, 'one.txt')
    errors = []
    monkeypatch.setattr(window, 'error', errors.append)
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **kw: ('888', True))
    window.ftp_commands.chmod()
    assert errors and not pane.busy
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **kw: ('755', False))
    window.ftp_commands.chmod()
    assert not window.workers
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **kw: ('755', True))
    def changed(*args, **kwargs):
        pane.generation += 1
        return QMessageBox.StandardButton.Yes
    monkeypatch.setattr(QMessageBox, 'question', changed)
    window.ftp_commands.chmod()
    assert not window.workers


def test_directory_cache_scopes_expiry_and_inflight_clear(monkeypatch):
    cache = DirectoryCache(ttl=30, capacity=2)
    site = Site(host='example.test')
    key = cache.key(site, 'secret', '/')
    now = [1]
    monkeypatch.setattr('fluxftp.directory_cache.time.monotonic', lambda: now[0])
    original = ([Entry('one', False)], 'security')
    cache.put(key, original, cache.epoch)
    original[0].clear()
    assert cache.get(key)[0][0].name == 'one'
    assert cache.get(cache.key(site, 'different', '/')) is None
    epoch = cache.epoch
    cache.clear(site)
    cache.put(key, original, epoch)
    assert cache.get(key) is None
    cache.put(key, original, cache.epoch)
    now[0] = 32
    assert cache.get(key) is None


def test_navigation_cache_manual_refresh_and_clear(window, servers):
    root, site = servers()
    (root / 'sub').mkdir()
    pane = connect(window, site)
    pane.navigate('/sub')
    settle(window)
    (root / 'new.txt').write_text('new')
    pane.navigate('/')
    assert not pane.busy and 'cached' in pane.status.text()
    assert not any(pane.tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole).name == 'new.txt' for i in range(pane.tree.topLevelItemCount()))
    pane.refresh()
    settle(window)
    select(pane, 'new.txt')
    assert window.directory_cache.records
    window.ftp_commands.clean_cache(False)
    assert not window.directory_cache.records
    pane.refresh()
    settle(window)
    window.disconnect_pane(pane)
    assert not window.directory_cache.records


def test_cancelled_export_preserves_existing_file(window, servers, tmp_path, monkeypatch):
    root, site = servers()
    pane = connect(window, site)
    output = tmp_path / 'existing.txt'
    output.write_text('keep me')
    errors = []
    monkeypatch.setattr(window, 'error', errors.append)
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **kw: (str(output), ''))
    def scan(connection, path, cancel):
        if not cancel.wait(3):
            raise AssertionError('Cancel was not delivered')
        raise Cancelled('scan cancelled')
    monkeypatch.setattr('fluxftp.ftp_menu.scan_directory', scan)
    window.ftp_commands.scan('export')
    window.findChildren(QProgressDialog)[-1].canceled.emit()
    settle(window)
    assert output.read_text() == 'keep me' and not pane.busy
    assert errors and 'ancel' in errors[-1]
