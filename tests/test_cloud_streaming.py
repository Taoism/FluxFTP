from dataclasses import asdict
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


def test_cloud_folder_uploads_smallest_files_by_directory_and_recovers_failure(window, service, tmp_path, monkeypatch):
    remote, site = local_remote(service, tmp_path)
    source = tmp_path / 'source'
    source.mkdir()
    alpha = source / 'alpha'
    alpha.mkdir()
    zeta = source / 'zeta'
    zeta.mkdir()
    (alpha / 'alpha.nfo').write_bytes(b'a')
    (zeta / 'zeta.bin').write_bytes(b'z')
    (source / 'larger.bin').write_bytes(b'large')
    options = TransferOptions()
    attempts, uploaded = [], []
    fail_once = [True]
    class Connection(CloudConnection):
        def transfer(self, local, remote_path, upload, cancel, progress, resume=False):
            relative = Path(local).relative_to(source).as_posix()
            attempts.append(relative)
            if relative == 'zeta/zeta.bin' and fail_once[0]:
                fail_once[0] = False
                raise OSError('Simulated lost connection')
            result = super().transfer(local, remote_path, upload, cancel, progress, resume)
            uploaded.append(relative)
            return result
    monkeypatch.setattr(window, 'new_connection', lambda selected, password='', **kwargs: Connection(selected, service))
    window.initialize_queue_storage(tmp_path / 'queue.json')
    window.preferences.setValue('Queue/cycle_retry', False)
    window.preferences.setValue('Queue/retry_after', False)
    window.preferences.setValue('Transfer/per_tab', 1)
    saved = [descriptor(tmp_path, src=str(source), dst='/target', name='source', directory=True,
                        destination_site=asdict(site), state='active')]
    def drain():
        deadline = time.monotonic() + 60
        while window.workers or window.active or window.pending:
            QTest.qWait(20)
            assert time.monotonic() < deadline, window.log.toPlainText()
    window.restore_queue_items(saved)
    folder_job = window.queue_jobs[-1]
    folder_job.update(needs_credentials=False, options=options)
    assert not folder_job['streaming_folder']
    window.paused = False
    window.start_next()
    drain()
    expected = ['alpha/alpha.nfo', 'zeta/zeta.bin', 'larger.bin']
    assert attempts == expected
    assert uploaded == ['alpha/alpha.nfo', 'larger.bin']
    recovered = read_queue(window.queue_file)
    assert any(Path(item['src']).relative_to(source).as_posix() == 'zeta/zeta.bin' for item in recovered)
    failed = next(job for job in window.queue_jobs if job['state'] == 'failed')
    assert Path(failed['src']).relative_to(source).as_posix() == 'zeta/zeta.bin'
    window.reset_failed_jobs()
    window.play_queue()
    drain()
    assert attempts == expected + ['zeta/zeta.bin']
    assert (remote / 'target' / 'alpha' / 'alpha.nfo').read_bytes() == b'a'
    assert (remote / 'target' / 'zeta' / 'zeta.bin').read_bytes() == b'z'
    assert (remote / 'target' / 'larger.bin').read_bytes() == b'large'
    assert not window.pending and read_queue(window.queue_file) == []


def test_noncloud_folder_keeps_buffered_planning(window, tmp_path):
    window.restore_queue_items([descriptor(tmp_path, directory=True)])
    assert not window.queue_jobs[-1]['streaming_folder']


def test_pause_after_folder_scan_leaves_planned_cloud_files_queued(window, tmp_path, monkeypatch):
    from uuid import uuid4
    from fluxftp.core import Site
    import fluxftp.queue_manager as module
    import fluxftp.site_connections as site_connections
    identity = uuid4().hex
    site = Site(name='Cloud test', host='cloud-' + identity, protocol='Google Drive', cloud_account=identity, username='')
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'first.bin').write_bytes(b'1')
    (source / 'second.bin').write_bytes(b'22')
    transferred = []
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def transfer(self, local, remote_path, upload, cancel, progress, resume=False):
            transferred.append(Path(local).name)
            progress(Path(local).stat().st_size, Path(local).stat().st_size)
            return True
    monkeypatch.setattr(site_connections, 'transfer_connection', lambda *args, **kwargs: Connection())
    scans = []
    def scan(source_connection, destination, src, dst, is_dir, cancel, progress, resume=False, **kwargs):
        planned = kwargs.get('planned')
        if planned is None:
            return destination.transfer(src, dst, True, cancel, progress, resume)
        scans.append(True)
        planned.append((str(source / 'first.bin'), '/target/first.bin', 1))
        planned.append((str(source / 'second.bin'), '/target/second.bin', 2))
        window.paused = True
        return True
    monkeypatch.setattr(module, 'transfer_tree', scan)
    window.initialize_queue_storage(tmp_path / 'queue.json')
    window.restore_queue_items([descriptor(tmp_path, src=str(source), directory=True, destination_site=asdict(site))])
    job = window.queue_jobs[-1]
    job['needs_credentials'] = False
    window.paused = False
    window.start_next()
    deadline = time.monotonic() + 10
    while window.workers or window.active:
        QTest.qWait(10)
        assert time.monotonic() < deadline
    assert scans and window.paused and len(window.pending) == 2
    assert [Path(item['src']).name for item in window.pending] == ['first.bin', 'second.bin']
    assert transferred == []
    assert window.save_queue_state()
    assert {Path(item['src']).name for item in read_queue(window.queue_file)} >= {'first.bin', 'second.bin'}
    window.play_queue()
    deadline = time.monotonic() + 10
    while window.workers or window.active or window.pending:
        QTest.qWait(10)
        assert time.monotonic() < deadline
    assert transferred == ['first.bin', 'second.bin']
