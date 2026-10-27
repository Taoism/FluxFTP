from dataclasses import replace
from types import SimpleNamespace
import paramiko
import pytest
from fluxftp.core import Connection, Cancelled
from fluxftp.ssh_trust import VerifiedPolicy, TrustDialog, fingerprint, remember
from test_sftp import ssh_server
from test_queue import window


def test_unknown_host_verified_once_then_persisted_before_password_auth(ssh_server, tmp_path):
    root, site = ssh_server
    site = replace(site, known_hosts='')
    accepted = []
    trust = SimpleNamespace(path=tmp_path / 'app-known-hosts', verify=lambda host, key: accepted.append(fingerprint(key)) or True)
    with Connection(site, 'password', ssh_trust=trust) as connection:
        assert connection.list('/') == []
    assert len(accepted) == 1 and trust.path.is_file()
    trust.verify = lambda *_: pytest.fail('Trusted server must not reprompt')
    with Connection(site, 'password', ssh_trust=trust) as connection:
        assert connection.list('/') == []


def test_cancel_does_not_save_and_changed_key_never_prompts(ssh_server, tmp_path):
    _, site = ssh_server
    site = replace(site, known_hosts='')
    trust = SimpleNamespace(path=tmp_path / 'app-known-hosts', verify=lambda *_: False)
    with pytest.raises(Cancelled):
        with Connection(site, 'password', ssh_trust=trust): pass
    assert not trust.path.exists()
    host = f'[{site.host}]:{site.port}'
    remember(trust.path, host, paramiko.RSAKey.generate(2048))
    trust.verify = lambda *_: pytest.fail('Changed keys must not receive first-use prompts')
    with pytest.raises(paramiko.BadHostKeyException):
        with Connection(site, 'password', ssh_trust=trust): pass


def test_fingerprint_dialog_requires_exact_independent_value(window):
    key = paramiko.RSAKey.generate(2048)
    dialog = TrustDialog(window, '[server.test]:2222', key)
    assert not dialog.trust.isEnabled()
    dialog.confirmation.setText('wrong fingerprint')
    assert not dialog.trust.isEnabled()
    dialog.confirmation.setText(fingerprint(key))
    assert dialog.trust.isEnabled()
    dialog.close()


def test_parallel_trust_does_not_replace_an_existing_key(tmp_path):
    path = tmp_path / 'known_hosts'
    first, other = paramiko.RSAKey.generate(2048), paramiko.RSAKey.generate(2048)
    remember(path, 'server.test', first)
    with pytest.raises(paramiko.SSHException): remember(path, 'server.test', other)
    assert paramiko.HostKeys(str(path))['server.test'][first.get_name()] == first


@pytest.mark.parametrize('cancelled', [False, True])
def test_worker_prompt_runs_on_gui_and_observes_cancellation(window, ssh_server, cancelled):
    import time
    from PySide6.QtTest import QTest
    _, site = ssh_server
    site = replace(site, known_hosts='')
    results, failures = [], []
    def task(worker):
        with window.new_connection(site, 'password') as connection:
            return connection.list('/')
    worker = window.run(task, results.append, failures.append)
    deadline = time.monotonic() + 15
    while window.ssh_trust.dialog is None:
        QTest.qWait(20)
        assert time.monotonic() < deadline, failures
    dialog = window.ssh_trust.dialog
    if cancelled:
        worker.cancel.set()
    else:
        dialog.confirmation.setText(dialog.expected)
        dialog.trust.click()
    while window.workers or window.ssh_trust.dialog is not None:
        QTest.qWait(20)
        assert time.monotonic() < deadline, failures
    if cancelled:
        assert failures and not window.ssh_trust.path.exists()
    else:
        assert results == [[]] and window.ssh_trust.path.exists()
