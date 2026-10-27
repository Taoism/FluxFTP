"""Disk-backed per-file evidence recorded by the verification engines."""
from contextvars import ContextVar
from contextlib import closing
from functools import wraps
import csv
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import uuid

_collector = ContextVar('verification_collector', default=None)
_file = ContextVar('verification_file', default=None)


def schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS verification_files (
        id INTEGER PRIMARY KEY, report_id TEXT NOT NULL, finished REAL NOT NULL,
        src TEXT NOT NULL, dst TEXT NOT NULL, outcome TEXT NOT NULL, checks TEXT NOT NULL)''')
    db.execute('CREATE INDEX IF NOT EXISTS verification_report ON verification_files(report_id,id)')


def evidence(method, status, expected=None, actual=None, algorithm='', detail=''):
    current = _file.get()
    if current is not None:
        current['checks'].append(dict(method=method, status=status, algorithm=algorithm,
            expected=expected, actual=actual, detail=str(detail)[:500]))


def renamed_destination(path):
    current = _file.get()
    if current is not None: current['dst'] = str(path)


def skipped_entry(path, reason):
    collector = _collector.get()
    if collector and _file.get() is None:
        collector(dict(src=str(path), dst='', outcome='skipped', checks=[dict(method='transfer', status='skipped',
            expected=None, actual=None, algorithm='', detail=str(reason)[:500])]))


def tracked_file(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        source, destination, src, dst, directory = (args[i] if len(args) > i else kwargs[key]
            for i, key in enumerate(('source', 'destination', 'src', 'dst', 'is_dir')))
        planned = kwargs.get('planned', args[12] if len(args) > 12 else None)
        collector = _collector.get()
        if not collector or directory or planned is not None or _file.get() is not None:
            return function(*args, **kwargs)
        row = dict(src=str(src), dst=str(dst), outcome='failed', checks=[])
        token = _file.set(row)
        try:
            result = function(*args, **kwargs)
            row['outcome'] = 'skipped' if result is False else 'complete'
            return result
        except BaseException as error:
            from .core import Cancelled
            row['outcome'] = 'cancelled' if isinstance(error, Cancelled) else 'failed'
            row['checks'].append(dict(method='transfer', status=row['outcome'], algorithm='',
                expected=None, actual=None, detail=type(error).__name__))
            raise
        finally:
            _file.reset(token)
            collector(row)
    return wrapped


def reported_task(window, job, task):
    @wraps(task)
    def wrapped(worker):
        summary = dict(report_id=uuid.uuid4().hex, file_count=0, error='')
        job['verification'] = summary
        db = None
        try:
            db = window.transfer_history.store.connect()
            schema(db)
            db.commit()
        except (OSError, sqlite3.Error):
            summary['error'] = 'Verification evidence could not be recorded.'
        def record(row):
            summary['file_count'] += 1
            if db is None: return
            try:
                db.execute('INSERT INTO verification_files(report_id,finished,src,dst,outcome,checks) VALUES (?,?,?,?,?,?)',
                    (summary['report_id'], time.time(), row['src'], row['dst'], row['outcome'], json.dumps(row['checks'])))
                db.commit()
            except sqlite3.Error:
                summary['error'] = 'Some verification evidence could not be recorded.'
        token = _collector.set(record)
        try: return task(worker)
        finally:
            _collector.reset(token)
            if db is not None: db.close()
    return wrapped


def verification_status(checks):
    if any(c['status'] in ('mismatch', 'source_changed') for c in checks): return 'mismatch'
    if any(c['status'] == 'verified' and c['method'] in ('checksum', 'CRC') for c in checks): return 'checksum verified'
    if any(c['status'] == 'verified' and c['method'] == 'size' for c in checks): return 'size verified only'
    return 'not verified'


def report_rows(store, search='', outcome='All', selected=None):
    with closing(store.connect()) as db:
        schema(db)
        db.commit()
        db.execute('BEGIN')
        clauses, args = [], []
        if selected is not None:
            if not selected: return
            clauses.append('id IN (' + ','.join('?' for _ in selected) + ')')
            args.extend(selected)
        if outcome != 'All': clauses.append('outcome=?'); args.append(outcome.lower())
        if search:
            clauses.append('(instr(lower(src),lower(?)) OR instr(lower(dst),lower(?)) OR instr(lower(detail),lower(?)))')
            args.extend([search] * 3)
        query = 'SELECT * FROM transfers' + (' WHERE ' + ' AND '.join(clauses) if clauses else '') + ' ORDER BY id'
        for history in db.execute(query, args):
            summary = json.loads(history['verification'])
            count = 0
            if summary.get('report_id'):
                for item in db.execute('SELECT * FROM verification_files WHERE report_id=? ORDER BY id', (summary['report_id'],)):
                    count += 1
                    checks = json.loads(item['checks'])
                    yield dict(history_id=history['id'], finished=item['finished'], source=item['src'], destination=item['dst'],
                        outcome=item['outcome'], verification=verification_status(checks), checks=checks, report_error=summary.get('error', ''))
            if not count or count != summary.get('file_count', count) or summary.get('error'):
                yield dict(history_id=history['id'], finished=history['finished'], source=history['src'], destination=history['dst'],
                    outcome=history['outcome'], verification='not recorded' if not count else 'incomplete report', checks=[],
                    report_error=summary.get('error') or ('Some retained file evidence is missing.' if count else 'No per-file evidence recorded for this history entry.'))


def export_report(store, path, search='', outcome='All', selected=None, cancel=None):
    path = Path(path)
    fd, name = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=path.parent)
    count = 0
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='') as stream:
            writer = csv.writer(stream) if path.suffix.lower() == '.csv' else None
            if writer: writer.writerow(['History ID', 'Finished (UTC)', 'Source', 'Destination', 'Outcome', 'Verification', 'Checks (JSON)', 'Report note'])
            else: stream.write('{"version":1,"files":[')
            from datetime import datetime, timezone
            for row in report_rows(store, search, outcome, selected):
                if cancel and cancel.is_set():
                    from .core import Cancelled
                    raise Cancelled('Report export cancelled')
                row['finished'] = datetime.fromtimestamp(row['finished'], timezone.utc).isoformat()
                if writer:
                    def cell(value):
                        value = str(value)
                        return "'" + value if value.lstrip().startswith(('=', '+', '-', '@')) or value.startswith(('\t', '\r', '\n')) else value
                    writer.writerow([cell(row[k]) for k in ('history_id', 'finished', 'source', 'destination', 'outcome', 'verification')] +
                                    [json.dumps(row['checks'], ensure_ascii=False), cell(row['report_error'])])
                else:
                    if count: stream.write(',')
                    stream.write(json.dumps(row, ensure_ascii=False))
                count += 1
            if not writer: stream.write('],"file_rows":' + str(count) + '}\n')
            stream.flush()
            os.fsync(stream.fileno())
        Path(name).replace(path)
    finally: Path(name).unlink(missing_ok=True)
    return count
