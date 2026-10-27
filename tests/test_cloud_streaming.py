from dataclasses import asdict, replace
from pathlib import Path
import time
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from fluxftp.cloud_connection import CloudConnection
from fluxftp.queue_manager import read_queue
from fluxftp.transfer_options import TransferOptions
from test_cloud import service, local_remote
from test_queue import window, descriptor
from test_ui import settle


def test_cloud_folder_uploads_before_descending_and_recovers_unvisited_files(window, service, tmp_path, monkeypatch):
    remote, site = local_remote(service, tmp_path)
    source = tmp_path / 'source'
    source.mkdir()
    first = source / 'first.nfo'
    first.write_bytes(b'first file')
    later = source / 'later'
    later.mkdir()
    (later / 'second.bin').write_bytes(b'second file')
    (later / 'third.bin').write_bytes(b'third file')
    options = replace(TransferOptions(), exists_enabled=True)
    uploaded, snapshots = [], []
    fail_once = [True]
    original_iterdir = Path.iterdir
    def iterdir(path):
        if path == later:
            assert (remote / 'target' / 'first.nfo').read_bytes() == first.read_bytes()
        return original_iterdir(path)
    monkeypatch.setattr(Path, 'iterdir', iterdir)
    class Connection(CloudConnection):
        def transfer(self, local, remote_path, upload, cancel, progress, resume=False):
            snapshots.append(read_queue(window.queue_file))
            assert snapshots[-1][0]['directory']
            assert snapshots[-1][0]['src'] == str(source)
            if Path(local).name == 'second.bin' and fail_once[0]:
                fail_once[0] = False
                raise OSError('Simulated lost connection')
            result = super().transfer(local, remote_path, upload, cancel, progress, resume)
            uploaded.append(Path(local).name)
            return result
    monkeypatch.setattr(window, 'new_connection', lambda selected, password='': Connection(selected, service))
    window.initialize_queue_storage(tmp_path / 'queue.json')
    window.preferences.setValue('Queue/cycle_retry', False)
    window.preferences.setValue('Queue/retry_after', False)
    saved = [descriptor(tmp_path, src=str(source), dst='/target', name='source', directory=True,
                        destination_site=asdict(site), state='active')]
    def start(items):
        window.restore_queue_items(items)
        job = window.queue_jobs[-1]
        job.update(needs_credentials=False, options=options)
        assert job['streaming_folder']
        window.paused = False
        window.start_next()
        deadline = time.monotonic() + 60
        while window.workers or window.active or window.pending:
            QTest.qWait(20)
            assert time.monotonic() < deadline, (job['state'], job['row'].text(7), window.log.toPlainText())
        return job
    first_job = start(saved)
    assert first_job['state'] == 'failed'
    assert uploaded == ['first.nfo']
    recovered = read_queue(window.queue_file)
    assert len(recovered) == 1 and recovered[0]['directory']
    recovered[0]['state'] = 'active'
    window.discard_queue_jobs(list(window.queue_jobs))
    second_job = start(recovered)
    assert second_job['state'] == 'complete'
    assert uploaded.count('first.nfo') == 1
    assert set(uploaded) == {'first.nfo', 'second.bin', 'third.bin'}
    assert (remote / 'target' / 'later' / 'third.bin').read_bytes() == b'third file'
    transferred = len(b'second file') + len(b'third file')
    assert second_job['row'].data(3, Qt.ItemDataRole.UserRole) == [transferred, 0]
    assert second_job['row'].text(3) == window.file_size(transferred)
    assert not window.pending and read_queue(window.queue_file) == []


def test_noncloud_folder_keeps_buffered_planning(window, tmp_path):
    window.restore_queue_items([descriptor(tmp_path, directory=True)])
    assert not window.queue_jobs[-1]['streaming_folder']


def test_pause_returns_cloud_folder_to_recoverable_queue_before_next_file(window, tmp_path, monkeypatch):
    from uuid import uuid4
    from fluxftp.core import Site
    import fluxftp.queue_manager as module
    identity = uuid4().hex
    site = Site(name='Cloud test', host='cloud-' + identity, protocol='Google Drive', cloud_account=identity, username='')
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *_): pass
    monkeypatch.setattr(window, 'new_connection', lambda *args: Connection())
    calls = []
    def transfer(*args, file_started, **kwargs):
        calls.append(True)
        if len(calls) == 1:
            file_started('first.bin', '/first.bin')
            window.paused = True  # Pause arriving while the first file finishes.
            file_started('second.bin', '/second.bin')
            raise AssertionError('The second file must not start while paused')
        file_started('second.bin', '/second.bin')
        return True
    monkeypatch.setattr(module, 'transfer_tree', transfer)
    window.initialize_queue_storage(tmp_path / 'queue.json')
    window.restore_queue_items([descriptor(tmp_path, directory=True, destination_site=asdict(site))])
    job = window.queue_jobs[-1]
    job['needs_credentials'] = False
    window.paused = False
    window.start_next()
    deadline = time.monotonic() + 10
    while window.workers or window.active:
        QTest.qWait(10)
        assert time.monotonic() < deadline
    assert window.paused and window.pending == [job] and job['state'] == 'queued'
    from fluxftp.queue_tree import PROGRESS_ROLE
    assert job['row'].data(1, PROGRESS_ROLE) is None
    assert job['row'].text(1) == 'Paused'
    assert window.save_queue_state()
    assert read_queue(window.queue_file)[0]['directory']
    assert job['retries'] == 0
    window.play_queue()
    settle(window)
    assert job['state'] == 'complete' and len(calls) == 2
    assert job['row'].data(1, PROGRESS_ROLE) == 100
