import json
import ftplib
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path
from threading import Event
from types import SimpleNamespace
import xml.etree.ElementTree as ET
import pytest
from PySide6.QtTest import QTest
from fluxftp.core import Connection, Site, PROTOCOLS, fxp, transfer_tree
from fluxftp.automation import execute_script, validate_script
from fluxftp.calendar_schedule import next_calendar
from fluxftp.site_connections import transfer_account, custom_proxy
from fluxftp.fxp_roles import source_passive
from fluxftp.background_scheduler import task_xml
from fluxftp.help_viewer import HelpDialog
from fluxftp.transfer_options import TransferOptions
from test_queue import window
from test_integration import servers
from test_automation import settle


def test_directional_account_and_proxy_vault(window):
    primary = Site(name='Main', host='test.example', username='browse', site_options=json.dumps({'Account/upload_site':'Writer'}))
    alternate = replace(primary, name='Writer', username='write', site_options='{}')
    window.sites['Writer'] = dict(asdict(alternate), _credential_id='writer-id')
    window.credential_vault.set('writer-id', 'writer-secret')
    site, password = transfer_account(window, primary, 'main-secret', 'upload')
    assert site.username == 'write' and password == 'writer-secret'
    assert transfer_account(window, primary, 'main-secret', 'download') == (primary, 'main-secret')
    window.sites['Writer']['host'] = 'different.example'
    with pytest.raises(ValueError, match='same host'):
        transfer_account(window, primary, '', 'upload')
    window.preferences.setValue('Connection/proxy_profiles', json.dumps([dict(id='other', name='Other', host='proxy.example', port=1080, type='SOCKS5', username='p', auth=True)]))
    window.credential_vault.set('proxy:other', 'proxy-secret')
    proxy_site = replace(primary, site_options=json.dumps({'Connection/proxy_profile':'other'}))
    proxy = custom_proxy(window, proxy_site)
    assert proxy['password'] == 'proxy-secret'
    connection = window.new_connection(proxy_site, '')
    assert connection.proxy['host'] == 'proxy.example'


@pytest.mark.parametrize('tls', [False, True])
def test_recursive_script_upload_download_and_skip(servers, tmp_path, tls):
    remote_root, site = servers(tls=tls)
    local = tmp_path / 'upload'
    (local / 'nested').mkdir(parents=True)
    (local / 'nested' / 'file.bin').write_bytes(b'payload' * 200)
    worker = SimpleNamespace(cancel=Event(), report=lambda *_: None)
    step = dict(action='Upload folder', site='Test', remote='/tree', local=str(local), exists='Fail')
    script = validate_script(dict(name='Folder round trip', steps=[step]))
    execute_script(script, [Connection(site, 'password')], worker, lambda *_: None)
    assert (remote_root / 'tree/nested/file.bin').read_bytes() == b'payload' * 200
    with pytest.raises(ValueError, match='already exists'):
        execute_script(script, [Connection(site, 'password')], worker, lambda *_: None)
    download = tmp_path / 'download'
    step.update(action='Download folder', local=str(download))
    script = validate_script(dict(name='Download', steps=[step]))
    execute_script(script, [Connection(site, 'password')], worker, lambda *_: None)
    assert (download / 'nested/file.bin').read_bytes() == b'payload' * 200
    (download / 'nested/file.bin').write_bytes(b'preserve')
    script['steps'][0]['exists'] = 'Skip'
    execute_script(script, [Connection(site, 'password')], worker, lambda *_: None)
    assert (download / 'nested/file.bin').read_bytes() == b'preserve'


def test_calendar_coalesces_and_restores_month_end():
    start = datetime(2026, 1, 31, 9).timestamp()
    feb = next_calendar(start, start, 'Monthly', 31)
    assert datetime.fromtimestamp(feb) == datetime(2026, 2, 28, 9)
    march = next_calendar(feb, feb, 'Monthly', 31)
    assert datetime.fromtimestamp(march) == datetime(2026, 3, 31, 9)
    later = datetime(2026, 6, 15, 10).timestamp()
    assert datetime.fromtimestamp(next_calendar(start, later, 'Daily')).hour == 9
    assert datetime.fromtimestamp(next_calendar(start, later, 'Weekly')).weekday() == 5


@pytest.mark.parametrize('tls', [False, True])
def test_source_passive_fxp(servers, tls):
    root_a, a = servers(tls=tls, fxp_tls='SSCN' if tls else None)
    root_b, b = servers(tls=tls)
    a = replace(a, site_options=json.dumps({'Transfer/fxp_role':'Passive'}))
    (root_a / 'input').write_bytes(b'reversed' * 10000)
    with Connection(a, 'password') as source, Connection(b, 'password') as destination:
        fxp(source, destination, '/input', '/output')
    assert (root_b / 'output').read_bytes() == (root_a / 'input').read_bytes()
    with pytest.raises(ValueError):
        source_passive(a, replace(b, site_options=a.site_options))


def test_stat_listing_on_control_connection(servers):
    root, site = servers()
    (root / 'hello.txt').write_text('hello')
    with Connection(site, 'password', list_mode='STAT') as connection:
        entries = connection.list('/')
        assert any(e.name == 'hello.txt' and e.size == 5 for e in entries)


def test_xdupe_reports_skip_not_success_and_keeps_other_paths(servers, tmp_path):
    root, site = servers(xdupe=True)
    site = replace(site, site_options=json.dumps({'Transfer/xdupe':True}))
    (root / 'duplicate').write_bytes(b'original')
    local = tmp_path / 'input'
    local.write_bytes(b'new')
    skipped = []
    options = TransferOptions(exists_enabled=False, atomic_upload=False, hash_verification='Off')
    with Connection(site, 'password') as destination:
        assert transfer_tree(None, destination, str(local), '/duplicate', False, Event(), lambda *_:None, options=options, skipped=lambda *args: skipped.append(args)) is False
        assert skipped and (root / 'duplicate').read_bytes() == b'original'
        transfer_tree(None, destination, str(local), '/fresh', False, Event(), lambda *_:None, options=options)
        assert (root / 'fresh').read_bytes() == b'new'


def test_task_xml_quotes_arguments_and_uses_current_user():
    text = task_xml('C:/Program Files/FluxFTP/FluxFTP.exe', ['--automation-once'], 'COMPUTER/user')
    root = ET.fromstring(text)
    ns = {'t':'http://schemas.microsoft.com/windows/2004/02/mit/task'}
    assert root.find('.//t:LogonType', ns).text == 'InteractiveToken'
    assert root.find('.//t:Arguments', ns).text == '--automation-once'
    assert root.find('.//t:Interval', ns).text == 'PT1M'
    assert 'password' not in text.casefold()


def test_help_search_and_monitor_baseline(window, monkeypatch):
    help = HelpDialog(window)
    help.search.setText('automation')
    assert help.filtered and all('automation' in body.casefold() for _, body in help.filtered)
    help.close()
    monitor = window.folder_monitor
    monitor.timer.stop()
    window.automation.timer.stop()
    window.sites = {'Watch':dict(asdict(Site(name='Watch')), site_options=json.dumps({'Monitor/enabled':True}))}
    entries = []
    class Fake:
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def list(self, path): return [SimpleNamespace(name=name) for name in entries]
    monkeypatch.setattr('fluxftp.folder_monitor.saved_connection', lambda *_:Fake())
    settle(window)
    assert monitor.tick()
    settle(window)
    entries.append('new.txt')
    for record in monitor.state.values(): record['next'] = 0
    assert monitor.tick()
    settle(window)
    assert 'new.txt' in window.log.toPlainText()
    assert monitor.path.exists()


def test_headless_runner_executes_due_script_without_window(window, servers):
    import time
    from PySide6.QtWidgets import QApplication
    from fluxftp.background_scheduler import run_once
    root, site = servers()
    window.folder_monitor.timer.stop()
    window.automation.timer.stop()
    settle(window)
    window.sites['Background'] = asdict(site)
    window.save_sites(window.sites)
    window.set_site_password('Background', 'password')
    identity = window.automation.put(None, dict(name='Background listing',
        steps=[dict(action='List folder', site='Background', remote='/')],
        enabled=True, next_run=time.time() - 5))
    window.automation.shutdown()
    before = set(QApplication.topLevelWidgets())
    assert run_once(QApplication.instance()) == 0
    record = json.loads(window.automation.path.read_text())['scripts'][identity]
    assert not record['enabled'] and not record['running']
    assert record['last'] == 'Completed all steps.'
    assert set(QApplication.topLevelWidgets()) == before


def test_gui_reloads_after_background_owner_releases(window):
    import time
    from fluxftp.automation import Automation
    primary = window.automation
    primary.timer.stop()
    secondary = Automation(window)
    secondary.timer.stop()
    assert not secondary.owner
    identity = primary.put(None, dict(name='New', steps=[dict(action='List folder', site='Missing', remote='/')]))
    primary.shutdown()
    secondary.tick()
    assert secondary.owner and identity in secondary.scripts
    secondary.shutdown()


def test_alternate_login_transfers_with_its_own_password(window, servers, tmp_path):
    from fluxftp.site_connections import transfer_connection
    root, site = servers(extra_account=('writer', 'write-password'))
    alternate = replace(site, name='Write account', username='writer')
    window.sites['Write account'] = dict(asdict(alternate), _credential_id='alternate-login')
    window.credential_vault.set('alternate-login', 'write-password')
    site.site_options = json.dumps({'Account/upload_site':'Write account'})
    local = tmp_path / 'upload.txt'
    local.write_bytes(b'alternate login verified')
    with transfer_connection(window, site, 'incorrect-main-password', 'upload') as connection:
        assert connection.site.username == 'writer'
        connection.transfer(local, '/uploaded.txt', True, Event(), lambda *_:None)
    assert (root / 'uploaded.txt').read_bytes() == local.read_bytes()


def test_recursive_download_rejects_remote_link_ancestor(tmp_path):
    from fluxftp.automation_folders import transfer_folder
    from fluxftp.core import Entry
    connection = SimpleNamespace(list=lambda path:[Entry('link', True, is_link=True)])
    worker = SimpleNamespace(cancel=Event(), report=lambda *_:None)
    step = dict(action='Download folder', remote='/link/sub', local=str(tmp_path / 'dest'), exists='Fail')
    with pytest.raises(ValueError, match='file/link'):
        transfer_folder(connection, step, worker, lambda *_:None)
    assert not (tmp_path / 'dest').exists()


def test_ident_returns_distinct_site_identities():
    from fluxftp.ident import IdentService
    service = IdentService()
    service.enabled, service.only_connecting = True, False
    class Owner:
        def __init__(self, name):
            self.site = Site(site_options=json.dumps({'Connection/ident_name':name}))
    first, second = Owner('first-site'), Owner('second-site')
    service.connections[first] = (('127.0.0.1',1001), ('127.0.0.2',21))
    service.connections[second] = (('127.0.0.1',1002), ('127.0.0.2',21))
    assert b':first-site' in service.response(b'1001,21\r\n', '127.0.0.1', '127.0.0.2')
    assert b':second-site' in service.response(b'1002,21\r\n', '127.0.0.1', '127.0.0.2')
