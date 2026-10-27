from dataclasses import asdict
from fluxftp.core import Site
from test_queue import window


def test_relaunch_hides_reconnect_but_retains_recent_history(window):
    from fluxftp.app import Window
    site = Site(name='Previous session', host='example.test')
    window.remember_recent_site(site)
    reopened = Window()
    try:
        assert reopened.recent_sites() == [asdict(site)]
        for pane in reopened.current_panes():
            pane.populate_site_menu()
            assert not any(action.text().startswith('Reconnect to ') for action in pane.sites_menu.actions())
        reopened.remember_recent_site(site)
        pane = reopened.current_panes()[0]
        pane.populate_site_menu()
        assert any(action.text() == 'Reconnect to Previous session' for action in pane.sites_menu.actions())
    finally:
        reopened.close()


def test_recent_requires_success_and_reconnect_targets_clicked_pane(window, monkeypatch):
    left, right = window.current_panes()
    site = Site(name='Recent', host='example.test', username='tester')
    window.sites = {'Saved': asdict(site)}
    for pane in (left, right):
        pane.populate_site_menu()
        assert not any(a.text() == 'Recent sites' or a.text().startswith('Reconnect to ')
                       for a in pane.sites_menu.actions())
    right.set_remote(site, 'secret')
    pending = []
    monkeypatch.setattr(window, 'run', lambda task, done, failed: pending.append((done, failed)))
    monkeypatch.setattr(window, 'error', lambda *_: None)
    right.refresh()
    pending.pop()[1]('failed login')
    assert not window.recent_sites()
    right.refresh()
    pending.pop()[0](([], 'TLS'))
    assert window.recent_sites() == [asdict(site)]
    calls = []
    monkeypatch.setattr(right, 'connect_saved_site', lambda name: calls.append(('right', name)))
    right.populate_site_menu()
    reconnect = next(a for a in right.sites_menu.actions() if a.text() == 'Reconnect to Recent')
    reconnect.trigger()
    assert calls == [('right', 'Saved')]
    right.busy = True
    right.populate_site_menu()
    assert not next(a for a in right.sites_menu.actions() if a.text() == 'Reconnect to Recent').isEnabled()
    right.busy = False


def test_unsaved_reconnect_keeps_profile_and_prompts_without_saving_password(window, monkeypatch):
    from PySide6.QtWidgets import QInputDialog
    pane = window.current_panes()[0]
    site = Site(name='Unsaved', host='example.test', username='tester', use_masq=False)
    monkeypatch.setattr(window, 'ensure_proxy_credentials', lambda *args: True)
    monkeypatch.setattr(QInputDialog, 'getText', lambda *args: ('temporary-secret', True))
    monkeypatch.setattr(pane, 'refresh', lambda: None)
    window.choose_recent_site(pane, asdict(site))
    assert pane.site == site
    assert pane.password == 'temporary-secret'
    assert 'Unsaved' not in window.sites
