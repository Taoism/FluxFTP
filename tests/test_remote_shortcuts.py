from dataclasses import replace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from fluxftp.core import Connection, Entry, Site, parse_list_entry
from test_integration import servers
from test_queue import window
from test_ui import settle


def test_shortcut_listing_preserves_alias_and_link_metadata(monkeypatch):
    alias = parse_list_entry('lrwxrwxrwx 1 user group 14 Sep 24 12:00 +Latest -> /archive/releases')
    regular = Entry('ordinary.bin', False, 123)
    connection = Connection(Site(), '')
    monkeypatch.setattr(connection, '_list_ftp', lambda _: [alias, regular, Entry('+Virtual', False)])
    entries = connection.list('/')
    assert entries[0] == replace(alias, is_dir=True)
    assert entries[0].name == '+Latest' and entries[0].is_link
    assert entries[1] == regular and not entries[1].is_dir
    assert entries[2].is_dir


@pytest.mark.parametrize('tls', [False, True])
def test_remote_shortcut_double_click_uses_exact_server_name(window, servers, monkeypatch, tls):
    root, site = servers(tls)
    (root / '+Latest').mkdir()
    (root / '+Latest' / 'inside.bin').write_bytes(b'contents')
    original = Connection._list_ftp
    def listing(connection, path, **kwargs):
        entries = original(connection, path, **kwargs)
        return [replace(e, is_dir=False, is_link=True) if e.name == '+Latest' else e for e in entries]
    monkeypatch.setattr(Connection, '_list_ftp', listing)
    pane = window.current_panes()[0]
    pane.set_remote(site, 'password', '/')
    pane.refresh()
    settle(window)
    row = pane.tree.topLevelItem(0)
    assert row.text(0) == '+Latest' and row.text(1) == 'Folder'
    window.show()
    QTest.qWait(20)
    QTest.mouseDClick(pane.tree.viewport(), Qt.MouseButton.LeftButton, pos=pane.tree.visualItemRect(row).center())
    settle(window)
    assert pane.path == '/+Latest'
    assert pane.tree.topLevelItem(0).data(0, Qt.ItemDataRole.UserRole).name == 'inside.bin'
    assert not window.queue_jobs


def test_local_plus_file_remains_a_file(window, tmp_path):
    (tmp_path / '+notes.txt').write_text('local file')
    pane = window.current_panes()[0]
    pane.mode = 'Local'
    pane.path = str(tmp_path)
    pane.refresh()
    row = next(pane.tree.topLevelItem(i) for i in range(pane.tree.topLevelItemCount())
               if pane.tree.topLevelItem(i).text(0) == '+notes.txt')
    assert not row.data(0, Qt.ItemDataRole.UserRole).is_dir
