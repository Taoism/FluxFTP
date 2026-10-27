from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication, QWidget
from fluxftp.cloud_accounts import CloudAccountWizard, setup_error


@pytest.fixture
def wizard():
    app = QApplication.instance() or QApplication([])
    parent = QWidget()
    requests, replies, saved = [], [], []
    def job(endpoint, parameters, **kwargs):
        requests.append((endpoint, parameters))
        return replies.pop(0)
    parent.cloud_service = SimpleNamespace(job=job,
        finish_account=lambda *args: saved.append(args), call=lambda *a, **k: {})
    parent.run = lambda work, done, failed: done(work(None))
    dialog = CloudAccountWizard(parent, provider='Microsoft OneDrive')
    dialog.name.setText('OneDrive')
    dialog.show()
    app.processEvents()
    yield dialog, requests, replies, saved
    dialog.reject()
    parent.close()


def test_drive_choices_show_labels_and_send_ids(wizard):
    dialog, requests, replies, saved = wizard
    replies.append({'State': 'driveid_final', 'Option': {
        'Name': 'config_driveid', 'Help': 'Select drive you want to use',
        'Default': 'personal-id', 'Required': True, 'Exclusive': False,
        'Examples': [{'Value': 'personal-id', 'Help': 'Personal (personal)'},
                     {'Value': 'work-id', 'Help': 'Work (business)'}]}})
    dialog.advance()
    assert dialog.choices.isVisible() and dialog.choices.isEditable()
    assert 'Personal (personal)' in dialog.choices.currentText()
    dialog.choices.setCurrentIndex(1)
    replies.append({})
    dialog.advance()
    assert requests[-1][1]['opt']['result'] == 'work-id'
    assert saved and dialog.completed


def test_custom_drive_id_is_preserved(wizard):
    dialog, requests, replies, saved = wizard
    replies.append({'State': 'driveid_final', 'Option': {
        'Name': 'config_driveid', 'Default': 'first',
        'Examples': [{'Value': 'first', 'Help': 'Personal'}]}})
    dialog.advance()
    dialog.choices.setEditText('custom-drive')
    replies.append({})
    dialog.advance()
    assert requests[-1][1]['opt']['result'] == 'custom-drive'


@pytest.mark.parametrize('error,result', [
    ('Failed to query root for drive SECRET: https://example/?token=SECRET', ''),
    ('', 'backend-result')])
def test_optionless_response_continues_without_stale_answer(wizard, error, result):
    dialog, requests, replies, saved = wizard
    replies.append({'State': 'driveid_final', 'Option': {
        'Name': 'config_driveid', 'Default': 'stale-id'}})
    dialog.advance()
    replies.append({'State': 'choose_type', 'Error': error, 'Result': result})
    dialog.advance()
    assert not dialog.answer.isVisible() and not dialog.choices.isVisible()
    assert 'SECRET' not in dialog.status.text()
    assert 'incomplete' not in dialog.status.text()
    assert not saved
    replies.append({'State': 'choose_type_done', 'Option': {
        'Name': 'config_type', 'Default': 'onedrive'}})
    dialog.advance()
    assert requests[-1][1]['opt']['state'] == 'choose_type'
    assert requests[-1][1]['opt']['result'] == result
    assert dialog.pending_result is None


def test_setup_error_never_echoes_provider_secrets():
    assert 'SECRET' not in setup_error('<b>SECRET</b> token=SECRET')
    assert 'expired' in setup_error('invalid_grant SECRET')
    assert 'accessible drive' in setup_error('No drives found SECRET')
    message = setup_error('Failed to query root for drive SECRET: ObjectHandle is Invalid token=SECRET')
    assert 'default drive' in message and 'SECRET' not in message
