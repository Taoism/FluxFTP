"""Durable single-owner automation scheduler and run controller."""
import copy
import json
import os
import tempfile
import time
import uuid

from PySide6.QtCore import QObject, QTimer, QLockFile, Signal
from PySide6.QtWidgets import QApplication

from .core import Cancelled, PROTOCOLS
from .server_commands import public_text
from .queue_metadata import remote_path

from .automation_scripts import (
    ACTIONS, command_plan, validate_script, next_occurrence, saved_connection,
    execute_script, list_folder,
)


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
