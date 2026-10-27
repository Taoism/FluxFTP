"""Read-only per-site polling; baselines survive background scheduler exits."""
import json
import time
from PySide6.QtCore import QObject, QTimer
from .site_options import decode
from .automation import saved_connection, list_folder


class FolderMonitor(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.path = window.sites_file.parent / 'monitor-state.json'
        try:
            self.state = json.loads(self.path.read_text('utf-8')) if self.path.stat().st_size < 8_000_000 else {}
        except (OSError, ValueError):
            self.state = {}
        if not isinstance(self.state, dict):
            self.state = {}
        self.worker = None
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(1000)

    def tick(self, cutoff=None):
        w = self.window
        if not w.automation.owner or not w.automation.idle() or self.worker:
            return False
        for name, data in w.sites.items():
            if data.get('_disabled'):
                continue
            settings = decode(data.get('site_options', '{}'))
            if not settings.get('Monitor/enabled'):
                continue
            path = settings.get('Monitor/path') or data.get('root') or '/'
            key = json.dumps([name, data.get('host'), data.get('port'), data.get('username'), path])
            state = self.state.get(key, {})
            if (time.time() if cutoff is None else cutoff) < state.get('next', 0):
                continue
            interval = settings.get('Monitor/interval', 60)
            self.state[key] = dict(state, next=time.time() + interval)
            try:
                connection = saved_connection(w, name)
                def scan(worker):
                    with connection:
                        entries = list_folder(connection, path, worker.cancel)
                    if len(entries) > 100_000:
                        raise ValueError('Monitor folder exceeds 100,000 entries.')
                    return sorted({e.name for e in entries})
                def done(names):
                    old = state.get('names')
                    added = sorted(set(names) - set(old)) if old is not None else []
                    self.state[key] = dict(names=names, next=time.time() + interval)
                    if added:
                        message = f'{name} — {path}: {len(added)} new item(s): ' + ', '.join(added[:10])
                        w.log.appendPlainText(message)
                        if getattr(w, 'tray', None):
                            w.tray.showMessage('FluxFTP folder monitor', message)
                    self.save()
                    self.worker = None
                def failed(error):
                    # Do not leak response bodies or credentials into unattended logs.
                    w.log.appendPlainText(f'Folder monitor {name}: {type(error).__name__}; retrying after the poll interval.')
                    self.save()
                    self.worker = None
                self.worker = w.run(scan, done, failed, raw_failure=True, autostart=False)
                self.worker.start()
            except Exception as error:
                w.log.appendPlainText(f'Folder monitor {name}: {type(error).__name__}; check saved login and server trust.')
                self.save()
            return True
        return False

    def save(self):
        # Only the automation owner polls/writes this file.
        try:
            temporary = self.path.with_suffix('.tmp')
            temporary.write_text(json.dumps(self.state, ensure_ascii=False), encoding='utf-8')
            temporary.replace(self.path)
        except OSError:
            self.window.log.appendPlainText('Folder monitor could not save its baseline; check the data folder permissions and disk space.')

    def shutdown(self):
        self.timer.stop()
