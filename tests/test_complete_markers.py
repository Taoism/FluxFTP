from threading import Event

import pytest

from fluxftp.core import Connection, transfer_tree
from fluxftp.transfer_options import TransferOptions
from test_integration import servers


@pytest.mark.parametrize('tls', [False, True])
def test_only_empty_remote_complete_markers_are_skipped(servers, tmp_path, tls):
    root, site = servers(tls)
    release = root / 'release'
    release.mkdir()
    (release / '- COMPLETE -').mkdir()
    (release / 'music - complete -').mkdir()
    (release / 'music - complete -' / 'song.bin').write_bytes(b'unchanged\x00\xff')
    (release / 'extras').mkdir()
    target = tmp_path / 'download'
    skipped = []
    skipped_directories = set()
    options = TransferOptions(skip_enabled=False)
    with Connection(site, 'password', transfer_options=options) as source:
        transfer_tree(source, None, '/release', str(target), True, Event(), lambda *_: None,
                      options=options, skipped=lambda *args: skipped.append(args), skipped_directories=skipped_directories)
    assert not (target / '- COMPLETE -').exists()
    assert (target / 'music - complete -' / 'song.bin').read_bytes() == b'unchanged\x00\xff'
    assert (target / 'extras').is_dir()
    assert skipped == [('/release/- COMPLETE -', 'Empty COMPLETE marker folder')]
    assert skipped_directories == {'/release/- COMPLETE -'}
