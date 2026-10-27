import os
from threading import Event

import pytest

from fluxftp.core import Connection, parse_list_entry, safe_name, safe_remote_name, transfer_tree
from fluxftp.transfer_options import TransferOptions
from test_integration import servers


MARKER = '[#:::::::::::::]-  9% Complete'
ROW = 'drwxr-xr-x 1 server group 0 Oct 27 12:00 ' + MARKER


def test_remote_listing_preserves_progress_marker():
    entry = parse_list_entry(ROW)
    assert entry.name == MARKER and entry.is_dir
    assert safe_remote_name(MARKER) == MARKER
    if os.name == 'nt':
        with pytest.raises(ValueError, match='Unsupported Windows file name'):
            safe_name(MARKER)


@pytest.mark.parametrize('name', ['../escape', 'nested/file', 'nested\\file', 'bad\rname', 'bad\nname', 'bad\x00name'])
def test_remote_names_still_reject_unsafe_components(name):
    with pytest.raises(ValueError):
        safe_remote_name(name)


@pytest.mark.parametrize('tls', [False, True])
def test_upload_conflict_listing_with_server_marker(servers, tmp_path, tls):
    root, site = servers(tls, no_mlsd=True)
    source = tmp_path / 'archive.r06'
    source.write_bytes(b'archive payload\x00\xff' * 100)
    options = TransferOptions(exists_enabled=True, upload_smaller='Auto Resume')
    (root / source.name).write_bytes(source.read_bytes()[:5])
    with Connection(site, 'password', transfer_options=options, use_mlsd=False) as connection:
        original = connection.ftp.retrlines
        def listing(command, callback):
            result = original(command, callback)
            if command.startswith('LIST'):
                callback(ROW)
            return result
        connection.ftp.retrlines = listing
        assert MARKER in {entry.name for entry in connection.list('/')}
        transfer_tree(None, connection, str(source), '/' + source.name, False,
                      Event(), lambda *_: None, options=options)
        assert connection.ftp.pwd() == '/'
    assert (root / source.name).read_bytes() == source.read_bytes()
