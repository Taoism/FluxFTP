from types import SimpleNamespace
from uuid import uuid4

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QWidget, QLineEdit
from fluxftp.cloud_accounts import CloudAccountWizard, CloudAccounts
from test_cloud import service


def account(service, name='S3 test'):
    identity = uuid4().hex
    service.call('config/create', {'name': 'c' + identity, 'type': 's3', 'parameters': {
        'provider': 'AWS', 'region': 'us-east-1', 'env_auth': 'false',
        'access_key_id': 'test-access', 'secret_access_key': 'test-secret'},
        'opt': {'nonInteractive': True}})
    service.save_account(identity, name, 'Amazon S3')
    return identity


@pytest.fixture
def edit_window(service):
    app = QApplication.instance() or QApplication([])
    window = QWidget()
    window.cloud_service = service
    window.sites = {}
    window.workers = set()
    window.active = {}
    def run(work, done, failed):
        try:
            value = work(None)
        except Exception as error:
            failed(str(error))
        else:
            done(value)
    window.run = run
    yield window
    window.close()


def test_edit_existing_s3_preserves_identity_and_credentials(edit_window, service):
    identity = account(service)
    other = account(service, 'Other account')
    old_other = service.call('config/get', {'name': 'c' + other})
    edit_window.sites = {'site': {'cloud_account': identity}}
    dialog = CloudAccountWizard(edit_window, identity=identity)
    folder = dialog.service.directory
    dialog.name.setText('Renamed S3')
    assert not dialog.provider.isEnabled()
    dialog.show()
    saw_region = False
    for _ in range(60):
        if dialog.completed:
            break
        if dialog.answer.accessibleName() == 'region':
            saw_region = True
            assert 'us-east-1' in dialog.choices.currentText()
            dialog.choices.setEditText('us-west-2')
        if dialog.answer.accessibleName() in ('access_key_id', 'secret_access_key'):
            assert dialog.answer.echoMode() == QLineEdit.EchoMode.Password
        dialog.advance()
    assert dialog.completed, dialog.status.text()
    assert saw_region and not folder.exists()
    assert service.accounts()[identity]['name'] == 'Renamed S3'
    details = service.call('config/get', {'name': 'c' + identity})
    assert details['region'] == 'us-west-2'
    assert details['access_key_id'] == 'test-access'
    assert details['secret_access_key'] == 'test-secret'
    assert service.call('config/get', {'name': 'c' + other}) == old_other
    assert edit_window.sites['site']['cloud_account'] == identity
    assert b'test-secret' not in service.config_path.read_bytes()
    dialog.close()


@pytest.mark.parametrize('start', [False, True])
def test_cancel_edit_discards_draft(edit_window, service, start):
    identity = account(service)
    baseline = service.config_path.read_bytes(), service.settings_path.read_bytes()
    dialog = CloudAccountWizard(edit_window, identity=identity)
    folder = dialog.service.directory
    dialog.show()
    if start:
        dialog.advance()
    dialog.reject()
    assert not folder.exists()
    assert (service.config_path.read_bytes(), service.settings_path.read_bytes()) == baseline


def test_edit_waits_for_other_operations_before_commit(edit_window, service):
    identity = account(service)
    baseline = service.config_path.read_bytes(), service.settings_path.read_bytes()
    dialog = CloudAccountWizard(edit_window, identity=identity)
    edit_window.workers.add(object())
    with pytest.raises(RuntimeError, match='current operations'):
        dialog.finish_account()
    assert (service.config_path.read_bytes(), service.settings_path.read_bytes()) == baseline
    dialog.reject()


def test_edit_refuses_to_overwrite_concurrent_change(service):
    identity = account(service)
    draft = service.begin_edit(identity)
    try:
        service.save_account(identity, 'Changed elsewhere', 'Amazon S3')
        baseline = service.config_path.read_bytes(), service.settings_path.read_bytes()
        with pytest.raises(RuntimeError, match='changed while editing'):
            draft.commit_edit()
        assert (service.config_path.read_bytes(), service.settings_path.read_bytes()) == baseline
    finally:
        draft.discard_edit()


def test_edit_restores_both_files_if_commit_fails(service, monkeypatch):
    identity = account(service)
    baseline = service.config_path.read_bytes(), service.settings_path.read_bytes()
    draft = service.begin_edit(identity)
    draft.save_account(identity, 'Draft name', 'Amazon S3')
    replace = draft._replace_file
    failed = False
    def fail_once(path, content):
        nonlocal failed
        if path == service.settings_path and not failed:
            failed = True
            raise OSError('simulated write failure')
        replace(path, content)
    monkeypatch.setattr(draft, '_replace_file', fail_once)
    try:
        with pytest.raises(OSError, match='simulated'):
            draft.commit_edit()
        assert (service.config_path.read_bytes(), service.settings_path.read_bytes()) == baseline
    finally:
        draft.discard_edit()


def test_cloud_accounts_edit_button_and_double_click(edit_window, service, monkeypatch):
    identity = account(service)
    calls = []
    monkeypatch.setattr('fluxftp.cloud_accounts.CloudAccountWizard',
        lambda window, parent, identity: SimpleNamespace(exec=lambda: calls.append(identity) or 0))
    dialog = CloudAccounts(edit_window)
    assert not dialog.edit_button.isEnabled()
    row = dialog.tree.topLevelItem(0)
    dialog.tree.setCurrentItem(row)
    assert dialog.edit_button.isEnabled()
    dialog.edit_button.click()
    dialog.tree.itemDoubleClicked.emit(row, 0)
    assert calls == [identity, identity]
    dialog.close()
