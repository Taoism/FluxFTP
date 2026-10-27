from types import SimpleNamespace
import pytest
from PySide6.QtWidgets import QApplication, QWidget
from fluxftp.cloud_accounts import CloudAccountTest, CloudAccounts
from test_cloud import service, local_remote


@pytest.fixture
def window():
    app = QApplication.instance() or QApplication([])
    window = QWidget()
    window.sites = {'a': {'cloud_account': 'account', 'root': '/container/folder'},
                    'b': {'cloud_account': 'other', 'root': '/wrong'}}
    window.cloud_service = SimpleNamespace(accounts=lambda: {'account': {'name': 'Test', 'provider': 'WebDAV'}})
    calls = []
    window.run = lambda *args: calls.append(args)
    yield window, calls
    window.close()


def test_saved_path_and_read_only_request(window):
    parent, calls = window
    received = []
    parent.cloud_service.job = lambda endpoint, parameters, **options: received.append((endpoint, parameters, options)) or {'list': []}
    dialog = CloudAccountTest(parent, 'account')
    assert dialog.path.currentText() == '/container/folder'
    dialog.start_test()
    work, done, failed = calls[0]
    done(work(None))
    endpoint, parameters, options = received[0]
    assert endpoint == 'operations/list'
    assert parameters == {'fs': 'caccount:', 'remote': 'container/folder', 'opt': {'recurse': False}}
    assert options['timeout'] == 60
    assert 'successful' in dialog.status.text() and '0 entries' in dialog.status.text()
    assert not dialog.busy
    dialog.close()


def test_invalid_path_and_failure_retry(window):
    parent, calls = window
    dialog = CloudAccountTest(parent, 'account')
    dialog.path.setEditText('/../bad')
    dialog.start_test()
    assert not calls
    dialog.path.setEditText('/container')
    dialog.start_test()
    calls[0][2]('Authorization expired. Sign in again.')
    assert 'Authorization expired' in dialog.status.text()
    assert dialog.test_button.isEnabled() and not dialog.cancel_button.isEnabled()
    dialog.start_test()
    assert len(calls) == 2
    dialog.cancel_test()
    calls[1][2]('Cancelled')
    dialog.close()


def test_cancel_and_close_wait_for_worker(window):
    parent, calls = window
    dialog = CloudAccountTest(parent, 'account')
    dialog.show()
    dialog.start_test()
    dialog.start_test()
    assert len(calls) == 1
    dialog.reject()
    assert dialog.busy and dialog.cancel_event.is_set() and dialog.isVisible()
    calls[0][1]({'list': [{}]})
    assert dialog.status.text() == 'Test cancelled.'
    assert not dialog.isVisible()


def test_test_button_routes_selected_account(window, monkeypatch):
    parent, _ = window
    called = []
    monkeypatch.setattr('fluxftp.cloud_accounts.CloudAccountTest',
        lambda window, identity, parent: SimpleNamespace(exec=lambda: called.append(identity)))
    dialog = CloudAccounts(parent)
    assert not dialog.test_button.isEnabled()
    dialog.tree.setCurrentItem(dialog.tree.topLevelItem(0))
    dialog.test_button.click()
    assert called == ['account']
    dialog.close()


def test_real_backend_listing_without_remote_mutations(service, tmp_path):
    app = QApplication.instance() or QApplication([])
    root, site = local_remote(service, tmp_path)
    (root / 'sample.txt').write_text('unchanged')
    parent = QWidget()
    parent.cloud_service = service
    parent.sites = {'site': {'cloud_account': site.cloud_account, 'root': '/'}}
    parent.run = lambda work, done, failed: done(work(None))
    dialog = CloudAccountTest(parent, site.cloud_account)
    dialog.start_test()
    assert '1 entries' in dialog.status.text()
    assert [p.name for p in root.iterdir()] == ['sample.txt']
    assert (root / 'sample.txt').read_text() == 'unchanged'
    dialog.close()
    parent.close()
