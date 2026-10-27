import time
from threading import Event, Thread
from types import SimpleNamespace
import pytest
from PySide6.QtCore import QTimer, Qt
from PySide6.QtTest import QTest
from fluxftp.core import Connection, Site, Entry
from fluxftp.listing_options import select_encoding
from fluxftp.directory_cache import DirectoryCache
from test_queue import window
from test_integration import servers


@pytest.mark.parametrize('mode,features,expected', [('Automatic', '211-Features\n UTF8\n211 End', 'utf-8'),
    ('Automatic', '211 End', 'cp1251'), ('cp1252', ' UTF8', 'cp1252'),
    ('shift_jis', '', 'shift_jis')])
def test_encoding_selection(mode, features, expected):
    assert select_encoding(mode, 'cp1251', Site(), features) == expected
    assert select_encoding(mode, 'cp1251', Site(force_utf8=True), features) == 'utf-8'


def test_automatic_utf8_names_roundtrip(servers, tmp_path):
    root, site = servers(False)
    (root / 'café 日本.txt').write_bytes(b'hello')
    with Connection(site, 'password', filename_encoding='Automatic') as connection:
        assert connection.ftp.encoding == 'utf-8'
        assert connection.list('/')[0].name == 'café 日本.txt'
        connection.transfer(tmp_path / 'download', '/café 日本.txt', False, Event(), progress=lambda *_: None)
        assert (tmp_path / 'download').read_bytes() == b'hello'


@pytest.mark.parametrize('encoding,name', [('cp1252', 'café €.txt'), ('cp1251', 'Москва.txt'), ('shift_jis', '日本.txt')])
@pytest.mark.parametrize('automatic', [False, True])
def test_legacy_listing_download_rename_roundtrip(tmp_path, encoding, name, automatic):
    from pyftpdlib.authorizers import DummyAuthorizer
    from pyftpdlib.handlers import FTPHandler
    from pyftpdlib.ioloop import IOLoop
    from pyftpdlib.servers import FTPServer
    root = tmp_path / 'remote'
    root.mkdir()
    (root / name).write_bytes(b'legacy bytes')
    auth = DummyAuthorizer()
    auth.add_user('user', 'password', str(root), perm='elradfmwMT')
    class Handler(FTPHandler):
        authorizer = auth
        def ftp_FEAT(self, line): self.respond('211 No UTF8 advertised')
    Handler.encoding = encoding
    loop = IOLoop()
    server = FTPServer(('127.0.0.1', 0), Handler, ioloop=loop)
    thread = Thread(target=server.serve_forever, kwargs={'timeout': .01, 'blocking': True, 'handle_exit': False}, daemon=True)
    thread.start()
    site = Site(host='127.0.0.1', port=server.socket.getsockname()[1], protocol='FTP · Unencrypted', username='user')
    try:
        with Connection(site, 'password', filename_encoding='Automatic' if automatic else encoding,
                        encoding_fallback=encoding, use_mlsd=False) as connection:
            assert connection.list('/')[0].name == name
            connection.transfer(tmp_path / 'download', '/' + name, False, Event(), lambda *_: None)
            assert (tmp_path / 'download').read_bytes() == b'legacy bytes'
            connection.ftp.rename('/' + name, '/' + name + '.renamed')
            assert connection.list('/')[0].name == name + '.renamed'
    finally:
        server.close_all()
        thread.join(3)
        loop.close()


def test_sftp_pipeline_setting():
    connection = Connection(Site(), sftp_read_aheads=64)
    calls = []
    def listing(path, read_aheads):
        calls.append((path, read_aheads))
        yield SimpleNamespace(filename='file', st_mode=0o100644, st_size=7, st_mtime=0, st_uid=1, st_gid=2)
    connection.sftp = SimpleNamespace(listdir_iter=listing)
    assert connection.list('/')[0].size == 7
    assert calls == [('/', 64)]


def test_essential_mlsd_requests_facts_once_and_keeps_size_type(servers):
    root, site = servers(False)
    (root / 'file').write_bytes(b'payload')
    (root / 'folder').mkdir()
    with Connection(site, 'password', mlsd_essential=True) as connection:
        commands = []
        send = connection.ftp.sendcmd
        def record(command):
            commands.append(command)
            return send(command)
        connection.ftp.sendcmd = record
        for _ in range(2):
            items = {entry.name: entry for entry in connection.list('/')}
            assert items['file'].size == 7 and items['folder'].is_dir
        assert commands.count('OPTS MLST type;size;modify;') == 1


def test_directory_cache_bounded_by_entries():
    cache = DirectoryCache(max_entries=3)
    cache.put('one', ([Entry('a', False)] * 2, ''), 0)
    cache.put('two', ([Entry('b', False)] * 2, ''), 0)
    assert cache.get('one') is None and len(cache.get('two')[0]) == 2
    cache.put('large', ([Entry('c', False)] * 4, ''), 0)
    assert cache.get('large') is None


def test_large_listing_batches_preserve_entries_and_server_order(window):
    pane = window.current_panes()[0]
    pane.mode, pane.site = 'Remote', Site(host='test')
    window.preferences.setValue('Connection/list_server_order', True)
    window.preferences.setValue('Connection/list_batch_size', 100)
    entries = [Entry(f'file-{i:06}', False, i) for i in range(20000, 0, -1)]
    ticks = []
    timer = QTimer(window)
    timer.timeout.connect(lambda: ticks.append(time.monotonic()))
    timer.start(10)
    pane.show_entries((entries, 'Synthetic listing'))
    assert pane.busy and pane.tree.topLevelItemCount() == 0
    deadline = time.monotonic() + 45
    while pane.busy:
        QTest.qWait(10)
        assert time.monotonic() < deadline
    timer.stop()
    assert len(ticks) > 10
    assert pane.tree.topLevelItemCount() == len(entries)
    assert pane.tree.topLevelItem(0).data(0, Qt.ItemDataRole.UserRole).name == entries[0].name
    assert pane.tree.topLevelItem(19999).data(0, Qt.ItemDataRole.UserRole).name == entries[-1].name
    assert pane.tree.isEnabled()


def test_site_listing_options_override_global_and_reject_invalid_values(window):
    import json
    from fluxftp.site_options import decode
    window.preferences.setValue('Connection/filename_encoding', 'cp1252')
    site = Site(host='test', site_options=json.dumps({'Connection/filename_encoding': 'shift_jis',
                'Connection/sftp_read_aheads': 64, 'Connection/mlsd_essential': True}))
    connection = window.new_connection(site, '')
    assert connection.filename_encoding == 'shift_jis'
    assert connection.sftp_read_aheads == 64 and connection.mlsd_essential
    for values in ({'Connection/filename_encoding': 'utf-16'}, {'Connection/sftp_read_aheads': 999},
                   {'Connection/list_batch_size': 0}):
        with pytest.raises(ValueError): decode(json.dumps(values))


def test_large_local_listing_can_be_superseded_without_stale_rows(window, tmp_path):
    large, next_folder = tmp_path / 'large', tmp_path / 'next'
    large.mkdir()
    next_folder.mkdir()
    for i in range(2001): (large / str(i)).touch()
    (next_folder / 'wanted.txt').write_text('only this directory')
    pane = window.current_panes()[0]
    pane.local_path = str(large)
    pane.change_mode('Local')
    assert pane.busy
    pane.navigate(str(next_folder))
    from test_ui import settle
    settle(window)
    assert pane.path == str(next_folder)
    assert pane.tree.topLevelItemCount() == 1
    assert pane.tree.topLevelItem(0).text(0) == 'wanted.txt'
    assert not pane.busy and pane.tree.isEnabled()
