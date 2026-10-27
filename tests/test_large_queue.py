from threading import Event

import pytest

from fluxftp.core import Cancelled, Entry, transfer_tree
from fluxftp.queue_manager import read_queue, write_queue
from fluxftp.transfer_options import TransferOptions
from test_queue import descriptor


def test_large_nested_cloud_folder_plans_every_file(tmp_path):
    class Cloud:
        is_cloud = True

        def list(self, path):
            if path == '/root':
                return [Entry('one', True), Entry('two', True)]
            return [Entry(f'{i:05}.bin', False, i) for i in range(6001)]

    planned = []
    transfer_tree(Cloud(), None, '/root', str(tmp_path / 'target'), True,
                  Event(), lambda *_: None, options=TransferOptions(), planned=planned)
    assert len(planned) == 12002
    assert len({src for src, _, _ in planned}) == 12002
    assert planned[-1][0] == '/root/two/06000.bin'
    assert planned[-1][2] == 6000
    assert not list((tmp_path / 'target').rglob('*.bin'))


def test_large_queue_roundtrip_preserves_order_and_paths(tmp_path):
    # Long remote paths push the snapshot past the former 16 MiB limit too.
    jobs = [descriptor(tmp_path, name=f'{i}.bin', src='/' + 'folder/' * 200 + f'{i}.bin',
                       manual_order=i) for i in range(12002)]
    path = tmp_path / 'queue.json'
    write_queue(path, jobs)
    saved = read_queue(path)
    assert len(saved) == len(jobs)
    assert [job['src'] for job in saved] == [job['src'] for job in jobs]
    assert saved[-1]['manual_order'] == 12001


def test_large_scan_remains_cancellable(tmp_path):
    cancel = Event()

    class Cloud:
        def list(self, path):
            cancel.set()
            return [Entry(f'{i}.bin', False, 1) for i in range(12002)]

    planned = []
    with pytest.raises(Cancelled):
        transfer_tree(Cloud(), None, '/root', str(tmp_path / 'target'), True,
                      cancel, lambda *_: None, options=TransferOptions(), planned=planned)
    assert planned == []
