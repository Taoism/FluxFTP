import hashlib
import sqlite3
import time
from types import SimpleNamespace

import pytest

from fluxftp.trade_ledger import TradeLedger, event_digest
from fluxftp.trade_manifest import completion_marker_present, folder_manifest


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def add_trade(ledger, content='version-one'):
    return ledger.record_candidate(rule_id='source-rule', route_id='route-ab',
        source_bookmark='siteA', source_path='/incoming/Release',
        destination_bookmark='siteB', destination_path='/archive/Release',
        manifest_digest=digest(content))


def test_candidates_are_deduplicated_and_pending_source_changes_restart_stability(tmp_path):
    ledger = TradeLedger(tmp_path / 'trade.DB')
    trade, created = add_trade(ledger)
    repeated, repeated_created = add_trade(ledger)

    assert created and not repeated_created
    assert repeated['id'] == trade['id']
    assert ledger.observe_stability(trade['id'], trade['manifest_digest'], 3)['stable_count'] == 1
    updated, changed_created = add_trade(ledger, 'version-two')
    assert not changed_created
    assert updated['id'] == trade['id']
    assert updated['manifest_digest'] == digest('version-two')
    assert updated['stable_count'] == 0
    assert updated['state'] == 'waiting_stability'
    assert ledger.history(trade['id'])[-1]['reason'] == 'Source contents changed before transfer; stability count reset.'


def test_state_transitions_and_stability_gate_are_persisted(tmp_path):
    ledger = TradeLedger(tmp_path / 'trade.DB')
    trade, _ = add_trade(ledger)

    assert ledger.observe_stability(trade['id'], trade['manifest_digest'], 2)['state'] == 'waiting_stability'
    eligible = ledger.observe_stability(trade['id'], trade['manifest_digest'], 2)
    assert eligible['state'] == 'eligible'
    ledger.transition(trade['id'], 'queued', 'Submitted to the transfer queue.', queue_job_id='job-1')
    active = ledger.transition(trade['id'], 'transferring', 'Queue worker started.')
    assert active['queue_job_id'] == 'job-1'
    with pytest.raises(ValueError, match='Invalid trade transition'):
        ledger.transition(trade['id'], 'detected', 'Cannot rewind an active trade.')
    assert [row['new_state'] for row in ledger.history(trade['id'])] == [
        'detected', 'waiting_stability', 'eligible', 'queued', 'transferring']


def test_irc_event_ids_are_hashed_and_duplicate_delivery_is_suppressed(tmp_path):
    ledger = TradeLedger(tmp_path / 'trade.DB')
    trade, _ = add_trade(ledger)
    event_id = 'network:msgid:opaque-secret'
    assert ledger.record_irc_event(event_id, network_id='net', channel='#trades',
        sender='releasebot', trigger_id='trigger', trade_id=trade['id'])
    assert not ledger.record_irc_event(event_id, network_id='net', channel='#trades',
        sender='releasebot', trigger_id='trigger', trade_id=trade['id'])
    assert event_digest(event_id) != event_id
    with sqlite3.connect(ledger.path) as connection:
        stored = connection.execute('SELECT event_digest FROM irc_events').fetchone()[0]
    assert stored == event_digest(event_id)
    assert event_id not in stored


def test_migration_backup_and_terminal_retention_leave_active_trades(tmp_path):
    path = tmp_path / 'trade.DB'
    with sqlite3.connect(path) as connection:
        connection.execute('PRAGMA user_version=0')
        connection.execute('CREATE TABLE legacy_note(value TEXT)')
        connection.execute("INSERT INTO legacy_note VALUES('keep')")
    ledger = TradeLedger(path)
    assert list(tmp_path.glob('trade.DB.pre-migration-*.bak'))
    complete, _ = add_trade(ledger, 'complete-me')
    active, _ = ledger.record_candidate(rule_id='r', route_id='r', source_bookmark='a',
        source_path='/active', destination_bookmark='b', destination_path='/dest',
        manifest_digest=digest('active'))
    ledger.transition(complete['id'], 'cancelled', 'Test terminal retention.')
    assert ledger.terminal_cleanup(1, now=time.time() + 2 * 86400, enabled=False) == 0
    assert ledger.get_trade(complete['id']) is not None
    assert ledger.terminal_cleanup(1, now=time.time() + 2 * 86400, enabled=True) == 1
    assert ledger.get_trade(complete['id']) is None
    assert ledger.get_trade(active['id']) is not None


def test_complete_source_revision_is_a_new_trade_after_content_changes(tmp_path):
    ledger = TradeLedger(tmp_path / 'trade.DB')
    first, _ = add_trade(ledger)
    ledger.transition(first['id'], 'cancelled', 'End first revision.')
    second, created = add_trade(ledger, 'new-content')

    assert created
    assert second['id'] != first['id']


def test_manifest_is_deterministic_recursive_and_checks_marker():
    tree = {
        '/release': [SimpleNamespace(name='sub', is_dir=True, is_link=False, size=0, modified=''),
                     SimpleNamespace(name='.fluxftp-complete', is_dir=False, is_link=False, size=0, modified='stamp')],
        '/release/sub': [SimpleNamespace(name='movie.mkv', is_dir=False, is_link=False, size=42, modified='stamp')],
    }
    connection = SimpleNamespace(list=lambda path: list(reversed(tree[path])))

    first, digest_a = folder_manifest(connection, '/release')
    second, digest_b = folder_manifest(connection, '/release')
    assert first == second
    assert digest_a == digest_b
    assert completion_marker_present(first, '.fluxftp-complete')
    assert not completion_marker_present(first, '.done')


@pytest.mark.parametrize('entry, message', [
    (SimpleNamespace(name='link', is_dir=False, is_link=True, size=0, modified=''), 'symbolic link'),
    (SimpleNamespace(name='..', is_dir=False, is_link=False, size=0, modified=''), 'Unsafe file name'),
])
def test_manifest_refuses_untrusted_entries(entry, message):
    connection = SimpleNamespace(list=lambda _path: [entry])
    with pytest.raises(ValueError, match=message):
        folder_manifest(connection, '/release')


def test_manifest_enforces_entry_limit():
    entries = [SimpleNamespace(name=f'file-{index}', is_dir=False, is_link=False,
                               size=1, modified='') for index in range(2)]
    connection = SimpleNamespace(list=lambda _path: entries)
    with pytest.raises(ValueError, match='entry scan limit'):
        folder_manifest(connection, '/release', max_entries=1)
