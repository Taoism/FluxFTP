"""Durable trade state, de-duplication, migration and retention."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import time
import uuid


SCHEMA_VERSION = 1
STATES = frozenset({'detected', 'waiting_stability', 'eligible', 'queued', 'transferring',
                    'verifying', 'complete', 'failed', 'paused', 'cancelled', 'rejected'})
TERMINAL_STATES = frozenset({'complete', 'cancelled', 'rejected'})
TRANSITIONS = {
    'detected': {'waiting_stability', 'rejected', 'cancelled', 'paused'},
    'waiting_stability': {'waiting_stability', 'eligible', 'rejected', 'cancelled', 'paused'},
    'eligible': {'waiting_stability', 'queued', 'rejected', 'cancelled', 'paused', 'failed'},
    'queued': {'transferring', 'paused', 'cancelled', 'failed'},
    'transferring': {'verifying', 'paused', 'cancelled', 'failed'},
    'verifying': {'complete', 'failed', 'cancelled', 'transferring', 'paused'},
    'failed': {'queued', 'cancelled', 'paused'},
    'paused': {'detected', 'waiting_stability', 'eligible', 'queued', 'transferring', 'failed', 'cancelled'},
    'complete': set(), 'cancelled': set(), 'rejected': set(),
}


def stable_id(*parts):
    """Create a deterministic 64-character identity from canonical values."""
    encoded = json.dumps(parts, ensure_ascii=False, separators=(',', ':'), sort_keys=True).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def event_digest(value):
    """Hash an untrusted external event ID before persisting it."""
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise ValueError('IRC event identity must be a non-empty string no longer than 4096 characters.')
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


class TradeLedger:
    """SQLite ledger. Credentials and raw IRC message text are never accepted."""
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate()
        try:
            self.path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('PRAGMA busy_timeout=15000')
        connection.execute('PRAGMA synchronous=FULL')
        connection.execute('PRAGMA secure_delete=ON')
        return connection

    def _migrate(self):
        existed = self.path.exists() and self.path.stat().st_size > 0
        connection = self._connect()
        try:
            connection.execute('PRAGMA journal_mode=WAL')
            version = connection.execute('PRAGMA user_version').fetchone()[0]
            if version > SCHEMA_VERSION:
                raise ValueError(f'trade.DB schema {version} is newer than this FluxFTP supports ({SCHEMA_VERSION}).')
            if version == SCHEMA_VERSION:
                return
            if existed:
                backup = self.path.with_name(self.path.name + f'.pre-migration-{int(time.time())}.bak')
                if backup.exists():
                    backup = self.path.with_name(self.path.name + f'.pre-migration-{uuid.uuid4().hex[:8]}.bak')
                with sqlite3.connect(backup) as target:
                    connection.backup(target)
            connection.execute('BEGIN IMMEDIATE')
            try:
                if version == 0:
                    statements = [
                        '''CREATE TABLE trades (
                            id TEXT PRIMARY KEY CHECK(length(id)=64),
                            revision_key TEXT NOT NULL UNIQUE CHECK(length(revision_key)=64),
                            source_key TEXT NOT NULL CHECK(length(source_key)=64),
                            rule_id TEXT NOT NULL,
                            route_id TEXT NOT NULL,
                            source_bookmark TEXT NOT NULL,
                            source_path TEXT NOT NULL,
                            destination_bookmark TEXT NOT NULL,
                            destination_path TEXT NOT NULL,
                            destination_resolved_path TEXT NOT NULL DEFAULT '',
                            manifest_digest TEXT NOT NULL CHECK(length(manifest_digest)=64),
                            state TEXT NOT NULL CHECK(state IN ('detected','waiting_stability','eligible','queued','transferring','verifying','complete','failed','paused','cancelled','rejected')),
                            resume_state TEXT NOT NULL DEFAULT '',
                            stable_count INTEGER NOT NULL DEFAULT 0 CHECK(stable_count >= 0),
                            trigger_source TEXT NOT NULL,
                            trigger_id TEXT NOT NULL DEFAULT '',
                            queue_job_id TEXT NOT NULL DEFAULT '',
                            retries INTEGER NOT NULL DEFAULT 0 CHECK(retries >= 0),
                            next_attempt REAL NOT NULL DEFAULT 0,
                            last_error TEXT NOT NULL DEFAULT '',
                            created_at REAL NOT NULL,
                            updated_at REAL NOT NULL,
                            completed_at REAL
                        )''',
                        'CREATE INDEX trades_state_updated ON trades(state, updated_at)',
                        'CREATE INDEX trades_source_key ON trades(source_key, created_at)',
                        '''CREATE TABLE trade_events (
                            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                            trade_id TEXT NOT NULL REFERENCES trades(id) ON DELETE CASCADE,
                            old_state TEXT NOT NULL,
                            new_state TEXT NOT NULL,
                            reason TEXT NOT NULL,
                            occurred_at REAL NOT NULL
                        )''',
                        'CREATE INDEX trade_events_trade ON trade_events(trade_id, sequence)',
                        '''CREATE TABLE irc_events (
                            event_digest TEXT PRIMARY KEY CHECK(length(event_digest)=64),
                            network_id TEXT NOT NULL,
                            channel TEXT NOT NULL,
                            sender TEXT NOT NULL,
                            trigger_id TEXT NOT NULL,
                            trade_id TEXT REFERENCES trades(id) ON DELETE SET NULL,
                            received_at REAL NOT NULL
                        )''',
                        'CREATE INDEX irc_events_received ON irc_events(received_at)',
                    ]
                    for statement in statements:
                        connection.execute(statement)
                    connection.execute(f'PRAGMA user_version={SCHEMA_VERSION}')
                connection.execute('COMMIT')
            except BaseException:
                if connection.in_transaction:
                    connection.execute('ROLLBACK')
                raise
        finally:
            connection.close()

    def record_candidate(self, *, rule_id, route_id, source_bookmark, source_path,
                         destination_bookmark, destination_path, manifest_digest,
                         trigger_source='poll', trigger_id='', initial_state='detected'):
        if initial_state not in STATES:
            raise ValueError('Invalid initial trade state.')
        if trigger_source not in ('poll', 'irc', 'manual'):
            raise ValueError('Invalid trade trigger source.')
        if (not isinstance(manifest_digest, str) or len(manifest_digest) != 64 or
                any(char not in '0123456789abcdef' for char in manifest_digest.lower())):
            raise ValueError('Invalid source manifest digest.')
        for value, label in ((rule_id, 'rule_id'), (route_id, 'route_id'),
                             (source_bookmark, 'source_bookmark'), (source_path, 'source_path'),
                             (destination_bookmark, 'destination_bookmark'), (destination_path, 'destination_path')):
            if not isinstance(value, str) or not value or len(value) > 2048 or any(ord(char) < 32 for char in value):
                raise ValueError(f'{label} is invalid.')
        source_key = stable_id(source_bookmark.casefold(), source_path)
        revision_key = stable_id(route_id, source_key, manifest_digest.lower())
        trade_id = revision_key
        now = time.time()
        values = (trade_id, revision_key, source_key, rule_id, route_id, source_bookmark,
                  source_path, destination_bookmark, destination_path, manifest_digest.lower(),
                  initial_state, trigger_source, trigger_id[:256], now, now)
        with self._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            active = connection.execute('''SELECT * FROM trades WHERE source_key=? AND route_id=?
                AND state IN ('detected','waiting_stability','eligible') ORDER BY created_at LIMIT 1''',
                (source_key, route_id)).fetchone()
            if active and active['manifest_digest'] != manifest_digest.lower():
                next_key = stable_id(route_id, source_key, manifest_digest.lower())
                prior_revision = connection.execute('SELECT id FROM trades WHERE revision_key=?', (next_key,)).fetchone()
                if prior_revision is None:
                    now = time.time()
                    connection.execute("""UPDATE trades SET manifest_digest=?,revision_key=?,stable_count=0,
                        state='waiting_stability',trigger_source=?,trigger_id=?,updated_at=? WHERE id=?""",
                        (manifest_digest.lower(), next_key, trigger_source, trigger_id[:256], now, active['id']))
                    connection.execute('INSERT INTO trade_events(trade_id,old_state,new_state,reason,occurred_at) VALUES(?,?,?,?,?)',
                        (active['id'], active['state'], 'waiting_stability', 'Source contents changed before transfer; stability count reset.', now))
                    trade_id = active['id']
                    created = False
                    connection.execute('COMMIT')
                    return self.get_trade(trade_id), created
            if active and active['manifest_digest'] == manifest_digest.lower():
                connection.execute('COMMIT')
                return self.get_trade(active['id']), False
            row = connection.execute('''INSERT OR IGNORE INTO trades
                (id,revision_key,source_key,rule_id,route_id,source_bookmark,source_path,
                 destination_bookmark,destination_path,manifest_digest,state,trigger_source,
                 trigger_id,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', values)
            created = row.rowcount == 1
            if created:
                connection.execute('INSERT INTO trade_events(trade_id,old_state,new_state,reason,occurred_at) VALUES(?,?,?,?,?)',
                                   (trade_id, '', initial_state, 'Trade candidate recorded.', now))
            else:
                trade_id = connection.execute('SELECT id FROM trades WHERE revision_key=?', (revision_key,)).fetchone()['id']
            connection.execute('COMMIT')
        return self.get_trade(trade_id), created

    def transition(self, trade_id, state, reason, *, queue_job_id=None, retries=None,
                   next_attempt=None, last_error=None, resolved_destination=None):
        if state not in STATES:
            raise ValueError('Invalid trade state.')
        if not isinstance(reason, str) or not reason or len(reason) > 2048:
            raise ValueError('Trade transition reason must be 1-2048 characters.')
        now = time.time()
        with self._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            trade = connection.execute('SELECT * FROM trades WHERE id=?', (trade_id,)).fetchone()
            if trade is None:
                raise KeyError(trade_id)
            old_state = trade['state']
            if state != old_state and state not in TRANSITIONS[old_state]:
                raise ValueError(f'Invalid trade transition: {old_state} -> {state}.')
            updates = {'state': state, 'updated_at': now}
            if state == 'paused':
                updates['resume_state'] = old_state
            elif old_state == 'paused':
                updates['resume_state'] = ''
            if queue_job_id is not None:
                updates['queue_job_id'] = queue_job_id
            if retries is not None:
                if type(retries) is not int or retries < 0:
                    raise ValueError('retries must be a non-negative integer.')
                updates['retries'] = retries
            if next_attempt is not None:
                updates['next_attempt'] = float(next_attempt)
            if last_error is not None:
                updates['last_error'] = str(last_error)[:2048]
            if resolved_destination is not None:
                updates['destination_resolved_path'] = str(resolved_destination)[:2048]
            if state in TERMINAL_STATES:
                updates['completed_at'] = now
            assignments = ','.join(f'{column}=?' for column in updates)
            connection.execute(f'UPDATE trades SET {assignments} WHERE id=?', (*updates.values(), trade_id))
            if state != old_state or reason != '':
                connection.execute('INSERT INTO trade_events(trade_id,old_state,new_state,reason,occurred_at) VALUES(?,?,?,?,?)',
                                   (trade_id, old_state, state, reason, now))
            connection.execute('COMMIT')
        return self.get_trade(trade_id)

    def observe_stability(self, trade_id, signature, required_polls):
        if not isinstance(signature, str) or len(signature) != 64:
            raise ValueError('A SHA-256 listing signature is required.')
        if type(required_polls) is not int or required_polls < 2:
            raise ValueError('required_polls must be at least two.')
        with self._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute('SELECT state,manifest_digest,stable_count FROM trades WHERE id=?', (trade_id,)).fetchone()
            if row is None:
                raise KeyError(trade_id)
            if row['state'] not in ('detected', 'waiting_stability'):
                connection.execute('COMMIT')
                return self.get_trade(trade_id)
            count = row['stable_count'] + 1 if row['manifest_digest'] == signature else 1
            state = 'eligible' if count >= required_polls else 'waiting_stability'
            now = time.time()
            old = row['state']
            connection.execute('UPDATE trades SET manifest_digest=?,stable_count=?,state=?,updated_at=? WHERE id=?',
                               (signature, count, state, now, trade_id))
            connection.execute('INSERT INTO trade_events(trade_id,old_state,new_state,reason,occurred_at) VALUES(?,?,?,?,?)',
                               (trade_id, old, state, f'Source listing stable for {count}/{required_polls} polls.', now))
            connection.execute('COMMIT')
        return self.get_trade(trade_id)

    def record_irc_event(self, event_id, *, network_id, channel, sender, trigger_id, trade_id=''):
        digest = event_digest(event_id)
        now = time.time()
        with self._connect() as connection:
            cursor = connection.execute('''INSERT OR IGNORE INTO irc_events
                (event_digest,network_id,channel,sender,trigger_id,trade_id,received_at)
                VALUES(?,?,?,?,?,?,?)''',
                (digest, str(network_id)[:128], str(channel)[:200], str(sender)[:128],
                 str(trigger_id)[:80], trade_id or None, now))
            return cursor.rowcount == 1

    def list_trades(self, states=None, limit=1000):
        if type(limit) is not int or not 1 <= limit <= 10_000:
            raise ValueError('limit must be from 1 through 10000.')
        with self._connect() as connection:
            if states:
                states = tuple(states)
                if not states or any(state not in STATES for state in states):
                    raise ValueError('Invalid trade state filter.')
                placeholders = ','.join('?' for _ in states)
                rows = connection.execute(f'SELECT * FROM trades WHERE state IN ({placeholders}) ORDER BY created_at DESC LIMIT ?',
                                          (*states, limit)).fetchall()
            else:
                rows = connection.execute('SELECT * FROM trades ORDER BY created_at DESC LIMIT ?', (limit,)).fetchall()
            return [dict(row) for row in rows]

    def get_trade(self, trade_id):
        with self._connect() as connection:
            row = connection.execute('SELECT * FROM trades WHERE id=?', (trade_id,)).fetchone()
            return dict(row) if row else None

    def history(self, trade_id):
        with self._connect() as connection:
            return [dict(row) for row in connection.execute(
                'SELECT old_state,new_state,reason,occurred_at FROM trade_events WHERE trade_id=? ORDER BY sequence',
                (trade_id,))]

    def terminal_cleanup(self, retention_days, *, now=None, enabled=False):
        if not enabled:
            return 0
        if type(retention_days) is not int or not 1 <= retention_days <= 3650:
            raise ValueError('retention_days must be from 1 through 3650.')
        cutoff = (time.time() if now is None else now) - timedelta(days=retention_days).total_seconds()
        with self._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            connection.execute("""DELETE FROM irc_events WHERE trade_id IN
                (SELECT id FROM trades WHERE state IN ('complete','cancelled','rejected') AND completed_at<?)""",
                (cutoff,))
            cursor = connection.execute("DELETE FROM trades WHERE state IN ('complete','cancelled','rejected') AND completed_at<?", (cutoff,))
            connection.execute('COMMIT')
            return cursor.rowcount
