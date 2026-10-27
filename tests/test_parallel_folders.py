from dataclasses import replace
from threading import Event, Lock

import pytest

from fluxftp.core import Connection
from fluxftp.transfer_options import TransferOptions
from test_integration import servers
from test_queue import window, job_for
from test_folder_sync import wait_idle as settle


@pytest.mark.parametrize('tls', [False, True])
@pytest.mark.parametrize('upload', [False, True])
def test_folder_files_have_parallel_logins_and_queue_rows(window, servers, tmp_path, monkeypatch, tls, upload):
    # Keep completed rows for these concurrency/reporting assertions. Automatic
    # download cleanup has separate coverage in test_window_positions.py.
    monkeypatch.setattr(window, 'clear_finished_download', lambda job: None)
    root, site = servers(tls)
    site = replace(site, max_transfers_total=2, max_downloads=2, max_uploads=2)
    local = tmp_path / 'local'
    source = local if upload else root / 'folder'
    source.mkdir()
    (source / 'nested').mkdir()
    payload = bytes(range(256)) * 64
    for name in ('a.bin', 'b.bin', 'nested/c.bin'):
        (source / name).write_bytes(payload)
    lock, overlap = Lock(), Event()
    state = dict(active=0, peak=0, calls=0)
    logins = []
    original = Connection.transfer
    def transfer(connection, *args, **kwargs):
        with lock:
            logins.append(connection)
            state['active'] += 1
            state['calls'] += 1
            state['peak'] = max(state['peak'], state['active'])
            if state['active'] == 2: overlap.set()
        try:
            assert overlap.wait(5), 'Files did not run concurrently'
            return original(connection, *args, **kwargs)
        finally:
            with lock: state['active'] -= 1
    monkeypatch.setattr(Connection, 'transfer', transfer)
    parent = job_for(window, None, 'Upload' if upload else 'Download', 'folder')
    parent.update(source_site=None if upload else site, destination_site=site if upload else None,
                  source_password='password', destination_password='password',
                  src=str(local) if upload else '/folder', dst='/received' if upload else str(local),
                  name='folder', directory=True, resume=False, options=TransferOptions(), held=False)
    parent['task'] = window.make_queue_task(parent)
    window.start_next()
    settle(window)
    assert parent['state'] == 'complete'
    children = [job for job in window.queue_jobs if job is not parent]
    assert len(children) == 3
    assert all(job['state'] == 'complete' and not job['directory'] for job in children)
    assert state['peak'] == 2 and state['calls'] == 3
    assert len({id(connection) for connection in logins}) == 3
    target = root / 'received' if upload else local
    assert all((target / name).read_bytes() == payload for name in ('a.bin', 'b.bin', 'nested/c.bin'))
    assert '3/3 files' in parent['row'].text(7)
    assert parent['row'].text(1) == '100%'
    assert parent['row'].childCount() == 3
    log = window.transfer_log.toPlainText()
    assert 'a.bin' in log and 'b.bin' in log and 'c.bin' in log
    assert 'transferred 3 file(s)' in log and '49152 bytes' in log


def test_cancelled_scan_does_not_enqueue_files(window):
    parent = job_for(window, lambda _: {'planned': [('/folder/file', 'target', 1)]})
    parent['suppress_retry'] = True
    window.start_next()
    settle(window)
    assert parent['state'] == 'cancelled'
    assert window.queue_jobs == [parent]


def test_same_destination_cannot_run_concurrently(window, tmp_path):
    release = Event()
    first = job_for(window, lambda _: release.wait(5), name='first')
    second = job_for(window, lambda _: None, name='second')
    first['dst'] = second['dst'] = str(tmp_path / 'same.bin')
    try:
        window.start_next()
        assert len(window.active) == 1
        assert window.pending == [second]
    finally:
        release.set()
    settle(window)
    assert first['state'] == second['state'] == 'complete'
