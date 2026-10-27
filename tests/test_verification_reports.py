import csv
import json
from pathlib import Path
from threading import Event
from types import SimpleNamespace
import time

import pytest
from fluxftp.core import transfer_tree
from fluxftp.transfer_options import TransferOptions
from fluxftp.transfer_history import HistoryStore
from fluxftp.verification_reports import reported_task, report_rows, export_report, verification_status


def run_copy(tmp_path, options=None, folder=False):
    store = HistoryStore(tmp_path / 'history.sqlite')
    window = SimpleNamespace(transfer_history=SimpleNamespace(store=store))
    source, destination = tmp_path / 'source', tmp_path / 'destination'
    if folder:
        source.mkdir()
        (source / 'one').write_bytes(b'one')
        (source / 'two').write_bytes(b'two')
    else: source.write_bytes(b'verified contents')
    job = {}
    worker = SimpleNamespace(cancel=Event(), report=lambda *_: None)
    task = reported_task(window, job, lambda w: transfer_tree(None, None, str(source), str(destination), folder,
        w.cancel, w.report, options=options or TransferOptions()))
    task(worker)
    store.append([dict(finished=time.time(), outcome='complete', route='Copy', src=str(source), dst=str(destination),
        size=None, elapsed=1, detail='', descriptor={}, verification=job['verification'])])
    return store, job


def test_hash_evidence_contains_actual_checksums_and_no_duplicate_inner_copy(tmp_path):
    store, job = run_copy(tmp_path, TransferOptions(hash_verification='Required'))
    rows = list(report_rows(store))
    assert len(rows) == 1 and job['verification']['file_count'] == 1
    assert rows[0]['verification'] == 'checksum verified'
    checksum = next(c for c in rows[0]['checks'] if c['method'] == 'checksum')
    assert checksum['algorithm'] == 'SHA-256' and len(checksum['actual']) == 64
    assert checksum['expected'] == checksum['actual']


def test_size_only_does_not_claim_checksum_verification(tmp_path):
    store, _ = run_copy(tmp_path)
    assert list(report_rows(store))[0]['verification'] == 'size verified only'


def test_folder_reports_are_per_file_and_export_json_csv(tmp_path):
    store, _ = run_copy(tmp_path, folder=True)
    path = tmp_path / 'report.json'
    assert export_report(store, path) == 2
    data = json.loads(path.read_text())
    assert data['file_rows'] == 2
    assert {Path(r['source']).name for r in data['files']} == {'one', 'two'}
    output = tmp_path / 'report.csv'
    assert export_report(store, output) == 2
    with output.open(newline='') as stream: assert len(list(csv.reader(stream))) == 3


def test_legacy_history_is_unknown_and_csv_is_formula_safe(tmp_path):
    store = HistoryStore(tmp_path / 'old.sqlite')
    store.append([dict(finished=time.time(), outcome='complete', route='Copy', src='=1+1', dst='@command', size=1,
        elapsed=1, detail='', descriptor={})])
    assert list(report_rows(store))[0]['verification'] == 'not recorded'
    path = tmp_path / 'safe.csv'
    export_report(store, path)
    with path.open(newline='') as stream: row = list(csv.reader(stream))[1]
    assert row[2] == "'=1+1" and row[3] == "'@command"


def test_cancelled_export_preserves_existing_report(tmp_path):
    store, _ = run_copy(tmp_path)
    path = tmp_path / 'report.json'; path.write_text('keep')
    cancel = Event(); cancel.set()
    from fluxftp.core import Cancelled
    with pytest.raises(Cancelled): export_report(store, path, cancel=cancel)
    assert path.read_text() == 'keep'


def test_mismatch_takes_precedence_over_earlier_success():
    assert verification_status([dict(method='size', status='verified'), dict(method='checksum', status='mismatch')]) == 'mismatch'
