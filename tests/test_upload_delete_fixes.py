import ftplib
import io
from threading import Event
from types import SimpleNamespace

import pytest

from fluxftp.core import Connection, Entry, remove_remote_items
from fluxftp.transfer_options import TransferOptions
from test_integration import servers
from test_queue import window


@pytest.mark.parametrize('tls', [False, True])
@pytest.mark.parametrize('extension', ['nfo', 'sfv', 'm3u'])
def test_ascii_refusal_retries_binary_byte_for_byte(servers, tmp_path, tls, extension):
    root, site = servers(tls, reject_ascii_upload=True)
    source = tmp_path / ('file.' + extension)
    source.write_bytes(b'line one\nline two\r\nlegacy byte \xff\n')
    events = []
    with Connection(site, 'password', transfer_options=TransferOptions(mode='Auto', ascii_extensions='.nfo;.sfv;.m3u')) as connection:
        connection.trace.listener = lambda text, level: events.append(text)
        connection.transfer(source, '/' + source.name, True, Event(), lambda *_: None)
        assert connection.ftp.pwd() == '/'
    assert (root / source.name).read_bytes() == source.read_bytes()
    assert any('retrying this file in Binary mode' in event for event in events)


@pytest.mark.parametrize('message,append', [
    ('550 Permission denied', False),
    ('553 File already exists', False),
    ('553 Error: upload in ASCII mode is not allowed!', True),
])
def test_no_binary_retry_for_unrelated_refusal_or_append(message, append):
    def refuse(command):
        raise ftplib.error_perm(message)
    connection = Connection.__new__(Connection)
    connection.ftp = SimpleNamespace(voidcmd=lambda command: None, transfercmd=refuse)
    with pytest.raises(ftplib.error_perm) as error:
        connection._ascii_upload(io.BytesIO(b'content'), '/file', lambda *_: None, append=append)
    assert type(error.value) is ftplib.error_perm


@pytest.mark.parametrize('tls', [False, True])
@pytest.mark.parametrize('legacy_listing', [False, True])
def test_delete_nonempty_remote_tree_preserves_siblings(servers, tls, legacy_listing):
    root, site = servers(tls, no_mlsd=legacy_listing)
    (root / 'selected' / 'nested').mkdir(parents=True)
    (root / 'selected' / 'nested' / 'file.nfo').write_bytes(b'nfo')
    (root / 'selected' / 'file.sfv').write_bytes(b'sfv')
    (root / 'untouched').write_bytes(b'keep')
    with Connection(site, 'password') as connection:
        remove_remote_items(connection, [('/selected', True)])
    assert not (root / 'selected').exists()
    assert (root / 'untouched').read_bytes() == b'keep'


@pytest.mark.parametrize('entry', [Entry('../outside', False), Entry('link', True, is_link=True)])
def test_delete_preflights_before_any_mutation(entry):
    removed = []
    connection = SimpleNamespace(list=lambda path: [Entry('valid', False), entry],
                                 remove=lambda *args: removed.append(args))
    with pytest.raises(ValueError):
        remove_remote_items(connection, [('/selected', True)])
    assert removed == []


@pytest.mark.parametrize('path', ['/', '/folder/..', '.', 'relative'])
def test_delete_refuses_root_and_relative_paths(path):
    with pytest.raises(ValueError):
        remove_remote_items(None, [(path, True)])


def test_delete_refuses_directory_shortcut():
    with pytest.raises(ValueError, match='shortcuts'):
        remove_remote_items(None, [('/+shortcut', True)])


def test_right_click_delete_confirms_contents_and_calls_recursive_helper(window, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from fluxftp import core
    pane = window.tabs.currentWidget().widget(0)
    pane.mode = 'Remote'
    monkeypatch.setattr(pane, 'selected', lambda: [Entry('folder', True)])
    monkeypatch.setattr(pane, 'join', lambda name: '/' + name)
    calls, prompts = [], []
    monkeypatch.setattr(core, 'remove_remote_items', lambda connection, paths: calls.append(paths))
    monkeypatch.setattr(pane, 'mutate', lambda remote, local: remote(None))
    def confirm(parent, title, message):
        prompts.append(message)
        return QMessageBox.StandardButton.No
    monkeypatch.setattr(QMessageBox, 'question', confirm)
    pane.delete()
    assert not calls
    assert 'all their contents' in prompts[0]
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.StandardButton.Yes)
    pane.delete()
    assert calls == [[('/folder', True)]]
