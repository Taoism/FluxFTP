import json
import time
from dataclasses import asdict, replace
from datetime import datetime
from zoneinfo import ZoneInfo
from PySide6.QtWidgets import QApplication
from fluxftp.core import Connection, fxp, Site
from fluxftp import calendar_schedule
from fluxftp.background_scheduler import run_once
from fluxftp.config_backup import configuration, restore_configuration
from test_queue import window
from test_integration import servers
from test_automation import settle


def test_calendar_dst_gap_and_repeat_keep_wall_time(monkeypatch):
    zone = ZoneInfo('America/Los_Angeles')
    class Local:
        @staticmethod
        def fromtimestamp(stamp):
            return datetime.fromtimestamp(stamp, zone)
    monkeypatch.setattr(calendar_schedule, 'datetime', Local)
    start = datetime(2026,3,7,2,30,tzinfo=zone).timestamp()
    spring = calendar_schedule.next_calendar(start,start,'Daily',wall_time='02:30:00')
    assert datetime.fromtimestamp(spring,zone).hour == 3
    following = calendar_schedule.next_calendar(spring,spring,'Daily',wall_time='02:30:00')
    assert datetime.fromtimestamp(following,zone) == datetime(2026,3,9,2,30,tzinfo=zone)
    due = datetime(2026,11,1,1,30,tzinfo=zone,fold=0).timestamp()
    now = datetime(2026,11,1,1,15,tzinfo=zone,fold=1).timestamp()
    next_run = calendar_schedule.next_calendar(due,now,'Daily',wall_time='01:30:00')
    assert datetime.fromtimestamp(next_run,zone).date().isoformat() == '2026-11-02'


def test_source_passive_cpsv_fallback(servers):
    root_a, a = servers(tls=True, fxp_tls='CPSV')
    root_b, b = servers(tls=True)
    a = replace(a, site_options=json.dumps({'Transfer/fxp_role':'Passive'}))
    (root_a/'source').write_bytes(b'CPSV reverse' * 1000)
    with Connection(a,'password') as source, Connection(b,'password') as destination:
        fxp(source,destination,'/source','/target')
    assert (root_b/'target').read_bytes() == (root_a/'source').read_bytes()


def test_background_smoke_does_not_claim_saved_schedules(window):
    window.folder_monitor.timer.stop()
    window.automation.timer.stop()
    settle(window)
    identity = window.automation.put(None,dict(name='Must not run',enabled=True,next_run=time.time()-5,
        steps=[dict(action='List folder',site='Missing',remote='/')]))
    window.automation.shutdown()
    before = window.automation.path.read_bytes()
    app = QApplication.instance()
    previous_quit = app.quitOnLastWindowClosed()
    assert run_once(app,smoke=True) == 0
    assert app.quitOnLastWindowClosed() == previous_quit
    assert window.automation.path.read_bytes() == before


def test_backup_remaps_site_proxy_reference_without_reusing_secret(window):
    window.sites = {'Main':asdict(Site(name='Main',host='example.test',site_options=json.dumps({'Connection/proxy_profile':'proxy-old'})))}
    window.preferences.setValue('Connection/proxy_profiles',json.dumps([
        dict(id='proxy-old',name='Proxy',type='SOCKS5',host='proxy.test',port=1080,auth=True,username='user')]))
    data=configuration(window.preferences,window.sites)
    restore_configuration(window,data)
    identity=json.loads(window.sites['Main']['site_options'])['Connection/proxy_profile']
    profiles=json.loads(window.preferences.value('Connection/proxy_profiles'))
    assert identity != 'proxy-old' and identity == profiles[0]['id']
    assert not window.credential_vault.get('proxy:'+identity)
