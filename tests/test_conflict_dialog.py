from threading import Event

from PySide6.QtCore import QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QCheckBox, QComboBox

from fluxftp.conflict_dialog import Request
from fluxftp.preferences import PreferencesDialog
from fluxftp.transfer_options import TransferOptions
from test_queue import window
from test_folder_sync import prepare, scan, wait_idle


def details(can_resume=True):
    return dict(src='C:/source/file.bin', dst='D:/target/file.bin', source_size=6000,
                target_size=3000, can_resume=can_resume)


def test_serialized_choices_apply_remaining_and_cancel(window):
    controller = window.conflicts
    a, b = Request(details(), Event()), Request(details(), Event())
    controller.enqueue(a); controller.enqueue(b)
    first = controller.dialog
    assert controller.current is a and len(controller.waiting) == 1
    first.remaining.setChecked(True)
    first.actions['Rename'].click()
    QTest.qWait(20)
    assert a.action == b.action == 'Auto Rename' and a.ready.is_set() and b.ready.is_set()
    assert controller.dialog is None
    controller.reset()
    c = Request(details(False), Event())
    controller.enqueue(c)
    assert not controller.dialog.actions['Resume'].isEnabled()
    c.cancel.set()
    QTest.qWait(150)
    assert c.ready.is_set() and not c.action and controller.dialog is None


def test_resume_remaining_reprompts_when_unavailable(window):
    controller = window.conflicts
    controller.choice = 'Auto Resume'
    request = Request(details(False), Event())
    controller.enqueue(request)
    assert controller.dialog and not request.ready.is_set()
    controller.dialog.reject()
    assert request.ready.is_set() and not request.action
    controller.reset()


def test_conflict_queue_worker_and_history(window, tmp_path):
    source, target = tmp_path / 'from', tmp_path / 'to'
    source.write_bytes(b'new contents'); target.write_bytes(b'old')
    window.preferences.setValue('Transfer/conflict_prompt', True)
    window.preferences.setValue('Transfer/hash_verification', 'Required')
    window.restore_queue_items([dict(src=str(source), dst=str(target), name='file', directory=False,
        resume=False, state='queued', workspace=0, source_site=None, destination_site=None)])
    timer = QTimer()
    timer.setInterval(10)
    seen = []
    def choose():
        if window.conflicts.dialog:
            seen.append(True)
            window.conflicts.dialog.actions['Overwrite'].click()
    timer.timeout.connect(choose); timer.start()
    try:
        window.play_queue()
        wait_idle(window)
    finally:
        timer.stop()
    assert seen and target.read_bytes() == b'new contents'
    rows, total = window.transfer_history.store.page()
    assert total == 1 and rows[0]['outcome'] == 'complete'
    assert 'Hash verified' in rows[0]['detail']


def test_sync_preserves_approved_conflict_and_hashes(window, tmp_path):
    dialog, source, target = prepare(window, tmp_path)
    (source / 'file').write_bytes(b'new'); (target / 'file').write_bytes(b'old old')
    window.preferences.setValue('Transfer/conflict_prompt', True)
    window.preferences.setValue('Transfer/hash_verification', 'Required')
    dialog.listings = [scan(None, str(p), Event()) for p in (source, target)]
    dialog.recompare()
    dialog.plan[0]['action'] = 'Left → Right'
    dialog.enqueue(); window.play_queue(); wait_idle(window)
    assert (target / 'file').read_bytes() == b'new'
    assert window.conflicts.dialog is None
    rows, _ = window.transfer_history.store.page()
    assert any('Hash verified' in row['detail'] for row in rows)


def test_reliability_preferences_persist(window):
    dialog = PreferencesDialog(window)
    checks = {c.text(): c for c in dialog.findChildren(QCheckBox)}
    checks['Ask before replacing each existing file'].setChecked(True)
    checks['Atomic remote upload (temporary file, then rename)'].setChecked(True)
    combo = next(c for c in dialog.findChildren(QComboBox) if c.findText('When available') >= 0)
    combo.setCurrentText('Required')
    options = TransferOptions.from_settings(window.preferences)
    assert options.conflict_prompt and options.atomic_upload and options.hash_verification == 'Required'
    dialog.close()


def test_verification_failure_is_not_automatically_retried(window):
    from test_queue import job_for
    from fluxftp.transfer_reliability import ReliabilityError
    calls = []
    def fail(_):
        calls.append(True)
        raise ReliabilityError('SHA-256 hash mismatch')
    job = job_for(window, fail)
    window.start_next(); wait_idle(window)
    assert calls == [True] and job['state'] == 'failed' and job.get('retries', 0) == 0
