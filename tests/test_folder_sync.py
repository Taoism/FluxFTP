import os
import json
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from dataclasses import replace

import pytest

from fluxftp.folder_sync import scan, compare, execute_sync, validate_spec, check_roots, stamp
from fluxftp.core import Cancelled, Entry
from fluxftp.transfer_options import TransferOptions
from fluxftp.sync_dialog import SyncDialog
from test_queue import window
from test_mouse import local_pair
from test_ui import settle
from test_integration import servers


def wait_idle(window, timeout=45):
    import time
    from PySide6.QtTest import QTest
    deadline = time.monotonic() + timeout
    while window.workers or window.active or window.pending:
        QTest.qWait(10)
        if not (window.workers or window.active or window.pending): break
        assert time.monotonic() < deadline, 'Synchronization did not finish'


def info(size=1, modified='1000000000', directory=False, link=False):
    return dict(size=size if not directory else 0, modified=modified if not directory else '', directory=directory, link=link)


def test_comparison_rules_directions_and_conflicts():
    a = {'same': info(), 'different': info(2), 'left': info(), 'unknown': info(modified='Jan 1 12:30'),
         'type': info(directory=True), 'type/child': info(), 'link': info(link=True), 'A': info()}
    b = {'same': info(modified='1000000001'), 'different': info(3), 'right': info(),
         'unknown': info(modified='Jan 1 12:30'), 'type': info(), 'a': info()}
    rows = {row['path']: row for row in compare(a, b, 'Two-way')}
    assert rows['same']['action'] == 'Skip' and rows['same']['reason'].startswith('Same')
    assert rows['left']['action'] == 'Left → Right' and rows['right']['action'] == 'Right → Left'
    assert rows['different']['choices'] == ['Skip', 'Left → Right', 'Right → Left']
    assert rows['unknown']['reason'] == 'Conflict: unknown timestamp'
    for key in ('type', 'type/child', 'link', 'A', 'a'):
        assert rows[key]['choices'] == ['Skip'] and rows[key]['reason'].startswith('Blocked')
    assert next(r for r in compare(a, b, rule='Size only') if r['path'] == 'unknown')['reason'].startswith('Same')
    assert next(r for r in compare(a, b, mode='Right → Left') if r['path'] == 'left')['action'] == 'Skip'


def test_scan_limit_cancel_and_roots(tmp_path):
    (tmp_path / 'file').write_bytes(b'x')
    with pytest.raises(ValueError, match='exceeds'): scan(None, str(tmp_path), Event(), limit=0)
    cancel = Event()
    cancel.set()
    with pytest.raises(Cancelled): scan(None, str(tmp_path), cancel)
    with pytest.raises(ValueError, match='separate'): check_roots(None, None, str(tmp_path), str(tmp_path / 'nested'))
    assert stamp('20260101120000') == stamp('2026-01-01T12:00:00Z')
    assert stamp('Jan 1 12:30') is None
    assert stamp('2026-01-01T12:00:00') is None


def prepare(window, tmp_path):
    left, right, source, target = local_pair(window, tmp_path)
    dialog = SyncDialog(window, [left, right], [None, None])
    dialog.listings = [scan(None, str(path), Event()) for path in (source, target)]
    dialog.recompare()
    return dialog, source, target


def test_preview_is_read_only_and_queues_recursive_copy_with_history(window, tmp_path):
    dialog, source, target = prepare(window, tmp_path)
    (source / 'folder').mkdir()
    (source / 'folder' / 'file.bin').write_bytes(b'copy me')
    (source / 'empty').mkdir()
    (target / 'keep.bin').write_bytes(b'keep')
    dialog.listings = [scan(None, str(path), Event()) for path in (source, target)]
    dialog.recompare()
    assert not (target / 'folder').exists()
    dialog.enqueue()
    assert len(window.pending) == 3 and all(j['held'] for j in window.pending)
    assert not (target / 'folder').exists()
    window.play_queue()
    settle(window)
    assert (target / 'folder' / 'file.bin').read_bytes() == b'copy me'
    assert (target / 'empty').is_dir() and (target / 'keep.bin').read_bytes() == b'keep'
    assert window.transfer_history.store.page()[1] == 3


@pytest.mark.parametrize('changed', ['source', 'destination', 'type'])
def test_stale_preview_fails_without_overwriting(window, tmp_path, changed):
    dialog, source, target = prepare(window, tmp_path)
    (source / 'file').write_bytes(b'new')
    (target / 'file').write_bytes(b'old data')
    dialog.listings = [scan(None, str(path), Event()) for path in (source, target)]
    dialog.recompare()
    dialog.plan[0]['action'] = 'Left → Right'
    dialog.enqueue()
    if changed == 'source': (source / 'file').write_bytes(b'changed source')
    elif changed == 'destination': (target / 'file').write_bytes(b'changed destination')
    else:
        (target / 'file').unlink()
        (target / 'file').mkdir()
    window.play_queue()
    settle(window)
    assert window.queue_jobs[0]['state'] == 'failed'
    assert window.queue_jobs[0].get('retries', 0) == 0
    assert (target / 'file').is_dir() if changed == 'type' else (target / 'file').read_bytes() == (b'changed destination' if changed == 'destination' else b'old data')
    assert 'changed since' in window.queue_jobs[0]['row'].toolTip(7)


def test_two_way_conflicts_are_explicit_and_recovery_keeps_guard(window, tmp_path):
    from fluxftp.queue_manager import read_queue, write_queue
    dialog, source, target = prepare(window, tmp_path)
    (source / 'same-name').write_bytes(b'left wins')
    (target / 'same-name').write_bytes(b'right')
    (target / 'right-only').write_bytes(b'right only')
    dialog.listings = [scan(None, str(path), Event()) for path in (source, target)]
    dialog.mode.setCurrentText('Two-way')
    conflict = next(r for r in dialog.plan if r['path'] == 'same-name')
    assert conflict['action'] == 'Skip'
    dialog.conflicts.setCurrentText('Left → Right')
    dialog.resolve_conflicts()
    dialog.enqueue()
    path = tmp_path / 'sync-queue.json'
    write_queue(path, window.queue_snapshot())
    saved = read_queue(path)
    assert all('sync' in item for item in saved)
    window.discard_queue_jobs(list(window.queue_jobs))
    window.restore_queue_items(saved)
    window.play_queue()
    settle(window)
    assert (target / 'same-name').read_bytes() == b'left wins'
    assert (source / 'right-only').read_bytes() == b'right only'
    next_plan = compare(*(scan(None, str(p), Event()) for p in (source, target)))
    assert all(row['action'] == 'Skip' and row['reason'].startswith('Same') for row in next_plan)


def test_guard_metadata_and_path_escape_rejected(tmp_path):
    source, target = tmp_path / 'source', tmp_path / 'target'
    source.mkdir(); target.mkdir()
    (source / 'file').write_bytes(b'x')
    original = scan(None, str(source), Event())['file']
    spec = dict(source_root=str(source), destination_root=str(target), relative='file', source=original, destination=None)
    with pytest.raises(ValueError): validate_spec(dict(spec, relative='../escape'))
    with pytest.raises(ValueError): validate_spec(dict(spec, source=dict(original, link=True)))
    job = dict(src=str(source / 'file'), dst=str(tmp_path / 'escape'), directory=False,
               source_site=None, destination_site=None, sync=spec, options=TransferOptions())
    worker = SimpleNamespace(cancel=Event(), report=lambda *_: None)
    with pytest.raises(ValueError, match='paths no longer'): execute_sync(None, None, job, worker)
    assert not (tmp_path / 'escape').exists()


def test_real_preview_worker_and_paged_actions(window, tmp_path):
    dialog, source, target = prepare(window, tmp_path)
    for index in range(210): (source / str(index)).write_bytes(b'x')
    dialog.start_scan()
    wait_idle(window)
    assert len(dialog.plan) == 210 and dialog.tree.topLevelItemCount() == 200
    dialog.plan[0]['action'] = 'Skip'
    dialog.page(200)
    assert dialog.tree.topLevelItemCount() == 10
    dialog.page(-200)
    assert dialog.plan[0]['action'] == 'Skip'
    assert not list(target.iterdir())
    dialog.close()


def test_buffered_recovery_preserves_each_sync_fingerprint(window, tmp_path):
    from fluxftp.queue_manager import write_queue, read_queue
    dialog, source, target = prepare(window, tmp_path)
    for index in range(270): (source / str(index)).write_bytes(str(index).encode())
    dialog.listings = [scan(None, str(path), Event()) for path in (source, target)]
    dialog.recompare()
    dialog.enqueue()
    assert len(window.queue_jobs) == 1 and window.queue_jobs[0]['buffered']
    path = tmp_path / 'buffered.json'
    write_queue(path, window.queue_snapshot())
    saved = read_queue(path)
    assert len(saved) == 270 and len({item['sync']['relative'] for item in saved}) == 270
    window.discard_queue_jobs(list(window.queue_jobs))
    window.restore_queue_items(saved)
    window.play_queue()
    # This checks durable recovery and contents, not transfer throughput.
    # Hosted Windows runners can need more than 45 seconds for 270 jobs,
    # each with queue/history writes. Keep the wait bounded without imposing
    # a workstation-speed requirement on the shared CI runner.
    wait_idle(window, timeout=180)
    assert len(list(target.iterdir())) == 270
    assert all(p.read_bytes() == (target / p.name).read_bytes() for p in source.iterdir())


def test_remote_symlink_root_is_blocked():
    connection = SimpleNamespace(list=lambda _: [Entry('alias', True, is_link=True)])
    with pytest.raises(ValueError, match='regular folders'): scan(connection, '/alias', Event())


def test_preview_cancellation_and_changed_pane_guard(window, tmp_path, monkeypatch):
    from PySide6.QtCore import QTimer
    import fluxftp.sync_dialog as module
    dialog, source, target = prepare(window, tmp_path)
    def slow_scan(connection, root, cancel, **kwargs):
        cancel.wait(2)
        raise Cancelled('Synchronization scan cancelled')
    monkeypatch.setattr(module, 'scan', slow_scan)
    dialog.start_scan()
    QTimer.singleShot(20, dialog.reject)
    wait_idle(window)
    assert not dialog.scanning and dialog.close_when_done and not window.queue_jobs
    assert not list(target.iterdir())


def test_unknown_newer_conflict_stays_skipped_and_changed_pane_cannot_queue(window, tmp_path):
    dialog, source, target = prepare(window, tmp_path)
    dialog.listings = [{'file': info(modified='unknown')}, {'file': info(2, modified='unknown')}]
    dialog.mode.setCurrentText('Two-way')
    dialog.conflicts.setCurrentText('Newer file wins')
    dialog.resolve_conflicts()
    assert dialog.plan[0]['action'] == 'Skip'
    dialog.plan[0]['action'] = 'Left → Right'
    dialog.panes[0].generation += 1
    dialog.enqueue()
    assert not window.queue_jobs and 'panes changed' in dialog.status.text()
    dialog.close()


@pytest.mark.parametrize('tls', [False, True])
@pytest.mark.parametrize('upload', [False, True])
def test_ftp_ftps_sync_through_queue(window, tmp_path, servers, tls, upload):
    from dataclasses import asdict
    root, site = servers(tls)
    local = tmp_path / 'local'
    local.mkdir()
    (root / 'remote').mkdir()
    source = local if upload else root / 'remote'
    (source / 'nested').mkdir()
    (source / 'nested' / 'file.bin').write_bytes(b'remote sync content')
    os.utime(source / 'nested' / 'file.bin', (1600000000, 1600000000))
    with window.new_connection(site, 'password') as connection:
        remote_listing = scan(connection, '/remote', Event())
    local_listing = scan(None, str(local), Event())
    left, right = (local_listing, remote_listing) if upload else (remote_listing, local_listing)
    items = []
    for row in compare(left, right):
        if row['action'] == 'Skip': continue
        source_root, destination_root = (str(local), '/remote') if upload else ('/remote', str(local))
        items.append(dict(src=str(source / row['path']) if upload else '/remote/' + row['path'],
            dst='/remote/' + row['path'] if upload else str(local / row['path']),
            name=Path(row['path']).name, directory=row['left']['directory'], size=row['left']['size'],
            resume=False, state='queued', workspace=0, held=True,
            source_site=None if upload else asdict(site), destination_site=asdict(site) if upload else None,
            sync=dict(source_root=source_root, destination_root=destination_root, relative=row['path'],
                      source=row['left'], destination=row['right'])))
    window.restore_queue_items(items)
    for job in window.queue_jobs:
        job.update(source_password='password', destination_password='password', needs_credentials=False)
    window.preferences.setValue('Queue/cycle_retry', False)
    window.preferences.setValue('Queue/retry_after', False)
    window.play_queue()
    wait_idle(window)
    destination = root / 'remote' if upload else local
    assert (destination / 'nested' / 'file.bin').read_bytes() == b'remote sync content'
    assert abs((destination / 'nested' / 'file.bin').stat().st_mtime - 1600000000) <= 2
    rows, total = window.transfer_history.store.page()
    assert total == 2 and all(row['outcome'] == 'complete' for row in rows)
