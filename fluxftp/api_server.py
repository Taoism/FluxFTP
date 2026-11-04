"""Loopback-only authenticated JSON control API for local clients and tunnels."""
from __future__ import annotations

import hmac
import json
import secrets
import threading
import posixpath
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from PySide6.QtCore import QObject, Qt, Signal

TOKEN_ID = 'control-api-token-v1'
MAX_BODY = 16 * 1024


class _Call:
    def __init__(self, method, path, payload):
        self.method, self.path, self.payload = method, path, payload
        self.done = threading.Event()
        self.result = None


class ControlAPI(QObject):
    """Authenticated localhost API. Remote use requires an authenticated tunnel."""
    dispatch = Signal(object)

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.token = window.credential_vault.get(TOKEN_ID)
        if not self.token:
            self.token = secrets.token_urlsafe(32)
            window.credential_vault.set(TOKEN_ID, self.token)
        self.dispatch.connect(self._dispatch, Qt.ConnectionType.QueuedConnection)
        self.server = ThreadingHTTPServer(('127.0.0.1', 8765), self._handler())
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, name='FluxFTP-JSON-API', daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def rotate_token(self):
        token = secrets.token_urlsafe(32)
        self.window.credential_vault.set(TOKEN_ID, token)
        self.token = token
        return token

    def _handler(self):
        controller = self

        class Handler(BaseHTTPRequestHandler):
            server_version = 'FluxFTP-API/1'
            def log_message(self, *_):
                return

            def respond(self, status, value):
                body = json.dumps(value, separators=(',', ':')).encode('utf-8')
                self.send_response(status)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(body)

            def handle_request(self):
                peer = self.client_address[0]
                if peer not in ('127.0.0.1', '::1'):
                    self.respond(403, {'error': 'loopback clients only'})
                    return
                supplied = self.headers.get('Authorization', '')
                if not supplied.startswith('Bearer ') or not hmac.compare_digest(supplied[7:], controller.token):
                    self.respond(401, {'error': 'bearer token required'})
                    return
                payload = None
                if self.command == 'POST':
                    try:
                        length = int(self.headers.get('Content-Length', '0'))
                        if length < 0 or length > MAX_BODY:
                            self.respond(413, {'error': 'request body too large'})
                            return
                        payload = json.loads(self.rfile.read(length) or b'{}')
                        if not isinstance(payload, dict):
                            raise ValueError('JSON body must be an object')
                    except (ValueError, UnicodeError, json.JSONDecodeError):
                        self.respond(400, {'error': 'invalid JSON request'})
                        return
                if self.command not in ('GET', 'POST'):
                    self.respond(405, {'error': 'method not allowed'})
                    return
                call = _Call(self.command, urlsplit(self.path).path, payload)
                controller.dispatch.emit(call)
                if not call.done.wait(10):
                    self.respond(503, {'error': 'desktop did not respond in time'})
                else:
                    status, result = call.result
                    self.respond(status, result)

            do_GET = handle_request
            do_POST = handle_request

        return Handler

    def _dispatch(self, call):
        try:
            call.result = self._execute(call.method, call.path, call.payload)
        except (ValueError, KeyError) as error:
            call.result = (400, {'error': str(error)})
        except Exception as error:
            call.result = (500, {'error': str(error)})
        finally:
            call.done.set()

    def _execute(self, method, path, payload):
        w = self.window
        if method == 'GET' and path == '/v1/sites':
            return 200, {'sites': sorted(name for name, profile in w.sites.items()
                if isinstance(name, str) and isinstance(profile, dict) and not profile.get('_disabled'))}
        if method == 'GET' and path in ('/v1/status', '/v1/transfers'):
            jobs = [job for job in w.queue_jobs if 'src' in job]
            if path.endswith('/status'):
                return 200, {'paused': bool(w.paused), 'queued': sum(j.get('state') == 'queued' for j in jobs),
                    'active': sum(j.get('state') == 'active' for j in jobs), 'failed': sum(j.get('state') == 'failed' for j in jobs)}
            return 200, {'transfers': [self._job(job) for job in jobs]}
        if method == 'POST' and path == '/v1/queue/play':
            w.play_queue(); return 200, {'ok': True, 'paused': False}
        if method == 'POST' and path == '/v1/queue/pause':
            if not w.paused: w.toggle_pause()
            return 200, {'ok': True, 'paused': True}
        if method == 'POST' and path == '/v1/queue/stop':
            w.stop_queue(); return 200, {'ok': True}
        if method == 'POST' and path == '/v1/queue/cancel':
            ids = payload.get('ids') if payload else None
            if not isinstance(ids, list) or not ids or any(not isinstance(item, str) for item in ids):
                raise ValueError('ids must be a non-empty array of transfer IDs')
            chosen = [job for job in w.queue_jobs if job.get('api_id') in ids]
            if len(chosen) != len(set(ids)): raise ValueError('one or more transfer IDs were not found')
            rows = {job['row'] for job in chosen}
            for record in w.active.values():
                if record.get('row') in rows:
                    if record.get('job') is not None: record['job']['suppress_retry'] = True
                    record['worker'].cancel.set()
            w.transfer_history.cancelled(job for job in w.pending if job['row'] in rows)
            for record in w.queue_engine.cancel(chosen):
                record['worker'].cancel.set()
                record['row'].setText(7, f"{record['route']} · Cancelling…")
            w.save_queue_state()
            return 200, {'ok': True, 'cancelled': ids}
        if method == 'POST' and path == '/v1/transfers':
            if not isinstance(payload, dict): raise ValueError('request body must be an object')
            source_data, dest_data = payload.get('source'), payload.get('destination')
            source, src = self._endpoint(source_data, 'source')
            destination, dst = self._endpoint(dest_data, 'destination')
            start = payload.get('start', False)
            if not isinstance(start, bool): raise ValueError('start must be true or false')
            local_source = Path(src) if source.site is None else None
            if local_source:
                if local_source.is_symlink() or not local_source.exists(): raise ValueError('local source must exist and cannot be a symbolic link')
                directory = local_source.is_dir()
                size = 0 if directory else local_source.stat().st_size
            else:
                directory = payload.get('directory')
                if not isinstance(directory, bool): raise ValueError('directory must be provided for a remote source')
                size = 0
            entry_name = self._basename(dst, destination.site is None)
            from .core import Entry
            split = w.tabs.widget(0)
            source.number, destination.number = 1, 2
            destination.parentWidget = lambda: split
            jobs = w.enqueue_items(source, destination, [Entry(entry_name, directory, size)], start=start, confirmed=True)
            return 202, {'queued': True, 'held': not start, 'transfers': [self._job(job) for job in (jobs or [])]}
        return 404, {'error': 'unknown endpoint'}

    def _endpoint(self, value, label):
        if not isinstance(value, dict) or not isinstance(value.get('path'), str) or not value['path']:
            raise ValueError(f'{label}.path is required')
        path, site_name = value['path'], value.get('site')
        if site_name is not None and (not isinstance(site_name, str) or site_name not in self.window.sites):
            raise ValueError(f'{label}.site must name a saved FluxFTP site')
        profile = self.window.sites.get(site_name) if site_name else None
        if profile and profile.get('_disabled'):
            raise ValueError(f'{label}.site is disabled')
        site = self.window.site_from_profile(profile) if profile else None
        password = self.window.get_site_password(profile) if profile else ''
        if site:
            name = posixpath.basename(path.rstrip('/'))
            parent = posixpath.dirname(path.rstrip('/')) or '/'
        else:
            p = Path(path).expanduser().absolute()
            name, parent, path = p.name, str(p.parent), str(p)
        if not name or name in ('.', '..') or any(char in name for char in '/\\\r\n\x00'):
            raise ValueError(f'{label}.path must identify a file or folder')
        from .core import safe_name, safe_remote_name
        (safe_remote_name if site else safe_name)(name)
        if any(char in path for char in '\r\n\x00'):
            raise ValueError(f'{label}.path contains a control character')
        from types import SimpleNamespace
        pane = SimpleNamespace(mode='Remote' if site else 'Local', site=site, password=password,
            busy=False, path=parent, join=lambda _name, exact=path: exact)
        return pane, path

    @staticmethod
    def _basename(path, local):
        name = Path(path).name if local else posixpath.basename(path.rstrip('/'))
        if not name or name in ('.', '..') or any(char in name for char in '/\\\r\n\x00'):
            raise ValueError('destination.path must identify a file or folder')
        return name

    @staticmethod
    def _job(job):
        job.setdefault('api_id', secrets.token_urlsafe(12))
        return {'id': job['api_id'], 'name': job.get('name', ''), 'route': job.get('route', ''),
            'source': job.get('src', ''), 'destination': job.get('dst', ''), 'state': job.get('state', ''),
            'progress': job.get('row').text(1) if job.get('row') else '',
            'transferred': job.get('row').text(3) if job.get('row') else '',
            'speed': job.get('row').text(2) if job.get('row') else '',
            'status': job.get('row').text(7) if job.get('row') else ''}


def show_access_dialog(window):
    from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QLineEdit, QDialogButtonBox
    dialog = QDialog(window)
    dialog.setWindowTitle('Local JSON API Access')
    dialog.resize(560, 170)
    layout = QVBoxLayout(dialog)
    layout.addWidget(QLabel('FluxFTP listens on 127.0.0.1:8765. Remote access requires an authenticated SSH or VPN tunnel; the API does not listen on public interfaces.'))
    field = QLineEdit(window.control_api.token)
    field.setReadOnly(True)
    field.setEchoMode(QLineEdit.EchoMode.Password)
    field.setToolTip('Bearer token stored in the encrypted credential vault')
    layout.addWidget(field)
    reveal = QDialogButtonBox()
    show = reveal.addButton('Show token', QDialogButtonBox.ButtonRole.ActionRole)
    show.clicked.connect(lambda: (field.setEchoMode(QLineEdit.EchoMode.Normal), show.setEnabled(False)))
    rotate = reveal.addButton('Regenerate token', QDialogButtonBox.ButtonRole.ActionRole)
    def regenerate():
        try:
            field.setText(window.control_api.rotate_token())
            field.setEchoMode(QLineEdit.EchoMode.Normal)
            show.setEnabled(False)
        except RuntimeError as error:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(dialog, 'Token was not changed', str(error))
    rotate.clicked.connect(regenerate)
    reveal.addButton(QDialogButtonBox.StandardButton.Close).clicked.connect(dialog.accept)
    layout.addWidget(reveal)
    dialog.exec()
