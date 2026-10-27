import json
import time
from types import SimpleNamespace

from fluxftp.transfer_history import HistoryStore, HistoryDialog
from fluxftp.transfer_options import TransferOptions
from test_queue import window, job_for
from test_ui import settle


def record(index, outcome='complete'):
    return dict(finished=time.time(), outcome=outcome, route='Copy', src=f'/source/{index}',
                dst=f'/target/{index}', size=index, elapsed=1.5, detail='result', descriptor={})


def test_history_persists_pages_search_and_retention(tmp_path):
    store = HistoryStore(tmp_path / 'history.sqlite')
    records = [record(i, 'failed' if i % 2 else 'complete') for i in range(240)]
    records[0]['finished'] -= 100 * 86400
    store.append(records, days=90, count=205)
    reopened = HistoryStore(store.path)
    first, total = reopened.page()
    assert total == 205 and len(first) == 100 and first[0]['src'] == '/source/239'
    last, total = reopened.page(offset=200)
    assert len(last) == 5
    failed, total = reopened.page(search='/source/23', outcome='Failed')
    assert total == 5 and all(row['outcome'] == 'failed' for row in failed)
    reopened.retain(90, 100)
    assert reopened.page()[1] == 100
    reopened.clear()
    assert reopened.page()[1] == 0


def local_job(window, tmp_path, name='source.bin'):
    source, target = tmp_path / name, tmp_path / ('target-' + name)
    source.write_bytes(b'content')
    job = job_for(window, None, 'Copy', name)
    job.update(src=str(source), dst=str(target), name=name, directory=False, resume=False,
               options=TransferOptions(), source_password='private-source', destination_password='private-destination')
    job['task'] = window.make_queue_task(job)
    return job


def test_success_is_recorded_before_queue_cleanup_and_no_passwords(window, tmp_path):
    job = local_job(window, tmp_path)
    job['route'] = 'Download'
    window.start_next()
    settle(window)
    assert not window.queue_jobs
    rows, total = window.transfer_history.store.page()
    assert total == 1 and rows[0]['outcome'] == 'complete'
    payload = json.loads(rows[0]['descriptor'])
    assert payload['src'] == job['src'] and payload['dst'] == job['dst']
    assert 'password' not in rows[0]['descriptor']
    assert b'private-source' not in window.transfer_history.store.path.read_bytes()
    assert (tmp_path / 'target-source.bin').read_bytes() == b'content'


def test_failure_retry_deduplicates_and_uses_held_queue(window, tmp_path, monkeypatch):
    job = local_job(window, tmp_path)
    window.pending.clear()
    job.update(state='failed', transfer_outcome='failed')
    job['row'].setToolTip(7, 'error private-source private-destination')
    window.transfer_history.record(job)
    rows, _ = window.transfer_history.store.page(outcome='Failed')
    assert 'private-source' not in rows[0]['detail'] and 'private-destination' not in rows[0]['detail']
    assert window.transfer_history.retry(rows * 2) == 1
    assert window.pending == [job] and job['held']
    assert window.transfer_history.retry(rows) == 0
    window.discard_queue_jobs([job])
    assert window.transfer_history.retry(rows) == 1
    restored = window.queue_jobs[-1]
    assert restored['held'] and restored['needs_credentials'] and 'source_password' not in restored
    window.play_queue()
    settle(window)
    assert (tmp_path / 'target-source.bin').read_bytes() == b'content'


def test_buffered_cancellation_records_every_file_once(window, tmp_path):
    from test_queue_buffer import buffered_folder
    parent = buffered_folder(window, tmp_path, 300)
    window.stop_queue()
    assert window.transfer_history.store.page(outcome='Cancelled')[1] == 300
    window.stop_queue()
    assert window.transfer_history.store.page()[1] == 300


def test_nested_skips_are_retained_after_rows_clear(window, tmp_path):
    root = tmp_path / 'folder'
    root.mkdir()
    (root / 'omit').mkdir()
    (root / 'keep.bin').write_bytes(b'x')
    job = job_for(window, None, 'Download', 'folder')
    job.update(src=str(root), dst=str(tmp_path / 'target'), name='folder', directory=True, resume=False,
               options=TransferOptions(skip_enabled=True, skip_folders=True, skip_rules='[{"pattern":"omit","regex":false}]'))
    job['task'] = window.make_queue_task(job)
    window.start_next()
    settle(window)
    rows, total = window.transfer_history.store.page()
    assert total == 2 and {row['outcome'] for row in rows} == {'skipped', 'complete'}
    assert not window.queue_jobs


def test_history_dialog_filters_and_copies_without_loading_entire_store(window):
    from PySide6.QtWidgets import QApplication
    window.transfer_history.store.append([record(i, 'failed') for i in range(140)])
    dialog = HistoryDialog(window.transfer_history)
    assert dialog.tree.topLevelItemCount() == 100
    dialog.page(100)
    assert dialog.tree.topLevelItemCount() == 40
    row = dialog.tree.topLevelItem(0)
    row.setSelected(True)
    dialog.copy('src')
    assert QApplication.clipboard().text() == row.text(5)
    dialog.search.setText('/source/139')
    dialog.reset()
    assert dialog.tree.topLevelItemCount() == 1
    dialog.close()


def test_history_storage_failure_does_not_break_transfer(window, tmp_path, monkeypatch):
    import sqlite3
    local_job(window, tmp_path)
    def fail(*args): raise sqlite3.OperationalError('unavailable')
    monkeypatch.setattr(window.transfer_history.store, 'append', fail)
    window.start_next()
    settle(window)
    assert (tmp_path / 'target-source.bin').read_bytes() == b'content'
    assert 'history could not be saved' in window.log.toPlainText()


def test_selected_cancel_and_auto_rename_history(window, tmp_path):
    job = local_job(window, tmp_path)
    other = local_job(window, tmp_path, 'other.bin')
    job['row'].setSelected(True)
    window.cancel_selected()
    assert window.transfer_history.store.page(outcome='Cancelled')[1] == 1
    assert window.pending == [other]
    window.pending.clear()
    other.update(state='complete', actual_dst=str(tmp_path / 'renamed.bin'))
    window.transfer_history.record(other)
    rows, total = window.transfer_history.store.page(outcome='Complete')
    assert rows[0]['dst'] == other['actual_dst']


def test_retry_does_not_duplicate_unloaded_buffered_file(window, tmp_path):
    from test_queue_buffer import buffered_folder
    parent = buffered_folder(window, tmp_path, 300)
    item = next(iter(parent['buffer']))
    failed = record(1, 'failed')
    failed['descriptor'] = json.dumps(item)
    assert window.transfer_history.retry([failed]) == 0
