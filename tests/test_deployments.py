from dataclasses import asdict
from pathlib import Path
from threading import Event
from types import SimpleNamespace
import uuid

import pytest
from fluxftp.core import Connection
from fluxftp.deployments import preview, execute, rollback_preview, validate_job, revision_path, read_revision
from fluxftp.folder_sync import PreviewChanged
from fluxftp.transfer_options import TransferOptions
from test_integration import servers
from test_queue import window
from test_folder_sync import wait_idle


def setup_plan(tmp_path, connection):
    source = tmp_path / 'source'; source.mkdir()
    (source / 'existing').write_bytes(b'new contents')
    (source / 'nested').mkdir()
    (source / 'nested' / 'new').write_bytes(b'new file')
    (source / 'excluded.tmp').write_bytes(b'do not deploy')
    profile = dict(name='Test', source=str(source), site=connection.site.name, destination='/',
        excludes=['*.tmp'], verification='Checksum readback')
    files = preview(profile, connection, Event())
    return dict(id=uuid.uuid4().hex, profile=profile, files=files, rollback=False)


def queue_item(window, connection, plan):
    return dict(src=plan['profile']['source'], dst='/', name='Deploy: Test', directory=True, size=1,
        resume=False, state='queued', held=True, retries=0, workspace=0, source_site=None,
        destination_site=asdict(connection.site), deployment=plan)


@pytest.mark.parametrize('tls', [False, True])
def test_deployment_and_rollback_preserve_originals_and_other_files(servers, tmp_path, tls):
    root, site = servers(tls, hash_support=True)
    (root / 'existing').write_bytes(b'original')
    (root / 'untouched').write_bytes(b'untouched')
    host = SimpleNamespace(sites_file=tmp_path / 'sites.json')
    worker = SimpleNamespace(cancel=Event(), report=lambda *_: None)
    with Connection(site, 'password') as connection:
        plan = setup_plan(tmp_path, connection)
        assert (root / 'existing').read_bytes() == b'original'
        job = dict(deployment=plan, options=TransferOptions())
        execute(host, connection, job, worker)
        assert (root / 'existing').read_bytes() == b'new contents'
        assert (root / 'nested' / 'new').read_bytes() == b'new file'
        assert not (root / 'excluded.tmp').exists()
        directory = revision_path(host, plan['id'])
        journal, rows = rollback_preview(directory, connection, Event())
        assert {r['action'] for r in rows} == {'Restore backup', 'Delete new file'}
        execute(host, connection, dict(deployment=dict(plan, rollback=True), options=TransferOptions()), worker)
        assert (root / 'existing').read_bytes() == b'original'
        assert not (root / 'nested' / 'new').exists()
        assert (root / 'untouched').read_bytes() == b'untouched'
        assert read_revision(directory)['state'] == 'rolled_back'
        assert list(directory.glob('*.backup'))


def test_later_remote_edits_block_rollback(servers, tmp_path):
    root, site = servers(hash_support=True)
    (root / 'existing').write_bytes(b'original')
    host = SimpleNamespace(sites_file=tmp_path / 'sites.json')
    with Connection(site, 'password') as connection:
        plan = setup_plan(tmp_path, connection)
        execute(host, connection, dict(deployment=plan, options=TransferOptions()), SimpleNamespace(cancel=Event(), report=lambda *_: None))
        (root / 'existing').write_bytes(b'later user edit')
        with pytest.raises(PreviewChanged): rollback_preview(revision_path(host, plan['id']), connection, Event())
    assert (root / 'existing').read_bytes() == b'later user edit'


def test_stale_local_preview_never_overwrites_remote(servers, tmp_path):
    root, site = servers(hash_support=True)
    (root / 'existing').write_bytes(b'original')
    host = SimpleNamespace(sites_file=tmp_path / 'sites.json')
    with Connection(site, 'password') as connection:
        plan = setup_plan(tmp_path, connection)
        (Path(plan['profile']['source']) / 'existing').write_bytes(b'changed after preview')
        with pytest.raises(PreviewChanged): execute(host, connection, dict(deployment=plan, options=TransferOptions()), SimpleNamespace(cancel=Event(), report=lambda *_: None))
    assert (root / 'existing').read_bytes() == b'original'


def test_unsupported_native_hash_uses_readback(servers, tmp_path):
    root, site = servers()
    host = SimpleNamespace(sites_file=tmp_path / 'sites.json')
    with Connection(site, 'password') as connection:
        plan = setup_plan(tmp_path, connection)
        execute(host, connection, dict(deployment=plan, options=TransferOptions()), SimpleNamespace(cancel=Event(), report=lambda *_: None))
    assert (root / 'existing').read_bytes() == b'new contents'


def test_deployment_queue_roundtrip_and_evidence(window, servers, tmp_path):
    from fluxftp.queue_manager import read_queue
    root, site = servers(hash_support=True)
    window.initialize_queue_storage(tmp_path / 'queue.json')
    with Connection(site, 'password') as connection:
        plan = setup_plan(tmp_path, connection)
        window.restore_queue_items([queue_item(window, connection, plan)])
    job = window.pending[0]
    job.update(source_password='', destination_password='password', needs_credentials=False)
    window.save_queue_state()
    assert read_queue(window.queue_file)[0]['deployment'] == plan
    window.play_queue()
    wait_idle(window)
    assert job['state'] == 'complete'
    from fluxftp.verification_reports import report_rows
    rows = list(report_rows(window.transfer_history.store))
    assert len(rows) == 2 and all(row['verification'] == 'checksum verified' for row in rows)


def test_revision_path_traversal_is_rejected(tmp_path):
    with pytest.raises(ValueError): revision_path(SimpleNamespace(sites_file=tmp_path / 'sites.json'), '../other')


def test_interrupted_write_retains_backup_and_cannot_replay(servers, tmp_path, monkeypatch):
    import fluxftp.deployments as deployments
    root, site = servers(hash_support=True)
    (root / 'existing').write_bytes(b'original')
    host = SimpleNamespace(sites_file=tmp_path / 'sites.json')
    worker = SimpleNamespace(cancel=Event(), report=lambda *_: None)
    with Connection(site, 'password') as connection:
        plan = setup_plan(tmp_path, connection)
        def interrupted(*args):
            (root / 'existing').write_bytes(b'partial')
            raise OSError('simulated interrupted write')
        monkeypatch.setattr(deployments, 'execute_sync', interrupted)
        job = dict(deployment=plan, options=TransferOptions())
        with pytest.raises(OSError): execute(host, connection, job, worker)
        directory = revision_path(host, plan['id'])
        assert read_revision(directory)['state'] == 'interrupted'
        assert (directory / '00000.backup').read_bytes() == b'original'
        with pytest.raises(PreviewChanged, match='already started'): execute(host, connection, job, worker)
        with pytest.raises(PreviewChanged, match='differs'): rollback_preview(directory, connection, Event())
    assert (root / 'existing').read_bytes() == b'partial'


def test_changed_backup_blocks_rollback(servers, tmp_path):
    root, site = servers(hash_support=True)
    (root / 'existing').write_bytes(b'original')
    host = SimpleNamespace(sites_file=tmp_path / 'sites.json')
    with Connection(site, 'password') as connection:
        plan = setup_plan(tmp_path, connection)
        execute(host, connection, dict(deployment=plan, options=TransferOptions()), SimpleNamespace(cancel=Event(), report=lambda *_: None))
        directory = revision_path(host, plan['id'])
        (directory / '00000.backup').write_bytes(b'changed backup')
        with pytest.raises(ValueError, match='missing or changed'): rollback_preview(directory, connection, Event())
    assert (root / 'existing').read_bytes() == b'new contents'
