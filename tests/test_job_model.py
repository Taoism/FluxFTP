import json
import os
import subprocess
import sys
from pathlib import Path
from threading import Event, Thread

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from fluxftp.job_model import JobModel, JobState, queue_descriptor
from fluxftp.queue_job import QueueJob
from fluxftp.queue_manager import read_queue, write_queue
from fluxftp.queue_io import Snapshot
from fluxftp.queue_tree import STATE_ROLE, JOB_ID_ROLE
from test_queue import window, job_for, descriptor
from test_queue_folders import folder


def test_model_is_usable_without_qt():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, '-c',
        "from fluxftp.job_model import JobModel, JobState; import sys; "
        "m=JobModel(); m.set('state','active'); assert m.state is JobState.ACTIVE; "
        "assert not any(n.startswith('PySide') for n in sys.modules)"],
        cwd=root, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_validated_identity_states_and_changes():
    model = JobModel(src='/a', dst='/b')
    changes = []
    unsubscribe = model.subscribe(changes.append)
    for value in ('active', 'failed', 'queued', 'active', 'complete'):
        model.set('state', value)
    assert model.state is JobState.COMPLETE
    assert len(changes) == 5 and all(c.job_id == model.job_id for c in changes)
    with pytest.raises(ValueError): model.set('state', 'typo')
    with pytest.raises(ValueError): model.set('job_id', 'f'*32)
    with pytest.raises(ValueError): model.set('row', object())
    with pytest.raises(ValueError): JobModel(workspace=object())
    with pytest.raises(ValueError): JobModel(identity='not-an-id')
    unsubscribe()
    model.set('state', 'queued')
    assert len(changes) == 5


def test_binding_keeps_widgets_and_secrets_out_of_model(window, tmp_path):
    job = job_for(window, lambda _: None)
    job.update(descriptor(tmp_path), workspace=window.tabs.currentWidget(), source_password='secret')
    assert isinstance(job, QueueJob)
    assert not {'row', 'workspace', 'task', 'source_password'} & job.model.values().keys()
    saved = queue_descriptor(job.model, 0)
    assert 'secret' not in json.dumps(saved) and saved['job_id'] == job.model.job_id
    assert job['row'].data(0, JOB_ID_ROLE) == job.model.job_id
    # The UI receives model changes, including changes emitted on a worker thread.
    thread = Thread(target=lambda: job.model.set('state', 'active'))
    thread.start(); thread.join()
    QApplication.processEvents()
    assert job['row'].data(0, STATE_ROLE) == 'active'
    window.job_progress(job, 50, 2, 50, 100)
    assert job.model.get('progress') == (50, 2, 50, 100)
    assert job['row'].text(1) == '50%' and job.model.get('size') == 100
    job['state'] = 'queued'


def test_identity_survives_recovery_retry_and_clear_undo(window, tmp_path):
    window.restore_queue_items([descriptor(tmp_path)])
    job = window.queue_jobs[0]
    identity = job['job_id']
    job['state'] = 'failed'
    assert window.retry_job(job) and job['job_id'] == identity
    path = tmp_path/'queue.json'
    write_queue(path, window.queue_snapshot())
    saved = read_queue(path)
    assert saved[0]['job_id'] == identity
    window.discard_queue_jobs(list(window.queue_jobs))
    window.restore_queue_items(saved)
    assert window.queue_jobs[0]['job_id'] == identity
    window.queue_commands.clear_current()
    assert identity not in window.job_views
    window.queue_commands.restore()
    assert window.queue_jobs[0]['job_id'] == identity
    # Importing the same work again is a distinct live job, not a duplicate ID.
    window.restore_queue_items(saved)
    assert len({j['job_id'] for j in window.queue_jobs}) == 2


def test_legacy_upgrade_duplicate_id_rejection_and_snapshot_parity(window, tmp_path):
    path = tmp_path/'old.json'
    write_queue(path, [descriptor(tmp_path)])
    saved = read_queue(path)
    assert len(saved[0]['job_id']) == 32
    window.restore_queue_items(saved)
    job = window.queue_jobs[0]
    job['size'] = 123
    # Serialized data comes from the model, not the presentation row.
    job['row'].setData(4, Qt.ItemDataRole.UserRole, 999)
    snapshot = Snapshot(window)
    try:
        assert list(snapshot) == window.queue_snapshot()
        assert list(snapshot)[0]['size'] == 123
    finally: snapshot.close()
    write_queue(path, [saved[0], saved[0]])
    with pytest.raises(ValueError, match='Duplicate'): read_queue(path)


def test_expanded_children_have_distinct_stable_ids(window, tmp_path):
    parent, children = folder(window, tmp_path)
    ids = {j['job_id'] for j in (parent, *children)}
    assert len(ids) == 3
    assert {j['job_id'] for j in window.queue_snapshot()} == {j['job_id'] for j in children}


def test_buffered_ids_survive_unloading_and_materialization(window, tmp_path):
    from fluxftp.queue_prepare import prepare, install, Settings
    items = [dict(descriptor(tmp_path), src=str(tmp_path/f'f{i}'), dst=str(tmp_path/f't{i}'),
                  name=f'f{i}', size=1, job_id=f'{i+1:032x}') for i in range(260)]
    prepared = prepare(items, Settings(window.preferences), Event())
    install(window, prepared)
    assert {j['job_id'] for j in window.queue_snapshot()} == {j['job_id'] for j in items}
    snapshot = Snapshot(window)
    try: assert {j['job_id'] for j in snapshot} == {j['job_id'] for j in items}
    finally: snapshot.close()
    assert len({j['job_id'] for j in window.queue_jobs}) == len(window.queue_jobs)
    window.restore_queue_items(items)
    snapshot = window.queue_snapshot()
    assert len(snapshot) == 520 and len({j['job_id'] for j in snapshot}) == 520


def test_snapshot_preserves_guarded_job_metadata(window, tmp_path):
    job = job_for(window, lambda _: None)
    job.update(descriptor(tmp_path), workspace=window.tabs.currentWidget(),
               watch={'id': 'watch'}, deployment={'revision': 'reviewed'}, sync={'operation': 'copy'})
    snapshot = Snapshot(window)
    try:
        value = list(snapshot)[0]
        assert value['watch'] == job['watch'] and value['deployment'] == job['deployment']
        assert value == window.queue_snapshot()[0]
    finally: snapshot.close()
