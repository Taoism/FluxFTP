"""Let existing UI tests close their windows after exit confirmation was added.

Confirmation behavior tests install their own question handler after this fixture.
Other dialogs retain their real behavior.
"""
import pytest


@pytest.fixture(autouse=True)
def isolate_application_state(tmp_path, monkeypatch):
    """Never let desktop tests read/write the user's settings, sites or vault."""
    from types import SimpleNamespace
    from PySide6.QtCore import QSettings, QStandardPaths
    from fluxftp import app as app_module, credentials

    settings = QSettings(str(tmp_path / 'application.ini'), QSettings.Format.IniFormat)
    monkeypatch.setattr(app_module, 'settings_store', lambda: settings)
    data = tmp_path / 'application-data'
    data.mkdir()
    monkeypatch.setattr(app_module, 'QStandardPaths', SimpleNamespace(
        StandardLocation=QStandardPaths.StandardLocation,
        writableLocation=lambda location: str(data)))
    # Individual credential tests can override these with their own test doubles.
    monkeypatch.setattr(credentials, '_key', lambda create=False: b'\x55' * 32)
    records = {}
    monkeypatch.setattr(credentials, '_keyring', lambda: SimpleNamespace(
        get_password=lambda service, account: records.get((service, account)),
        set_password=lambda service, account, password: records.__setitem__((service, account), password),
        delete_password=lambda service, account: records.pop((service, account), None)))


@pytest.fixture(autouse=True)
def accept_test_window_exit(monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    original = QMessageBox.question
    def question(parent, title, *args, **kwargs):
        if title == 'Exit FluxFTP':
            return QMessageBox.StandardButton.Yes
        return original(parent, title, *args, **kwargs)
    monkeypatch.setattr(QMessageBox, 'question', question)
