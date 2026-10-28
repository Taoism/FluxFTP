"""Scheduling decisions run with plain data and fake clocks, without a desktop."""
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from fluxftp.queue_engine import QueueEngine, QueuePolicy


def job(name='file', **values):
    result = dict(name=name, src='/source/' + name, dst='/target/' + name,
        source_site=None, destination_site=None, workspace=WORKSPACE,
        route='Copy', state='queued', retries=0, directory=False)
    result.update(values)
    return result


WORKSPACE = object()


def site(**values):
    result = dict(host='server', port=21, username='user', protocol='FTP',
        single_connection=False, max_transfers_total=6, max_downloads=6, max_uploads=6)
    result.update(values)
    return SimpleNamespace(**result)


def activate(engine, item):
    if item in engine.pending: engine.pending.remove(item)
    engine.begin(item)
    return engine.register(dict(engine.transfer_record(item), job=item))


def test_engine_import_does_not_load_qt_or_desktop():
    command = "from fluxftp.queue_engine import QueueEngine; import sys; assert 'fluxftp.app' not in sys.modules; assert not any(n.startswith('PySide') for n in sys.modules)"
    result = subprocess.run([sys.executable, '-c', command], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_manual_order_priority_holds_and_clock():
    now = [100]
    engine = QueueEngine(clock=lambda: now[0])
    ordinary, priority, manual = job('z.bin'), job('a.sfv'), job('manual', manual_order=2, scheduled_at=101)
    engine.pending.extend([ordinary, priority, manual])
    policy = QueuePolicy()
    assert engine.next_index(policy) == 1
    priority['held'] = True
    assert engine.next_index(policy) == 0
    now[0] = 101
    assert engine.next_index(policy) == 2
    engine.paused = True
    assert engine.next_index(policy) is None
    engine.paused = False
    manual['editing'] = True
    ordinary['buffered'] = True
    assert engine.next_index(policy) is None


@pytest.mark.parametrize('setting', ['single_connection', 'max_transfers_total', 'max_downloads'])
def test_site_limits(setting):
    remote = site(**{setting: True if setting == 'single_connection' else 1})
    engine = QueueEngine()
    activate(engine, job('one', source_site=remote, route='Download'))
    assert not engine.can_start(job('two', source_site=remote, route='Download'), QueuePolicy())
    # Existing total limits constrain the workspace, including other sites.
    assert engine.can_start(job('two', source_site=site(host='other'), route='Download', workspace=object()), QueuePolicy())


def test_global_and_workspace_limits_and_upload_direction():
    engine = QueueEngine()
    remote = site(max_uploads=1)
    activate(engine, job('one', destination_site=remote, route='Upload'))
    assert not engine.can_start(job('two', destination_site=remote, route='Upload'), QueuePolicy())
    # Preserve the existing conservative per-site directional admission policy.
    assert not engine.can_start(job('two', source_site=remote, route='Download'), QueuePolicy())
    assert engine.can_start(job('two', source_site=site(host='other'), route='Download'), QueuePolicy())
    assert not engine.can_start(job('two'), QueuePolicy(per_tab=1))
    assert engine.can_start(job('two', workspace=object()), QueuePolicy(per_tab=1))
    assert not engine.can_start(job('two', workspace=object()), QueuePolicy(single_connection=True))


@pytest.mark.parametrize('active_directory,candidate_directory,active_path,candidate_path,allowed', [
    (False, False, '/out/file', '/out/file', False),
    (True, False, '/out', '/out/file', False),
    (False, True, '/out/file', '/out', False),
    (True, False, '/out', '/outside/file', True),
])
def test_destination_conflicts_across_workspaces(active_directory, candidate_directory, active_path, candidate_path, allowed):
    engine = QueueEngine()
    remote = site()
    activate(engine, job('one', dst=active_path, directory=active_directory, destination_site=remote))
    assert engine.can_start(job('two', dst=candidate_path, directory=candidate_directory,
        destination_site=remote, workspace=object()), QueuePolicy()) is allowed


def test_deployments_are_exclusive_in_both_directions():
    engine = QueueEngine()
    ordinary, deployment = job('one'), job('deploy', deployment={'id': 'approved'})
    transfer = activate(engine, ordinary)
    engine.pending.append(deployment)
    assert engine.next_index(QueuePolicy()) is None
    engine.release(transfer)
    assert engine.next_index(QueuePolicy()) == 0
    activate(engine, deployment)
    engine.pending.append(ordinary)
    assert engine.next_index(QueuePolicy()) is None


@pytest.mark.parametrize('cycle,after,attempts', [(True, True, 3), (True, False, 3), (False, True, 3), (False, False, 1)])
def test_retry_budget_shared_across_policies(cycle, after, attempts):
    engine = QueueEngine()
    item = job()
    engine.pending.append(item)
    policy = QueuePolicy(cycle_retry=cycle, retry_after=after, retry_download=2)
    events = []
    engine.subscribe(events.append)
    ran = 0
    while engine.pending:
        transfer = activate(engine, item)
        ran += 1
        item['state'] = 'failed'
        engine.release(transfer)
        engine.settle(item, policy)
    assert ran == attempts and item['state'] == 'failed' and engine.batch_failed
    assert sum(event.kind == 'retry' for event in events) == attempts - 1


def test_deferred_retry_waits_for_original_work_and_retry_is_not_duplicated():
    engine = QueueEngine()
    first, second = job('first'), job('second')
    engine.pending.extend([first, second])
    policy = QueuePolicy(cycle_retry=False)
    transfer = activate(engine, first)
    first['state'] = 'failed'
    engine.release(transfer)
    engine.settle(first, policy)
    assert engine.pending == [second]
    transfer = activate(engine, second)
    second['state'] = 'complete'
    engine.release(transfer)
    engine.settle(second, policy)
    assert engine.pending == [first]
    assert not engine.retry(first, policy) and first['retries'] == 1


@pytest.mark.parametrize('guard', ['watch', 'deployment', 'crc_job', 'suppress_retry'])
def test_guarded_failures_do_not_retry(guard):
    engine = QueueEngine()
    item = job(state='failed', **{guard: True})
    assert not engine.retry(item, QueuePolicy()) and not engine.pending


def test_cancellation_does_not_release_active_slots_until_worker_finishes():
    engine = QueueEngine()
    active, queued, unrelated = job('active'), job('queued'), job('unrelated')
    engine.jobs.extend([active, queued, unrelated])
    transfer = activate(engine, active)
    engine.pending.extend([queued, unrelated])
    records = engine.cancel([active, queued])
    assert records == [engine.active[transfer]]
    assert active['suppress_retry'] and queued['state'] == 'cancelled'
    assert engine.pending == [unrelated] and unrelated['state'] == 'queued'
    active['state'] = 'cancelled'
    engine.release(transfer)
    engine.settle(active, QueuePolicy())
    assert not engine.active and engine.pending == [unrelated]


def test_trust_dialog_defers_finish_and_duplicate_finish_is_harmless():
    engine = QueueEngine()
    item = job()
    transfer = activate(engine, item)
    record = engine.active[transfer]
    record['handling_failure'] = True
    assert engine.release(transfer) is None and record['finish_deferred']
    record['handling_failure'] = False
    item['state'] = 'queued'
    assert engine.release(transfer) is record
    engine.settle(item, QueuePolicy())
    assert engine.pending == [item] and item['retries'] == 0
    assert engine.release(transfer) is None


def test_explicit_reset_clears_budget_and_preserves_job_identity():
    engine = QueueEngine()
    item = job(state='failed', job_id='a' * 32, retries=5, suppress_retry=True)
    engine.jobs.append(item)
    engine.reset_failed()
    engine.reset_failed()
    assert engine.pending == [item] and item['job_id'] == 'a' * 32
    assert item['retries'] == 0 and not item['suppress_retry']
