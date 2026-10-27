import json
from dataclasses import replace

from PySide6.QtWidgets import QCheckBox, QSpinBox

from fluxftp.preferences import PreferencesDialog
from fluxftp.site_options import decode
from test_queue import window
from test_integration import servers
from test_ui import settle


def test_pane_keepalive_reuses_socket_and_disable_releases_it(window, servers, monkeypatch):
    root, site = servers()
    created, errors = [], []
    original = window.new_connection
    def factory(*args):
        connection = original(*args)
        created.append(connection)
        return connection
    monkeypatch.setattr(window, 'new_connection', factory)
    monkeypatch.setattr(window, 'error', errors.append)
    pane = window.current_panes()[0]
    pane.set_remote(site, 'password', '/')
    pane.refresh(); settle(window)
    pane.refresh(); settle(window)
    assert len(created) == 1
    commands = []
    original_command = created[0].ftp.voidcmd
    def command(value):
        commands.append(value)
        return original_command(value)
    created[0].ftp.voidcmd = command
    pane.browse_session.last_activity -= 61
    window.maintain_browse_sessions(); settle(window)
    assert commands == ['NOOP'] and len(created) == 1
    assert not pane._keepalive_pending and not errors
    window.preferences.setValue('Connection/persistent_browsing', False)
    window.maintain_browse_sessions()
    assert pane.browse_session.connection is None and created[0].ftp.sock is None
    pane.refresh(); settle(window)
    assert len(created) == 2 and created[1].ftp.sock is None


def test_site_override_disables_probe_and_single_connection_disables_reuse(window, servers):
    _, site = servers()
    site.site_options = json.dumps({'Connection/ftp_keepalive': False})
    pane = window.current_panes()[0]
    pane.set_remote(site, 'password', '/')
    pane.refresh(); settle(window)
    connection = pane.browse_session.connection
    pane.browse_session.last_activity -= 61
    window.maintain_browse_sessions()
    assert not getattr(pane, '_keepalive_pending', False)
    assert pane.browse_session.connection is connection
    pane.set_remote(replace(site, single_connection=True), 'password', '/')
    pane.refresh(); settle(window)
    assert pane.browse_session.connection is None


def test_keepalive_controls_persist_and_site_values_validate(window):
    dialog = PreferencesDialog(window)
    page = dialog.pages.widget(dialog.page_by_name['Keep Alive'])
    checks = {widget.text(): widget for widget in page.findChildren(QCheckBox)}
    checks['Reuse browsing connections'].setChecked(False)
    checks['Send NOOP on idle FTP/FTPS browsing connections'].setChecked(False)
    interval = next(widget for widget in page.findChildren(QSpinBox) if widget.maximum() == 3600)
    interval.setValue(120)
    assert not window.preferences.value('Connection/persistent_browsing', True, type=bool)
    assert not window.preferences.value('Connection/ftp_keepalive', True, type=bool)
    assert window.preferences.value('Connection/ftp_keepalive_interval', 0, type=int) == 120
    assert decode(json.dumps({'Connection/ftp_keepalive_interval': 120}))['Connection/ftp_keepalive_interval'] == 120
    import pytest
    with pytest.raises(ValueError): decode(json.dumps({'Connection/ftp_keepalive_interval': 0}))
    dialog.close()
