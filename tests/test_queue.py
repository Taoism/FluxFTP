import json
import os
from dataclasses import asdict

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QTreeWidgetItem
from fluxftp import app as module
from fluxftp.core import Site, Cancelled
from fluxftp.preferences import PreferencesDialog
from fluxftp.queue_manager import read_queue, write_queue
from fluxftp.transfer_options import TransferOptions
from test_ui import settle


@pytest.fixture
def window(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / 'settings.ini'), QSettings.Format.IniFormat)
    monkeypatch.setattr(module, 'settings_store', lambda: settings)
    window = module.Window()
    yield window
    window.stop_queue()
    settle(window)
    window.close()


def job_for(window, task, route='Download', name='item'):
    row = QTreeWidgetItem([name, '', '', '', '', '/source/' + name, '/target/' + name, 'Queued'])
    window.queue.addTopLevelItem(row)
    job = dict(row=row, task=task, route=route, workspace=window.tabs.currentWidget(),
               source_site=None, destination_site=None, state='queued', retries=0)
    job = window.new_queue_job(job)
    window.queue_jobs.append(job)
    window.pending.append(job)
    return job


@pytest.mark.parametrize('cycle,after,attempts', [(True, True, 3), (True, False, 3), (False, True, 3), (False, False, 1)])
def test_retry_switches_share_one_budget(window, cycle, after, attempts):
    window.preferences.setValue('Queue/cycle_retry', cycle)
    window.preferences.setValue('Queue/retry_after', after)
    window.preferences.setValue('Queue/retry_download', 2)
    calls = []
    def fail(_):
        calls.append(True)
        raise OSError('broken transfer')
    job = job_for(window, fail)
    window.start_next()
    settle(window)
    assert len(calls) == attempts
    assert job['state'] == 'failed' and window.queue_batch_failed


def test_deferred_retry_runs_after_original_queue(window):
    window.preferences.setValue('Transfer/single_connection', True)
    window.preferences.setValue('Queue/cycle_retry', False)
    events = []
    def first(_):
        events.append('first')
        if events.count('first') == 1: raise OSError('try again')
    first_job = job_for(window, first)
    job_for(window, lambda _: events.append('second'))
    window.start_next()
    settle(window)
    assert events == ['first', 'second', 'first']
    assert first_job['state'] == 'complete' and not window.queue_batch_failed


@pytest.mark.parametrize('route,attempts', [('Upload', 3), ('Download', 6), ('FXP', 1)])
def test_default_repeat_counts(window, route, attempts):
    calls = []
    def fail(_):
        calls.append(True)
        raise OSError('failed')
    job_for(window, fail, route)
    window.start_next()
    settle(window)
    assert len(calls) == attempts


def test_cancel_never_retries(window):
    calls = []
    def cancel(_):
        calls.append(True)
        raise Cancelled('Cancelled')
    job = job_for(window, cancel)
    window.start_next()
    settle(window)
    assert len(calls) == 1 and job['state'] == 'cancelled'


def descriptor(tmp_path, **changes):
    value = dict(source_site=None, destination_site=None, src=str(tmp_path / 'source'), dst=str(tmp_path / 'target'),
                 name='source', directory=False, resume=False, workspace=0, retries=0, state='queued')
    value.update(changes)
    return value


def test_restore_active_queue_and_run_local_copy(window, tmp_path):
    source = tmp_path / 'source'
    source.write_bytes(b'recovered contents')
    path = tmp_path / 'queue.json'
    write_queue(path, [descriptor(tmp_path, state='active', workspace=1)])
    window.initialize_queue_storage(path)
    assert window.paused and len(window.pending) == 1 and window.tabs.count() == 2
    assert not (tmp_path / 'target').exists()
    window.play_queue()
    settle(window)
    assert (tmp_path / 'target').read_bytes() == source.read_bytes()
    assert read_queue(path) == []


def test_persistence_excludes_passwords_and_completed_jobs(window, tmp_path):
    window.initialize_queue_storage(tmp_path / 'queue.json')
    job = job_for(window, lambda _: None, 'Upload')
    job.update(descriptor(tmp_path), workspace=window.tabs.currentWidget(), source_site=None,
               destination_site=Site(host='ftp.example.test', username='user'),
               source_password='DO-NOT-SAVE-ONE', destination_password='DO-NOT-SAVE-TWO',
               options=TransferOptions())
    assert window.save_queue_state()
    text = window.queue_file.read_text('utf-8')
    assert 'DO-NOT-SAVE' not in text and 'password' not in text
    assert len(read_queue(window.queue_file)) == 1
    job['state'] = 'failed'
    window.preferences.setValue('Queue/keep_failed', False)
    window.save_queue_state()
    assert read_queue(window.queue_file) == []
    window.preferences.setValue('Queue/keep_failed', True)
    window.save_queue_state()
    assert read_queue(window.queue_file)[0]['state'] == 'failed'
    window.preferences.setValue('Queue/auto_save', False)
    before = window.queue_file.read_bytes()
    job['state'] = 'complete'
    window.save_queue_state()
    assert window.queue_file.read_bytes() == before
    window.pending.clear()


def test_bad_queue_is_not_overwritten(window, tmp_path):
    path = tmp_path / 'queue.json'
    path.write_text('{broken', encoding='utf-8')
    window.initialize_queue_storage(path)
    assert not window.queue_storage_enabled
    window.save_queue_state()
    assert path.read_text() == '{broken'


def test_reload_disabled(window, tmp_path):
    path = tmp_path / 'queue.json'
    write_queue(path, [descriptor(tmp_path)])
    window.preferences.setValue('Queue/reload', False)
    window.initialize_queue_storage(path)
    assert not window.pending


def test_clear_only_affected_site_and_do_not_retry_active(window):
    a, b = Site(host='a.example'), Site(host='b.example')
    pane = window.current_panes()[0]
    pane.set_remote(a, 'password')
    job_a = job_for(window, lambda _: None)
    job_b = job_for(window, lambda _: None)
    job_a['source_site'], job_b['source_site'] = a, b
    window.preferences.setValue('Queue/clear_logout', True)
    window.disconnect_pane(pane)
    assert job_a not in window.pending and job_b in window.pending
    assert pane.site is None and pane.password == ''
    job_a['state'] = 'active'
    window.clear_site_queue(pane, a, logout=True)
    assert job_a['suppress_retry']
    window.preferences.setValue('Queue/clear_switch', True)
    pane.set_remote(b)
    pane.set_remote(a)
    assert job_b not in window.pending


def test_reset_failed_is_explicit_and_resets_budget(window):
    job = job_for(window, lambda _: None)
    window.pending.clear()
    job.update(state='failed', retries=5)
    window.reset_failed_jobs(automatic=True)
    assert not window.pending
    window.preferences.setValue('Queue/reset_failed', True)
    window.reset_failed_jobs(automatic=True)
    assert window.pending == [job] and job['retries'] == 0


def test_queue_ui_and_status_paths(window):
    dialog = PreferencesDialog(window)
    assert len(dialog.queue_checks) == 9
    assert all(dialog.queue_checks[key].isChecked() for key in ('reload', 'auto_save', 'keep_failed', 'cycle_retry', 'retry_after'))
    assert not any(dialog.queue_checks[key].isChecked() for key in ('clear_switch', 'reset_failed', 'clear_logout', 'show_paths'))
    assert [dialog.queue_retries[key].value() for key in ('Upload', 'Download', 'FXP')] == [2, 5, 0]
    dialog.queue_checks['show_paths'].setChecked(True)
    dialog.queue_retries['Upload'].setValue(4)
    assert window.preferences.value('Queue/retry_upload', type=int) == 4
    job_for(window, lambda _: None)
    window.start_next()
    settle(window)
    assert '/source/item -> /target/item' in window.log.toPlainText()
    dialog.close()


def test_restore_credentials_only_on_play(window, tmp_path, monkeypatch):
    site = Site(host='ftp.example.test', username='user')
    path = tmp_path / 'queue.json'
    write_queue(path, [descriptor(tmp_path, destination_site=asdict(site))])
    window.initialize_queue_storage(path)
    prompts = []
    from fluxftp import queue_manager
    monkeypatch.setattr(queue_manager.QInputDialog, 'getText', lambda *args: (prompts.append(True) or 'secret', False))
    window.play_queue()
    assert window.paused and prompts == [True] and len(window.pending) == 1
    monkeypatch.setattr(queue_manager.QInputDialog, 'getText', lambda *args: ('secret', True))
    assert window.prepare_restored_jobs()
    assert window.pending[0]['destination_password'] == 'secret'
    window.save_queue_state()
    assert 'secret' not in path.read_text()


@pytest.mark.parametrize('accepted', [True, False])
def test_certificate_prompt_keeps_queue_worker_tracked(window, monkeypatch, accepted):
    from PySide6.QtTest import QTest
    from fluxftp.core import CertificateTrustRequired
    calls = []
    def transfer(_):
        calls.append(True)
        if len(calls) == 1:
            raise CertificateTrustRequired(Site(host='example.test'), b'certificate', ValueError('untrusted'))
    def trust(_):
        QTest.qWait(30)  # A modal prompt processes the original worker's finished signal.
        return accepted
    monkeypatch.setattr(window, '_trust_server_certificate', trust)
    job = job_for(window, transfer)
    window.start_next()
    settle(window)
    assert len(calls) == (2 if accepted else 1)
    assert job['state'] == ('complete' if accepted else 'cancelled')
    assert job['retries'] == 0


def test_paused_queue_close_requires_successful_save(window, tmp_path, monkeypatch):
    from PySide6.QtGui import QCloseEvent
    from fluxftp import queue_manager
    window.initialize_queue_storage(tmp_path / 'queue.json')
    job = job_for(window, lambda _: None)
    job.update(descriptor(tmp_path), workspace=window.tabs.currentWidget())
    window.paused = True
    event = QCloseEvent()
    window.closeEvent(event)
    assert event.isAccepted() and len(read_queue(window.queue_file)) == 1
    def fail(*_): raise OSError('disk full')
    monkeypatch.setattr(queue_manager, 'write_queue', fail)
    monkeypatch.setattr(module.QMessageBox, 'information', lambda *_: None)
    event = QCloseEvent()
    window.closeEvent(event)
    assert not event.isAccepted()


def test_old_cancellation_does_not_fail_new_batch(window, tmp_path):
    from fluxftp.core import Entry
    old = job_for(window, lambda _: None)
    window.stop_queue()
    assert old['state'] == 'cancelled'
    source_path, target_path = tmp_path / 'source', tmp_path / 'target'
    source_path.mkdir()
    target_path.mkdir()
    (source_path / 'file').write_bytes(b'new batch')
    window.preferences.setValue('Confirm/queue', False)
    left, right = window.current_panes()
    left.change_mode('Local')
    right.change_mode('Local')
    left.path, right.path = str(source_path), str(target_path)
    window.enqueue_items(left, right, [Entry('file', False, 9)])
    settle(window)
    assert (target_path / 'file').read_bytes() == b'new batch'
    assert not window.queue_batch_failed


def test_fast_failing_retries_cannot_strand_active_job(window):
    window.preferences.setValue('Queue/retry_upload', 30)
    attempts = []
    def fast_failure(_):
        attempts.append(True)
        raise OSError('immediate failure')
    job = job_for(window, fast_failure, 'Upload')
    window.start_next()
    settle(window)
    assert len(attempts) == 31 and job['state'] == 'failed'
    assert not window.active and not window.pending
