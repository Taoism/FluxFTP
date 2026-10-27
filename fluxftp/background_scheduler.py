"""Windows user task and a headless, single-owner automation host."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

TASK = 'FluxFTP Scheduled Automation'


def task_xml(executable, arguments, user):
    ns = 'http://schemas.microsoft.com/windows/2004/02/mit/task'
    ET.register_namespace('', ns)
    def child(parent, tag, text=None, **attrs):
        node = ET.SubElement(parent, '{' + ns + '}' + tag, attrs)
        node.text = text
        return node
    root = ET.Element('{' + ns + '}Task', version='1.2')
    triggers = child(root, 'Triggers')
    trigger = child(triggers, 'TimeTrigger')
    repetition = child(trigger, 'Repetition')
    child(repetition, 'Interval', 'PT1M')
    child(repetition, 'StopAtDurationEnd', 'false')
    child(trigger, 'StartBoundary', time.strftime('%Y-%m-%dT%H:%M:%S'))
    child(trigger, 'Enabled', 'true')
    principals = child(root, 'Principals')
    principal = child(principals, 'Principal', id='Author')
    child(principal, 'UserId', user)
    child(principal, 'LogonType', 'InteractiveToken')
    child(principal, 'RunLevel', 'LeastPrivilege')
    settings = child(root, 'Settings')
    child(settings, 'MultipleInstancesPolicy', 'IgnoreNew')
    child(settings, 'DisallowStartIfOnBatteries', 'false')
    child(settings, 'StopIfGoingOnBatteries', 'false')
    child(settings, 'StartWhenAvailable', 'true')
    child(settings, 'ExecutionTimeLimit', 'PT0S')
    actions = child(root, 'Actions', Context='Author')
    execute = child(actions, 'Exec')
    child(execute, 'Command', str(executable))
    child(execute, 'Arguments', subprocess.list2cmdline(arguments))
    child(execute, 'WorkingDirectory', str(Path(executable).parent))
    return ET.tostring(root, encoding='utf-16', xml_declaration=True).decode('utf-16')


def configure_task(enabled):
    if sys.platform != 'win32':
        raise ValueError('Background scheduling currently uses Windows Task Scheduler.')
    flags = subprocess.CREATE_NO_WINDOW
    if not enabled:
        result = subprocess.run(['schtasks.exe', '/Delete', '/TN', TASK, '/F'], capture_output=True, text=True, creationflags=flags)
    else:
        executable = Path(sys.executable)
        arguments = ['--automation-once']
        if not getattr(sys, 'frozen', False):
            executable = executable.with_name('pythonw.exe')
            arguments.insert(0, str(Path(__file__).resolve().parents[1] / 'launch.py'))
        who = subprocess.run(['whoami.exe'], capture_output=True, text=True, check=True, creationflags=flags).stdout.strip()
        with tempfile.TemporaryDirectory(prefix='fluxftp-task-') as directory:
            path = Path(directory) / 'task.xml'
            path.write_text(task_xml(executable, arguments, who), encoding='utf-16')
            result = subprocess.run(['schtasks.exe', '/Create', '/TN', TASK, '/XML', str(path), '/F'], capture_output=True, text=True, creationflags=flags)
    if result.returncode:
        raise OSError((result.stderr or result.stdout).strip())


def run_once(app, smoke=False):
    from PySide6.QtCore import QObject, Signal, QTimer, QSettings
    from types import SimpleNamespace
    from .app import Window, Worker, settings_store, QStandardPaths
    from .core import Site
    from .transfer_options import RateLimiter
    from .credentials import CredentialVault
    from .cloud import CloudService
    from .ident import IdentService
    from .automation import Automation
    from .folder_monitor import FolderMonitor
    class Host(QObject):
        ftp_message = Signal(str, int)
        site_from_profile = staticmethod(Window.site_from_profile)
        new_connection = Window.new_connection
        get_site_password = Window.get_site_password
        proxy_bypassed = Window.proxy_bypassed
        _configured_local_port_range = Window._configured_local_port_range
        _transfer_site_key = staticmethod(Window._transfer_site_key)
        def run(self, function, result, failed=None, raw_failure=False, autostart=True):
            worker = Worker(function, self)
            self.workers.add(worker)
            worker.result.connect(result)
            worker.failed.connect(failed)
            worker.finished.connect(lambda: self.workers.discard(worker))
            worker.finished.connect(worker.deleteLater)
            if autostart:
                worker.start()
            return worker
    host = Host()
    temporary = tempfile.TemporaryDirectory(prefix='fluxftp-background-smoke-') if smoke else None
    host.preferences = QSettings(str(Path(temporary.name) / 'settings.ini'), QSettings.Format.IniFormat) if smoke else settings_store()
    default = Path(temporary.name) / 'data' if smoke else Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppConfigLocation))
    host.sites_file = Path(host.preferences.value('General/data_path', str(default), type=str)) / 'sites.json'
    host.credential_vault = CredentialVault(default / 'credentials.enc')
    host.sites = json.loads(host.sites_file.read_text('utf-8')) if host.sites_file.exists() else {}
    host.workers, host.active, host.pending = set(), {}, []
    host.upload_limiter, host.download_limiter = RateLimiter(), RateLimiter()
    host.key_access = SimpleNamespace(resolve=lambda *_: (_ for _ in ()).throw(ValueError('Managed keys require interactive unlock.')))
    host.ssh_trust = SimpleNamespace(path=default / 'known_hosts', verify=lambda *_: False)
    host.cloud_service = CloudService(default / 'cloud', host.credential_vault)
    identity = host.preferences.value('Connection/proxy_profile_id', 'proxy', type=str)
    host.proxy_password = host.credential_vault.get('proxy' if identity == 'proxy' else 'proxy:' + identity)
    host.ident = IdentService()
    log_path = host.sites_file.parent / 'background-automation.log'
    def log(message):
        if log_path.exists() and log_path.stat().st_size > 2_000_000:
            log_path.replace(log_path.with_suffix('.previous.log'))
        with log_path.open('a', encoding='utf-8') as stream:
            stream.write(time.strftime('%Y-%m-%d %H:%M:%S ') + message + '\n')
    host.log = SimpleNamespace(appendPlainText=log)
    from .bandwidth import BandwidthControl
    host.bandwidth_control = BandwidthControl(host)
    host.automation = Automation(host)
    if not host.automation.owner or host.automation.problem:
        host.automation.shutdown()
        host.bandwidth_control.timer.stop()
        if temporary:
            temporary.cleanup()
        return 0
    host.ident.configure(enabled=host.preferences.value('Ident/enabled', False, type=bool),
                         only_connecting=host.preferences.value('Ident/only_connecting', True, type=bool),
                         name=host.preferences.value('Ident/name', 'FluxFTP', type=str),
                         system=host.preferences.value('Ident/system', 'OTHER', type=str),
                         port=host.preferences.value('Ident/port', 113, type=int))
    host.monitor = FolderMonitor(host)
    host.monitor.timer.stop()
    host.automation.timer.stop()
    started = time.time()
    def tick():
        if not host.automation.idle():
            return
        # One pass handles schedules already due when this invocation started.
        due = sorted((s['next_run'], k) for k,s in host.automation.scripts.items() if s['enabled'] and s['next_run'] <= started)
        if due and not host.automation.problem:
            host.automation.start(due[0][1], scheduled=True)
            return
        if host.monitor.tick(started):
            return
        host.monitor.shutdown()
        host.automation.shutdown()
        host.ident.close()
        app.quit()
    timer = QTimer(host)
    timer.timeout.connect(tick)
    timer.start(250)
    previous_quit = app.quitOnLastWindowClosed()
    app.setQuitOnLastWindowClosed(False)
    try:
        return app.exec()
    finally:
        timer.stop()
        host.bandwidth_control.timer.stop()
        host.monitor.shutdown()
        host.automation.cancel()
        # An external quit can bypass tick's normal idle shutdown. Keep the
        # ownership lock until all workers have stopped touching shared state.
        workers = list(host.workers)
        for worker in workers:
            worker.cancel.set()
        for worker in workers:
            worker.wait()
        host.automation.shutdown()
        host.ident.close()
        host.cloud_service.close()
        app.setQuitOnLastWindowClosed(previous_quit)
        if temporary:
            temporary.cleanup()
