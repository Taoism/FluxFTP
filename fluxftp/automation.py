"""Durable, single-owner automation; scripts are data, never executable Python."""
import copy
import json
import os
import posixpath
import re
import tempfile
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import QObject, QTimer, QLockFile, Signal
from PySide6.QtWidgets import QApplication

from .core import Cancelled, PROTOCOLS, safe_name
from .server_commands import compile_commands, execute_commands, public_text, SECRET, CREDENTIAL

ACTIONS = ('List folder', 'Download file', 'Upload file', 'Download folder', 'Upload folder', 'Create folder', 'FTP commands')


def command_plan(lines):
    def no_prompt(*_):
        raise ValueError('Automation commands cannot prompt. Replace prompted arguments in Command Designer first.')
    plan = compile_commands(lines, [], no_prompt)
    for line in plan.lines:
        if SECRET.search(line) or CREDENTIAL.search(line):
            raise ValueError('Credential commands cannot be saved in scripts. Use the saved site credential vault.')
        if line.split()[0].upper() in ('USER', 'OPEN', 'CONNECT', 'REIN', 'AUTH', 'QUIT', 'PORT', 'EPRT', 'PASV', 'EPSV', 'RETR', 'STOR', 'APPE'):
            raise ValueError('Use saved sites and transfer steps instead of connection or data-transfer commands.')
    return plan


def remote_path(value):
    if not isinstance(value, str) or not value.startswith('/') or re.search(r'[\x00-\x1f\x7f]', value):
        raise ValueError('Enter an absolute remote path beginning with /.')
    if '..' in value.split('/'):
        raise ValueError('Remote paths cannot contain parent-directory segments.')
    return posixpath.normpath(value)


def validate_script(raw):
    """Return a bounded schema-only copy, excluding credentials/unknown fields."""
    if not isinstance(raw, dict):
        raise ValueError('Invalid script.')
    name = raw.get('name', '')
    if not isinstance(name, str) or not name.strip() or len(name) > 120 or re.search(r'[\x00-\x1f]', name):
        raise ValueError('Enter a script name of 1–120 characters.')
    steps = raw.get('steps')
    if not isinstance(steps, list) or not 1 <= len(steps) <= 100:
        raise ValueError('A script must have 1–100 steps.')
    cleaned = []
    for step in steps:
        if not isinstance(step, dict) or step.get('action') not in ACTIONS:
            raise ValueError('Unknown script action.')
        site = step.get('site')
        if not isinstance(site, str) or not site or len(site) > 500:
            raise ValueError('Each step needs a saved site.')
        action = step['action']
        path = remote_path(step.get('remote', ''))
        item = dict(action=action, site=site, remote=path)
        if action in ('Download file', 'Upload file', 'Create folder'):
            safe_name(posixpath.basename(path))
        if action in ('Download file', 'Upload file', 'Download folder', 'Upload folder'):
            local = step.get('local', '')
            if not isinstance(local, str) or not Path(local).is_absolute() or re.search(r'[\x00-\x1f]', local):
                raise ValueError('Transfer steps need an absolute local file path (including the filename).')
            policy = step.get('exists', 'Fail')
            if policy not in ('Fail', 'Skip', 'Overwrite'):
                raise ValueError('Choose Fail, Skip or Overwrite for existing files.')
            item.update(local=local, exists=policy)
        if action == 'FTP commands':
            lines = step.get('lines', [])
            if not isinstance(lines, list) or not all(isinstance(line, str) for line in lines):
                raise ValueError('Invalid command lines.')
            command_plan(lines)
            item['lines'] = list(lines)
        cleaned.append(item)
    interval = raw.get('interval', 0)
    due = raw.get('next_run', 0)
    if type(interval) is not int or not 0 <= interval <= 525600:
        raise ValueError('Repeat interval must be 0 (once) or 1–525600 minutes.')
    if type(due) not in (int, float) or not 0 <= due <= 253402300799:
        raise ValueError('Invalid schedule time.')
    enabled = raw.get('enabled', False)
    if type(enabled) is not bool or (enabled and not due):
        raise ValueError('An enabled schedule needs a start time.')
    from .calendar_schedule import MODES
    schedule = raw.get('schedule', 'Interval')
    day = raw.get('month_day', 0)
    if schedule not in MODES or type(day) is not int or not 0 <= day <= 31:
        raise ValueError('Invalid calendar schedule.')
    wall_time = raw.get('wall_time', '')
    if not isinstance(wall_time, str) or (wall_time and not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d', wall_time)):
        raise ValueError('Invalid calendar wall time.')
    return dict(name=name.strip(), steps=cleaned, interval=interval, next_run=due, enabled=enabled,
                schedule=schedule, month_day=day, wall_time=wall_time)


def next_occurrence(due, interval, now):
    """Coalesce missed occurrences; never enqueue a catch-up burst."""
    if not interval:
        return 0
    seconds = interval * 60
    return due + max(1, int((now - due) // seconds) + 1) * seconds


def saved_connection(window, name, unattended=True, direction=None):
    data = window.sites.get(name)
    if not data or data.get('_disabled'):
        raise ValueError('The saved site is missing or disabled: ' + name)
    site = window.site_from_profile(data)
    password = window.get_site_password(data)
    if direction:
        from .site_connections import transfer_account
        site, password = transfer_account(window, site, password, direction)
    if not password and site.username.casefold() == 'anonymous':
        password = window.preferences.value('General/anonymous_email', 'anonymous@fluxftp.local', type=str)
    if not password and not site.cloud_account and not site.managed_key and site.protocol != PROTOCOLS[3]:
        raise ValueError('Save the site password in Site Manager before testing or scheduling it.')
    connection = window.new_connection(site, password)
    if unattended:
        if connection.proxy and connection.proxy.get('username') and not connection.proxy.get('password'):
            raise ValueError('Save the proxy password before running automation.')
        # Previously verified keys still work, but background runs must never open trust dialogs.
        if connection.ssh_trust:
            connection.ssh_trust = SimpleNamespace(path=connection.ssh_trust.path, verify=lambda *_: False)
        if site.managed_key:
            raise ValueError('Automation currently requires a saved key-file path instead of an interactive managed key.')
    return connection


def execute_script(script, connections, worker, report):
    """Sequential file operations using the application's protocol engines."""
    for index, step in enumerate(script['steps'], 1):
        if worker.cancel.is_set():
            raise Cancelled('Script cancelled. Earlier steps may have completed.')
        report(f'Step {index}/{len(script["steps"])}: {step["action"]}')
        connection = connections[index - 1]
        with connection:
            action, remote = step['action'], step['remote']
            if worker.cancel.is_set():
                raise Cancelled('Script cancelled.')
            if action == 'List folder':
                entries = list_folder(connection, remote, worker.cancel)
                report(f'Step {index}: listed {len(entries)} entries.')
            elif action == 'Create folder':
                connection.mkdir(remote)
            elif action == 'FTP commands':
                execute_commands(connection, remote, command_plan(step['lines']), worker.cancel, raise_on_error=True)
            elif action in ('Upload folder', 'Download folder'):
                from .automation_folders import transfer_folder
                transfer_folder(connection, step, worker, report)
            else:
                upload = action == 'Upload file'
                local = Path(step['local'])
                if local.is_symlink() or local.with_name(local.name + '.fluxpart').is_symlink():
                    raise ValueError('Transfer paths cannot be symbolic links.')
                if upload and not local.is_file():
                    raise ValueError('Upload source must be an existing regular file.')
                if not upload and not local.parent.is_dir():
                    raise ValueError('Download destination folder does not exist.')
                entries = list_folder(connection, posixpath.dirname(remote), worker.cancel)
                entry = next((e for e in entries if e.name == posixpath.basename(remote)), None)
                if entry and (entry.is_dir or entry.is_link):
                    raise ValueError('Transfer steps require regular files, not directories or links.')
                if not upload and entry is None:
                    raise ValueError('Download source file does not exist.')
                exists = entry is not None if upload else local.exists()
                if exists:
                    if step['exists'] == 'Fail':
                        raise ValueError('Destination already exists; change the step policy to Skip or Overwrite.')
                    if step['exists'] == 'Skip':
                        report(f'Step {index}: skipped existing destination.')
                        continue
                    if not upload and not local.is_file():
                        raise ValueError('Download destination is not a regular file.')
                connection.transfer(local, remote, upload, worker.cancel, worker.report, resume=False)
        if worker.cancel.is_set():
            raise Cancelled('Script cancelled. Earlier steps may have completed.')
    return 'Completed all steps.'


def list_folder(connection, path, cancel):
    if cancel.is_set():
        raise Cancelled('Cancelled')
    result = connection.list(path, cancel=cancel) if getattr(connection, 'is_cloud', False) else connection.list(path)
    if cancel.is_set():
        raise Cancelled('Cancelled')
    return result


class Automation(QObject):
    changed = Signal()
    progress = Signal(str)

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.path = window.sites_file.parent / 'automation.json'
        self.scripts = {}
        self.worker = None
        self.running = None
        self.message = ''
        self.problem = ''
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = QLockFile(str(self.path) + '.lock')
        self.owner = self.lock.tryLock(0)
        if not self.owner:
            self.problem = 'Automation is open in another FluxFTP instance. Manage it there.'
        try:
            if self.path.exists():
                if self.path.stat().st_size > 4_000_000:
                    raise ValueError('Automation file exceeds 4 MB.')
                data = json.loads(self.path.read_text('utf-8'))
                if data.get('version') != 1 or not isinstance(data.get('scripts'), dict) or len(data['scripts']) > 200:
                    raise ValueError('Invalid automation file.')
                for identity, raw in data['scripts'].items():
                    if not isinstance(identity, str) or len(identity) > 80:
                        raise ValueError('Invalid script identity.')
                    script = validate_script(raw)
                    script['last'] = str(raw.get('last', 'Not run'))[:500]
                    script['history'] = [str(s)[:500] for s in raw.get('history', [])][-50:]
                    if raw.get('running'):
                        script.update(enabled=False, last='Interrupted — review the destination before running again.')
                    self.scripts[identity] = script
                if self.owner:
                    self.persist()
        except (OSError, ValueError, TypeError, AttributeError) as error:
            self.problem = 'Cannot load automation; original file retained. ' + str(error)
            self.scripts.clear()
        self.progress.connect(self.on_progress)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(1000)

    def persist(self):
        if not self.owner or self.problem:
            raise ValueError(self.problem or 'Automation is owned by another instance.')
        payload = json.dumps(dict(version=1, scripts=self.scripts), ensure_ascii=False, indent=2)
        if len(payload.encode('utf-8')) > 4_000_000:
            raise ValueError('Automation configuration exceeds 4 MB.')
        fd, name = tempfile.mkstemp(prefix='.automation-', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def put(self, identity, value):
        if identity is not None and identity == self.running:
            raise ValueError('Stop the running script before editing it.')
        script = validate_script(value)
        if any(s['name'].casefold() == script['name'].casefold() for key, s in self.scripts.items() if key != identity):
            raise ValueError('Another script already uses that name.')
        if identity is None and len(self.scripts) >= 200:
            raise ValueError('At most 200 scripts can be saved.')
        identity = identity or uuid.uuid4().hex
        previous = copy.deepcopy(self.scripts)
        old = self.scripts.get(identity, {})
        script.update(last=old.get('last', 'Not run'), history=old.get('history', []))
        self.scripts[identity] = script
        try:
            self.persist()
        except Exception:
            self.scripts = previous
            raise
        self.changed.emit()
        return identity

    def remove(self, identity):
        if identity == self.running:
            raise ValueError('Stop the script before deleting it.')
        old = self.scripts.pop(identity)
        try:
            self.persist()
        except Exception:
            self.scripts[identity] = old
            raise
        self.changed.emit()

    def idle(self):
        w = self.window
        return not (self.worker or w.workers or w.active or w.pending or getattr(w, '_queue_operation_depth', 0))

    def tick(self):
        if not self.owner and self.problem == 'Automation is open in another FluxFTP instance. Manage it there.':
            if self.lock.tryLock(0):
                self.owner = True
                self.problem = ''
                try:
                    data = json.loads(self.path.read_text('utf-8')) if self.path.exists() else {'scripts': {}}
                    fresh = {}
                    for identity, raw in data['scripts'].items():
                        item = validate_script(raw)
                        item['last'] = str(raw.get('last', 'Not run'))[:500]
                        item['history'] = [str(v)[:500] for v in raw.get('history', [])][-50:]
                        if raw.get('running'):
                            item.update(enabled=False, last='Interrupted — review before running again.')
                        fresh[identity] = item
                    self.scripts = fresh
                    self.persist()
                    monitor = getattr(self.window, 'folder_monitor', None)
                    if monitor and monitor.path.exists():
                        monitor.state = json.loads(monitor.path.read_text('utf-8'))
                except (OSError, ValueError, TypeError, KeyError):
                    self.problem = 'Cannot reload automation after the other instance exited.'
                self.changed.emit()
        if not self.owner or self.problem or not self.idle() or QApplication.activeModalWidget():
            return
        now = time.time()
        due = sorted((s['next_run'], key) for key, s in self.scripts.items() if s['enabled'] and s['next_run'] <= now)
        if due:
            try:
                self.start(due[0][1], scheduled=True)
            except (ValueError, OSError):
                self.problem = 'Could not start or save the scheduled run. Scheduling stopped; check the automation settings file.'
                self.changed.emit()

    def start(self, identity, scheduled=False):
        if not self.owner or self.problem:
            raise ValueError(self.problem or 'Automation is unavailable.')
        if not self.idle():
            raise ValueError('Wait for active operations and queued transfers before running a script.')
        script = self.scripts[identity]
        snapshot = validate_script(script)
        previous = copy.deepcopy(script)
        script.update(running=True, last='Running')
        if scheduled:
            if script.get('schedule', 'Interval') == 'Interval':
                script['next_run'] = next_occurrence(script['next_run'], script['interval'], time.time())
            else:
                from .calendar_schedule import next_calendar
                from datetime import datetime
                script['month_day'] = script.get('month_day') or datetime.fromtimestamp(script['next_run']).day
                script['wall_time'] = script.get('wall_time') or datetime.fromtimestamp(script['next_run']).strftime('%H:%M:%S')
                script['next_run'] = next_calendar(script['next_run'], time.time(), script['schedule'], script['month_day'], script['wall_time'])
            script['enabled'] = bool(script['next_run'])
        try:
            self.persist()  # Claim durably before any remote effects.
        except Exception:
            self.scripts[identity] = previous
            raise
        self.running = identity
        self.message = 'Preparing connections…'
        self.changed.emit()
        try:
            connections = []
            for step in snapshot['steps']:
                direction = 'upload' if step['action'].startswith('Upload') else 'download' if step['action'].startswith('Download') else None
                connections.append(saved_connection(self.window, step['site'], direction=direction) if direction else saved_connection(self.window, step['site']))
            for step, connection in zip(snapshot['steps'], connections):
                if step['action'] == 'FTP commands' and connection.site.protocol not in (*PROTOCOLS[:3], PROTOCOLS[4]):
                    raise ValueError('FTP command steps require an FTP or FTPS saved site.')
            secrets = tuple(secret for c in connections for secret in
                (c.password, (c.proxy or {}).get('password', '')) if secret)
            def failed(error):
                message = 'Cancelled.' if isinstance(error, Cancelled) else 'Failed during ' + getattr(self, 'phase', 'connection') + ': ' + public_text(str(error), secrets)[:300]
                self.finish(message + ' Earlier steps may have completed.', failed=True)
            self.worker = self.window.run(
                lambda worker: execute_script(snapshot, connections, worker, self.progress.emit),
                lambda result: self.finish(result),
                failed,
                raw_failure=True, autostart=False)
            self.worker.progress.connect(self.on_bytes)
            self.worker.start()
            self.changed.emit()
        except Exception as error:
            self.finish('Failed: ' + str(error), failed=True)

    def on_progress(self, message):
        self.phase = message
        self.message = message
        self.changed.emit()

    def on_bytes(self, percent, rate, done, total):
        self.message = f'{getattr(self, "phase", "Transferring")} — {percent}% ({done:,} / {total:,} bytes)'
        self.changed.emit()

    def finish(self, message, failed=False):
        if self.running is None:
            return
        script = self.scripts[self.running]
        script.update(running=False, last=message)
        if failed:
            script['enabled'] = False
        stamp = time.strftime('%Y-%m-%d %H:%M:%S')
        script['history'] = (script.get('history', []) + [stamp + ' — ' + message])[-50:]
        self.worker = None
        self.running = None
        self.message = message
        try:
            self.persist()
        except Exception:
            self.problem = 'Could not save the run result. Scheduling stopped; review the automation file before restarting.'
        self.changed.emit()

    def cancel(self):
        if self.worker:
            self.worker.cancel.set()
            self.message = 'Cancelling — waiting for the current operation to stop…'
            self.changed.emit()

    def shutdown(self):
        self.timer.stop()
        if self.owner:
            self.lock.unlock()

    def show(self):
        from .automation_ui import AutomationDialog
        if not getattr(self, 'dialog', None):
            self.dialog = AutomationDialog(self)
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()
