import copy
import json
import time
from dataclasses import asdict
from threading import Event
from types import SimpleNamespace

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox
from fluxftp.automation import (Automation, command_plan, execute_script, next_occurrence,
                                validate_script)
from fluxftp.automation_ui import ScriptDialog, StepDialog, AutomationDialog
from fluxftp.core import Connection, Site, PROTOCOLS, Cancelled
from fluxftp.setup_wizard import SetupWizard
from test_queue import window
from test_integration import servers


def script(**values):
    return dict(name='Nightly listing', steps=[dict(action='List folder', site='Test', remote='/')], **values)


def settle(window):
    for _ in range(500):
        QTest.qWait(10)
        if not window.workers:
            return
    raise AssertionError('Worker did not finish')


@pytest.mark.parametrize('lines', [['PASS secret'], ['SITE CHPASS secret'], ['SITE USER %d[User]'],
    ['SITE CAT %f'], ['RETR /file'], ['NOOP\r\nDELE /file'], ['USER someone'], ['SITE CHANGE user PASSWD secret']])
def test_scripts_reject_prompted_or_credential_commands(lines):
    with pytest.raises(ValueError):
        command_plan(lines)


def test_schema_paths_and_repeat_coalescing():
    assert next_occurrence(100, 5, 1020) == 1300
    assert next_occurrence(100, 0, 1020) == 0
    assert next_occurrence(100, 5, 400) == 700
    raw = script()
    raw['password'] = 'must not persist'
    assert 'password' not in validate_script(raw)
    for path in ('relative', '/a/../b', '/a\nNOOP'):
        raw['steps'][0]['remote'] = path
        with pytest.raises(ValueError):
            validate_script(raw)


def test_persistence_restart_interruption_and_second_instance_lock(window):
    controller = window.automation
    key = controller.put(None, script(enabled=True, next_run=time.time() + 3600, interval=60))
    other = Automation(window)
    assert not other.owner
    with pytest.raises(ValueError):
        other.put(None, script())
    other.shutdown()
    controller.scripts[key]['running'] = True
    controller.persist()
    controller.shutdown()
    restarted = Automation(window)
    try:
        assert restarted.owner
        assert not restarted.scripts[key]['enabled']
        assert 'Interrupted' in restarted.scripts[key]['last']
        assert not json.loads(restarted.path.read_text())['scripts'][key].get('running')
    finally:
        restarted.shutdown()


def test_atomic_save_failure_retains_previous_script(window, monkeypatch):
    c = window.automation
    key = c.put(None, script())
    before = c.path.read_bytes()
    def fail(*_):
        raise OSError('disk error')
    monkeypatch.setattr('fluxftp.automation.os.replace', fail)
    with pytest.raises(OSError):
        c.put(key, dict(script(), name='Changed'))
    assert c.scripts[key]['name'] == 'Nightly listing'
    assert c.path.read_bytes() == before


def test_corrupt_configuration_is_preserved(window):
    c = window.automation
    c.shutdown()
    c.path.write_text('{broken', encoding='utf-8')
    other = Automation(window)
    try:
        assert other.problem
        with pytest.raises(ValueError):
            other.put(None, script())
        assert c.path.read_text() == '{broken'
    finally:
        other.shutdown()


def test_due_schedule_claim_and_no_duplicate_start(window, monkeypatch):
    c = window.automation
    c.timer.stop()
    key = c.put(None, script(enabled=True, next_run=time.time() - 1000, interval=5))
    calls = []
    class ConnectionStub:
        site = Site()
        password = 'secret'
        proxy = None
        def __enter__(self):
            return self
        def __exit__(self, *_):
            pass
        def list(self, path):
            record = json.loads(c.path.read_text())['scripts'][key]
            assert record['running'] and record['next_run'] > time.time()
            calls.append(path)
            return []
    monkeypatch.setattr('fluxftp.automation.saved_connection', lambda *_: ConnectionStub())
    settle(window)
    c.tick()
    c.tick()
    settle(window)
    assert calls == ['/']
    assert c.scripts[key]['last'] == 'Completed all steps.'
    c.tick()
    assert len(calls) == 1


def test_failure_stops_steps_disables_schedule_and_redacts(window, monkeypatch):
    c = window.automation
    c.timer.stop()
    key = c.put(None, dict(script(enabled=True, next_run=time.time() + 500),
                          steps=script()['steps'] * 2))
    calls = []
    class Broken:
        site = Site()
        password = 'secret-value'
        proxy = None
        def __enter__(self):
            return self
        def __exit__(self, *_):
            pass
        def list(self, path):
            calls.append(path)
            raise OSError('refused secret-value')
    monkeypatch.setattr('fluxftp.automation.saved_connection', lambda *_: Broken())
    settle(window)
    c.start(key)
    settle(window)
    assert calls == ['/']
    assert not c.scripts[key]['enabled']
    assert 'secret-value' not in c.path.read_text()
    assert '[REDACTED]' in c.scripts[key]['last']


def test_busy_queue_defers_schedules(window, monkeypatch):
    c = window.automation
    c.put(None, script(enabled=True, next_run=time.time() - 1))
    calls = []
    monkeypatch.setattr(c, 'start', lambda *a, **k: calls.append(a))
    window.pending.append({'held': True})
    c.tick()
    assert not calls
    window.pending.clear()


def test_editor_reorders_saves_and_cancel_does_not_mutate(window, tmp_path):
    c = window.automation
    key = c.put(None, script())
    editor = ScriptDialog(c, key)
    editor.steps.append(dict(action='List folder', site='Test', remote='/second'))
    editor.refresh(1)
    editor.move(-1)
    editor.accept()
    assert c.scripts[key]['steps'][0]['remote'] == '/second'
    editor = ScriptDialog(c, key)
    editor.steps.clear()
    editor.reject()
    assert len(c.scripts[key]['steps']) == 2
    window.sites['Test'] = asdict(Site(name='Test'))
    step = StepDialog(window, window)
    step.action.setCurrentText('Upload file')
    step.local.setText(str(tmp_path / 'source.bin'))
    step.remote.setText('/target.bin')
    assert step.value()['exists'] == 'Fail'
    step.reject()
    manager = AutomationDialog(c)
    assert manager.tree.topLevelItemCount() == 1
    manager.close()


@pytest.mark.parametrize('tls', [False, True])
def test_ftp_script_live_transfer_commands_and_failure(servers, tmp_path, tls):
    root, site = servers(tls)
    source, target = tmp_path / 'source.bin', tmp_path / 'download.bin'
    source.write_bytes(bytes(range(256)) * 100)
    steps = [dict(action='Upload file', site='Test', remote='/payload.bin', local=str(source), exists='Fail'),
             dict(action='FTP commands', site='Test', remote='/', lines=['NOOP', 'PWD']),
             dict(action='Download file', site='Test', remote='/payload.bin', local=str(target), exists='Fail')]
    worker = SimpleNamespace(cancel=Event(), report=lambda *_: None)
    factory = lambda: Connection(site, 'password')
    execute_script(validate_script(dict(name='Round trip', steps=steps)), [factory() for _ in steps], worker, lambda *_: None)
    assert target.read_bytes() == source.read_bytes() == (root / 'payload.bin').read_bytes()
    # Existing-file policy fails before replacing the remote file.
    source.write_bytes(b'changed')
    with pytest.raises(ValueError, match='already exists'):
        execute_script(dict(steps=[steps[0]]), [factory()], worker, lambda *_: None)
    assert (root / 'payload.bin').read_bytes() != b'changed'
    steps[0]['exists'] = 'Skip'
    execute_script(dict(steps=[steps[0]]), [factory()], worker, lambda *_: None)
    assert (root / 'payload.bin').read_bytes() != b'changed'
    steps[0]['exists'] = 'Overwrite'
    execute_script(dict(steps=[steps[0]]), [factory()], worker, lambda *_: None)
    assert (root / 'payload.bin').read_bytes() == b'changed'
    bad = dict(action='FTP commands', site='Test', remote='/', lines=['INVALID', 'DELE payload.bin'])
    with pytest.raises(Exception):
        execute_script(dict(steps=[bad]), [factory()], worker, lambda *_: None)
    assert (root / 'payload.bin').exists()
    worker.cancel.set()
    with pytest.raises(Cancelled):
        execute_script(dict(steps=[steps[0]]), [factory()], worker, lambda *_: None)


def test_wizard_test_invalidation_and_new_workspace(window, tmp_path, monkeypatch):
    window.sites['Test'] = asdict(Site(name='Test', root='/public'))
    class Fake:
        site = Site(name='Test', root='/public')
        password = 'hidden'
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def list(self, path):
            assert path == '/public'
            return [1, 2]
    monkeypatch.setattr('fluxftp.setup_wizard.saved_connection', lambda *a, **k: Fake())
    wizard = SetupWizard(window)
    wizard.show()
    wizard.local.setText(str(tmp_path))
    wizard.next()
    wizard.test()
    settle(window)
    assert wizard.tested == wizard.key()
    assert '2 entries' in wizard.status.text()
    wizard.remote.setText('/changed')
    assert wizard.tested is None
    wizard.remote.setText('/public')
    wizard.test()
    settle(window)
    wizard.next()
    assert wizard.currentId() == 2
    count = window.tabs.count()
    monkeypatch.setattr('fluxftp.app.Pane.refresh', lambda *_: None)
    wizard.accept()
    assert window.tabs.count() == count + 1
    left, right = window.current_panes()
    assert left.mode == 'Local' and left.path == str(tmp_path)
    assert right.mode == 'Remote' and right.path == '/public'
    assert right.password == 'hidden'


def test_cancel_running_script_stops_before_next_step(window, monkeypatch):
    from PySide6.QtCore import QThread
    c = window.automation
    c.timer.stop()
    key = c.put(None, dict(script(), steps=script()['steps'] * 2))
    entered = Event()
    calls = []
    class Waiting:
        site = Site()
        password = ''
        proxy = None
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def list(self, path):
            calls.append(path)
            entered.set()
            if QThread.currentThread().cancel.wait(3):
                raise Cancelled('Cancelled')
            raise AssertionError('Cancellation did not arrive')
    monkeypatch.setattr('fluxftp.automation.saved_connection', lambda *_: Waiting())
    settle(window)
    c.start(key)
    assert entered.wait(2)
    c.cancel()
    settle(window)
    assert calls == ['/']
    assert 'Cancelled' in c.scripts[key]['last']
    assert c.running is None


def test_one_time_run_is_consumed_and_manual_run_preserves_schedule(window, monkeypatch):
    c = window.automation
    c.timer.stop()
    key = c.put(None, script(enabled=True, next_run=time.time() - 1))
    class Listing:
        site = Site()
        password = ''
        proxy = None
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def list(self, path): return []
    monkeypatch.setattr('fluxftp.automation.saved_connection', lambda *_: Listing())
    settle(window)
    due = c.scripts[key]['next_run']
    c.start(key)
    settle(window)
    assert c.scripts[key]['next_run'] == due and c.scripts[key]['enabled']
    c.tick()
    settle(window)
    assert not c.scripts[key]['enabled'] and c.scripts[key]['next_run'] == 0
    assert len(c.scripts[key]['history']) == 2


def test_wizard_close_waits_for_test_and_does_not_open_workspace(window, tmp_path, monkeypatch):
    from PySide6.QtCore import QThread
    window.sites['Test'] = asdict(Site(name='Test'))
    entered = Event()
    class Waiting:
        site = Site(name='Test')
        password = ''
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def list(self, path):
            entered.set()
            QThread.currentThread().cancel.wait(3)
            return []
    monkeypatch.setattr('fluxftp.setup_wizard.saved_connection', lambda *a, **k: Waiting())
    wizard = SetupWizard(window)
    wizard.show()
    wizard.next()
    count = window.tabs.count()
    wizard.test()
    assert entered.wait(2)
    wizard.reject()
    assert wizard.worker is not None
    settle(window)
    assert wizard.worker is None and wizard.tested is None
    assert not wizard.isVisible() and window.tabs.count() == count
