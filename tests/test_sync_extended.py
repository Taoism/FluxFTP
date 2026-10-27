import json
import os
from threading import Event
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QMessageBox
from fluxftp.folder_sync import scan, compare, execute_sync, PreviewChanged, validate_spec
from fluxftp.sync_profiles import SyncProfiles, enrich_plan
from fluxftp.transfer_options import TransferOptions
from fluxftp.core import Connection
from test_queue import window
from test_folder_sync import prepare, wait_idle
from test_integration import servers


def test_checksums_find_same_size_same_time_changes(tmp_path):
    left, right = tmp_path / 'a', tmp_path / 'b'
    left.mkdir(); right.mkdir()
    (left / 'file').write_bytes(b'abc')
    (right / 'file').write_bytes(b'xyz')
    for root in (left, right): os.utime(root / 'file', (1000000000, 1000000000))
    listings = [scan(None, str(root), Event(), hash_algorithm='SHA-256') for root in (left, right)]
    assert compare(*listings)[0]['reason'].startswith('Same')
    assert compare(*listings, rule='Checksum')[0]['reason'].startswith('Conflict')


def test_checksum_queue_rejects_changed_content_even_same_metadata(window, tmp_path):
    dialog, left, right = prepare(window, tmp_path)
    file = left / 'file'
    file.write_bytes(b'abc')
    stamp = file.stat().st_mtime_ns
    dialog.listings = [scan(None, str(root), Event(), hash_algorithm='SHA-256') for root in (left, right)]
    dialog.rule.setCurrentText('Checksum')
    dialog.recompare()
    dialog.enqueue()
    file.write_bytes(b'xyz')
    os.utime(file, ns=(stamp, stamp))
    window.play_queue()
    wait_idle(window)
    assert not (right / 'file').exists()
    assert window.queue_jobs[0]['state'] == 'failed'


def test_remote_checksum_scan(servers):
    root, site = servers(True, hash_support=True)
    (root / 'file').write_bytes(b'hash me')
    with Connection(site, 'password') as connection:
        remote = scan(connection, '/', Event(), hash_algorithm='SHA-256')
    local = scan(None, str(root), Event(), hash_algorithm='SHA-256')
    assert remote['file']['digest'] == local['file']['digest']


def test_mirror_requires_explicit_choice_and_preserves_folders(window, tmp_path, monkeypatch):
    dialog, left, right = prepare(window, tmp_path)
    (right / 'extra').write_bytes(b'extra')
    (right / 'empty').mkdir()
    dialog.listings = [scan(None, str(root), Event()) for root in (left, right)]
    dialog.mirror.setChecked(True)
    dialog.recompare()
    row = next(r for r in dialog.plan if r['path'] == 'extra')
    assert row['action'] == 'Skip' and row['choices'] == ['Skip', 'Delete right']
    row['action'] = 'Delete right'
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.StandardButton.Yes)
    dialog.enqueue()
    assert (right / 'extra').exists() and window.pending[0]['held']
    assert window.pending[0]['row'].text(0).startswith('Delete: ')
    window.play_queue()
    wait_idle(window)
    assert not (right / 'extra').exists() and (right / 'empty').is_dir()


@pytest.mark.parametrize('side', ['source', 'destination'])
def test_mirror_rechecks_both_sides(tmp_path, side):
    left, right = tmp_path / 'a', tmp_path / 'b'
    left.mkdir(); right.mkdir()
    (right / 'file').write_bytes(b'old')
    spec = dict(source_root=str(left), destination_root=str(right), relative='file', source=None,
        destination=scan(None, str(right), Event())['file'], operation='delete')
    (left if side == 'source' else right).joinpath('file').write_bytes(b'changed')
    job = dict(sync=spec, source_site=None, destination_site=None, directory=False,
               src=str(left / 'file'), dst=str(right / 'file'), options=TransferOptions())
    with pytest.raises(PreviewChanged): execute_sync(None, None, job, SimpleNamespace(cancel=Event(), report=lambda *_: None))
    assert (right / 'file').exists()


def test_profiles_and_explicit_baseline(window, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QInputDialog
    dialog, left, right = prepare(window, tmp_path)
    (left / 'file').write_bytes(b'a')
    dialog.listings = [scan(None, str(root), Event()) for root in (left, right)]
    dialog.save_baseline()
    (left / 'file').write_bytes(b'changed')
    dialog.listings = [scan(None, str(root), Event()) for root in (left, right)]
    dialog.recompare()
    assert 'Left changed' in dialog.plan[0]['change']
    monkeypatch.setattr(QInputDialog, 'getText', lambda *args: ('Photos', True))
    dialog.rule.setCurrentText('Size only')
    dialog.save_profile()
    dialog.rule.setCurrentText('Size and time')
    dialog.load_profile()
    assert dialog.rule.currentText() == 'Size only'
    stored = SyncProfiles(dialog.profile_store.path)
    assert stored.data['profiles']['Photos']['roots'] == [str(left), str(right)]
    assert 'password' not in dialog.profile_store.path.read_text()
    dialog.reject()


def test_delete_metadata_cannot_delete_folders_or_existing_sources():
    info = dict(directory=True, link=False, size=0, modified='')
    with pytest.raises(ValueError): validate_spec(dict(source_root='/', destination_root='/', relative='dir',
        source=None, destination=info, operation='delete'))


def test_remote_mirror_delete_has_guarded_queue_roundtrip(servers, tmp_path):
    from dataclasses import asdict
    from fluxftp.queue_manager import read_queue, write_queue
    local = tmp_path / 'local'
    local.mkdir()
    root, site = servers(True, hash_support=True)
    (root / 'extra').write_bytes(b'extra')
    with Connection(site, 'password') as connection:
        info = scan(connection, '/', Event(), hash_algorithm='SHA-256')['extra']
        job = dict(sync=dict(source_root=str(local), destination_root='/', relative='extra', source=None,
            destination=info, operation='delete'), source_site=None, destination_site=asdict(site),
            src=str(local / 'extra'), dst='/extra', name='extra', directory=False, resume=False,
            state='queued', workspace=0, size=0)
        path = tmp_path / 'queue.json'
        write_queue(path, [job])
        restored = read_queue(path)[0]
        restored.update(destination_site=site, options=TransferOptions())
        execute_sync(None, connection, restored, SimpleNamespace(cancel=Event(), report=lambda *_: None))
    assert not (root / 'extra').exists()


def test_baseline_tracks_deletions_on_both_sides():
    info = dict(directory=False, link=False, size=1, modified='100')
    rows = enrich_plan([], {}, {}, 'Two-way', baseline=[{'gone': info}, {'gone': info}])
    assert rows[0]['action'] == 'Skip' and rows[0]['change'] == 'Left deleted, Right deleted'


def test_corrupt_baseline_rejected_without_replacing_file(tmp_path):
    path = tmp_path / 'sync.json'
    text = json.dumps(dict(profiles={}, baselines={'pair': 'not a listing'}))
    path.write_text(text)
    with pytest.raises(ValueError): SyncProfiles(path)
    assert path.read_text() == text
