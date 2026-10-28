"""Persistent, opt-in local folder watches feeding guarded ordinary queue jobs."""
from .queue_metadata import validate_tag
import fnmatch
import json
import posixpath
import time
import uuid
from dataclasses import asdict, replace
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Qt
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QLineEdit, QComboBox,
    QSpinBox, QCheckBox, QPushButton, QHBoxLayout, QTreeWidget, QTreeWidgetItem,
    QFileDialog, QDialogButtonBox, QLabel, QApplication, QScrollArea, QWidget)

from .automation import saved_connection, remote_path
from .folder_sync import scan, lookup, joined, local_fingerprint
from .sync_profiles import atomic_json


def excluded(relative, patterns):
    parts = relative.split('/')
    return any(fnmatch.fnmatchcase(relative.casefold(), p.casefold()) or
               any(fnmatch.fnmatchcase(part.casefold(), p.casefold()) for part in parts)
               for p in patterns)


def validate_watch(raw):
    if not isinstance(raw, dict): raise ValueError('Invalid folder watch.')
    item = {key: raw[key] for key in ('id', 'name', 'local', 'remote', 'site', 'settle', 'excludes', 'existing', 'overwrite', 'auto', 'enabled')}
    for key in ('id', 'name', 'local', 'remote', 'site'):
        if not isinstance(item[key], str) or not item[key] or len(item[key]) > 32768 or any(ord(c) < 32 for c in item[key]):
            raise ValueError('Invalid watch name or path.')
    if not Path(item['local']).is_absolute(): raise ValueError('Watch folder must be absolute.')
    remote_path(item['remote'])
    if type(item['settle']) is not int or not 2 <= item['settle'] <= 3600: raise ValueError('Settle time must be 2–3600 seconds.')
    for key in ('existing', 'overwrite', 'auto', 'enabled'):
        if type(item[key]) is not bool: raise ValueError('Invalid watch option.')
    if not isinstance(item['excludes'], list) or len(item['excludes']) > 100 or any(not isinstance(p, str) or len(p) > 500 for p in item['excludes']):
        raise ValueError('Invalid watch exclusions.')
    return item




def stable_candidates(files, observations, acknowledged, now, settle):
    """Require at least two unchanged observations, and a complete settle interval."""
    ready = []
    for relative in list(observations):
        if relative not in files: observations.pop(relative)
    for relative, info in files.items():
        previous = observations.get(relative)
        if not previous or previous[0] != info:
            observations[relative] = (info, now)
        elif now - previous[1] >= settle and acknowledged.get(relative) != info:
            ready.append(relative)
    return sorted(ready)


def load_state(path):
    if not path.exists(): return {}, {}
    if path.stat().st_size > 32_000_000: raise ValueError('Watch settings exceed 32 MB.')
    data = json.loads(path.read_text('utf-8'))
    if not isinstance(data, dict) or data.get('version') != 1 or not isinstance(data.get('watches'), dict) or len(data['watches']) > 200:
        raise ValueError('Invalid watch settings.')
    watches = {key: validate_watch(value) for key, value in data['watches'].items()}
    if any(key != value['id'] for key, value in watches.items()): raise ValueError('Watch identity mismatch.')
    states = data['states']
    if not isinstance(states, dict): raise ValueError('Invalid watch state.')
    for identity, state in states.items():
        if not isinstance(state, dict) or not isinstance(state.get('acknowledged', {}), dict): raise ValueError('Invalid watch state.')
        if type(state.get('initialized', False)) is not bool or not isinstance(state.get('next', 0), (int, float)):
            raise ValueError('Invalid watch checkpoint.')
        import math
        if not math.isfinite(state.get('next', 0)): raise ValueError('Invalid watch interval.')
        for relative, fingerprint in state.get('acknowledged', {}).items():
            validate_tag(dict(id=identity, relative=relative, fingerprint=fingerprint))
    return watches, states


class WatchUploads(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.path = window.sites_file.with_name('watch-uploads.json')
        self.watches, self.states, self.observations = {}, {}, {}
        self.worker, self.problem, self.next_scan = None, '', 0
        self.was_owner = window.automation.owner
        try:
            self.watches, self.states = load_state(self.path)
        except (OSError, ValueError, TypeError, KeyError) as error:
            self.problem = f'Watch settings could not be read ({type(error).__name__}); original file preserved.'
            self.watches = {}
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(2000)

    def save(self):
        if self.problem: raise ValueError(self.problem)
        if not self.window.automation.owner: raise ValueError('Manage watches in the FluxFTP instance that owns automation.')
        atomic_json(self.path, dict(version=1, watches=self.watches, states=self.states))

    def pause(self, identity, message):
        if identity in self.watches:
            self.watches[identity]['enabled'] = False
            self.states.setdefault(identity, {})['status'] = message
            try: self.save()
            except (OSError, ValueError): self.problem = 'Could not save watch state. Watches are stopped; check the data folder.'
            self.window.log.appendPlainText('Watch uploads: ' + message)

    def completed(self, job, outcome):
        tag = job.get('watch')
        if not tag or tag['id'] not in self.watches: return
        identity = tag['id']
        if outcome == 'complete':
            state = self.states.setdefault(identity, {})
            state.setdefault('acknowledged', {})[tag['relative']] = tag['fingerprint']
            state['status'] = 'Uploaded: ' + tag['relative']
            try: self.save()
            except (OSError, ValueError): self.pause(identity, 'Could not save upload completion; review before resuming.')
        elif outcome in ('failed', 'cancelled', 'skipped'):
            if self.watches[identity]['enabled']:
                self.pause(identity, 'Transfer ' + outcome + '; review the queue, then enable the watch to continue.')

    def tick(self):
        w = self.window
        if w.automation.owner and not self.was_owner:
            try:
                self.watches, self.states = load_state(self.path)
                self.observations = {}
            except (OSError, ValueError, TypeError, KeyError):
                self.problem = 'Could not reload watch checkpoints after automation ownership changed; original file preserved.'
        self.was_owner = w.automation.owner
        if self.problem or self.worker or not w.automation.owner or w.automation.worker or getattr(w, '_queue_operation_depth', 0) or QApplication.activeModalWidget(): return
        if time.monotonic() < self.next_scan: return
        for identity, item in self.watches.items():
            if not item['enabled']: continue
            state = self.states.setdefault(identity, {'acknowledged': {}})
            if time.time() < state.get('next', 0): continue
            if getattr(w, 'deferred_queue', []):
                state['status'] = 'Load or dismiss the saved queue before watching.'
                continue
            if any(job.get('buffered') and job.get('state') in ('queued', 'active') for job in w.queue_jobs):
                state['status'] = 'Waiting for the buffered queue to drain before checking for duplicate uploads.'
                continue
            pending = [job for job in w.queue_jobs if job.get('watch', {}).get('id') == identity and job.get('state') in ('queued', 'active', 'failed')]
            if any(job.get('state') == 'failed' for job in pending):
                self.pause(identity, 'A watched upload failed; review the queue before enabling the watch.')
                continue
            blocked = {job['watch']['relative'] for job in pending}
            state['next'] = time.time() + 5
            self.next_scan = time.monotonic() + 1
            # Work on a detached state so no UI mutation races the scan.
            config = dict(item)
            acknowledged = dict(state.get('acknowledged', {}))
            observed = dict(self.observations.get(identity, {}))
            initialized = state.get('initialized', False)
            try:
                connection = saved_connection(w, item['site'], unattended=True, direction='upload')
                connection.site = replace(connection.site, timezone='')
            except Exception as error:
                self.pause(identity, f'Saved connection unavailable ({type(error).__name__}); check credentials and trust.')
                return
            def work(worker):
                ack = dict(acknowledged)
                listing = scan(None, config['local'], worker.cancel)
                files = {key: info for key, info in listing.items() if not info['directory'] and not info['link'] and not excluded(key, config['excludes'])}
                if not initialized and not config['existing']:
                    ack.update(files)
                ready = stable_candidates(files, observed, ack, time.monotonic(), config['settle'])
                ready = [key for key in ready if key not in blocked][:100]
                descriptors = []
                if ready:
                    with connection:
                        for relative in ready:
                            if worker.cancel.is_set():
                                from .core import Cancelled
                                raise Cancelled('Watch scan cancelled')
                            destination = lookup(connection, config['remote'], relative)
                            if destination and (destination['directory'] or destination['link']):
                                raise ValueError('Watch destination is not a regular file.')
                            if destination and not config['overwrite']:
                                ack[relative] = files[relative]
                                continue
                            descriptors.append(dict(src=joined(config['local'], relative, False),
                                dst=joined(config['remote'], relative, True), name=posixpath.basename(relative),
                                directory=False, size=files[relative]['size'], resume=False, state='queued', held=not config['auto'],
                                retries=0, workspace=0, source_side=1, destination_side=2,
                                source_site=None, destination_site=asdict(connection.site),
                                sync=dict(source_root=config['local'], destination_root=config['remote'], relative=relative,
                                          source=files[relative], destination=destination),
                                watch=dict(id=identity, relative=relative, fingerprint=files[relative])))
                # Retain only present local paths; re-created files will be detected anew.
                ack = {key: info for key, info in ack.items() if key in files}
                return descriptors, observed, ack, set(files)
            def done(result):
                self.worker = None
                if identity not in self.watches or not self.watches[identity]['enabled']: return
                items, observations, ack, present = result
                self.observations[identity] = observations
                state['acknowledged'] = {key: info for key, info in state.get('acknowledged', {}).items() if key in present}
                state['acknowledged'].update(ack)
                state.update(initialized=True, status=f'Watching; {len(items)} upload(s) queued this scan.')
                try:
                    self.save()
                    # Match all existing destinations too, including manually queued jobs.
                    def target_key(site, path):
                        return (site.host.casefold(), site.port, site.username, site.protocol, site.cloud_account, posixpath.normpath(path)) if site else None
                    existing = {target_key(job.get('destination_site'), job.get('dst', '')) for job in w.queue_jobs if job.get('state') in ('queued', 'active', 'failed')}
                    items = [job for job in items if target_key(connection.site, job['dst']) not in existing]
                    if items:
                        paused = w.paused
                        previous = {id(job) for job in w.queue_jobs}
                        w.restore_queue_items(items)
                        w.paused = paused
                        for job in w.queue_jobs:
                            if id(job) not in previous:
                                job.update(source_password='', destination_password=connection.password, needs_credentials=False,
                                           suppress_retry=True)
                        if not w.save_queue_state():
                            for job in w.queue_jobs:
                                if id(job) not in previous: job['held'] = True
                            self.pause(identity, 'Queue could not be saved; uploads are held. Enable queue autosave before resuming.')
                            return
                        if config['auto']: w.start_next()
                except (OSError, ValueError) as error:
                    self.pause(identity, f'Watch could not save or queue work ({type(error).__name__}).')
            def failed(error):
                self.worker = None
                self.pause(identity, f'Watch scan failed ({type(error).__name__}); check source, destination and permissions.')
            self.worker = w.run(work, done, failed, raw_failure=True, autostart=False)
            self.worker.start()
            return

    def shutdown(self):
        self.timer.stop()

    def show(self):
        dialog = WatchDialog(self)
        dialog.exec()
        dialog.deleteLater()


class WatchEditor(QDialog):
    def __init__(self, controller, parent):
        super().__init__(parent)
        self.setWindowTitle('Add watch-and-upload folder')
        layout = QVBoxLayout(self)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        layout.addWidget(scroll)
        form = QFormLayout()
        self.name, self.local, self.remote = QLineEdit(), QLineEdit(), QLineEdit('/')
        self.site = QComboBox()
        self.site.addItems(sorted(n for n, s in controller.window.sites.items() if not s.get('_disabled')))
        self.settle = QSpinBox()
        self.settle.setRange(2, 3600)
        self.settle.setValue(10)
        self.settle.setSuffix(' seconds')
        self.excludes = QLineEdit('*.tmp;*.part;*.fluxpart;~*')
        for title, widget in (('Name', self.name), ('Local folder', self.local), ('Saved destination site', self.site),
                ('Remote folder', self.remote), ('Unchanged for', self.settle), ('Exclude masks (; separated)', self.excludes)):
            from .accessibility import name_control
            name_control(widget, title)
            form.addRow(title, widget)
        browse = QPushButton('Choose local folder…')
        def choose():
            path = QFileDialog.getExistingDirectory(self, 'Watch local folder', self.local.text())
            if path: self.local.setText(path)
        browse.clicked.connect(choose)
        form.addRow(browse)
        self.existing = QCheckBox('Include files already present when first scanned')
        self.overwrite = QCheckBox('Overwrite existing destination files when the local file changes')
        self.auto = QCheckBox('Start new uploads automatically when the queue is not paused')
        for control in (self.existing, self.overwrite, self.auto): form.addRow(control)
        content_layout.addLayout(form)
        note = QLabel('Watches run while FluxFTP is open. New watches are enabled on save. '
            'Uploads otherwise enter the held queue. Existing destination files are skipped unless overwrite is selected. '
            'Source deletions never delete remote files. Failed uploads pause the watch.')
        note.setWordWrap(True)
        content_layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.resize(760, 520)
        from .accessibility import fit_dialog
        fit_dialog(self)

    def value(self):
        root = Path(self.local.text().strip())
        if not root.is_dir(): raise ValueError('Choose an existing local folder.')
        for path in (root, *root.parents):
            if local_fingerprint(path)['link']: raise ValueError('Watch roots cannot pass through links or junctions.')
        return validate_watch(dict(id=uuid.uuid4().hex, name=self.name.text().strip(), local=str(root.absolute()),
            remote=self.remote.text().strip(), site=self.site.currentText(), settle=self.settle.value(),
            excludes=[p.strip() for p in self.excludes.text().split(';') if p.strip()], existing=self.existing.isChecked(),
            overwrite=self.overwrite.isChecked(), auto=self.auto.isChecked(), enabled=True))


class WatchDialog(QDialog):
    def __init__(self, controller):
        super().__init__(controller.window)
        self.controller = controller
        self.setWindowTitle('Watch-and-upload Folders')
        self.resize(1000, 500)
        layout = QVBoxLayout(self)
        self.status = QLabel(controller.problem or 'Persistent local folder watches. Pause/remove stops discovery; already queued uploads remain in the queue.')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(['Name', 'Enabled', 'Local folder', 'Destination', 'Last result'])
        self.tree.setAccessibleName('Watch-and-upload folders')
        layout.addWidget(self.tree)
        buttons = QHBoxLayout()
        for title, callback in (('Add…', self.add), ('Enable / Pause', self.toggle), ('Remove', self.remove), ('Refresh', self.refresh), ('Close', self.accept)):
            button = QPushButton(title)
            button.clicked.connect(callback)
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.refresh()

    def refresh(self):
        self.tree.clear()
        for identity, item in self.controller.watches.items():
            row = QTreeWidgetItem([item['name'], 'Yes' if item['enabled'] else 'No', item['local'],
                item['site'] + ': ' + item['remote'], self.controller.states.get(identity, {}).get('status', 'Not scanned')])
            row.setData(0, Qt.ItemDataRole.UserRole, identity)
            self.tree.addTopLevelItem(row)

    def selected(self):
        row = self.tree.currentItem()
        return row.data(0, Qt.ItemDataRole.UserRole) if row else None

    def commit(self):
        try: self.controller.save()
        except (OSError, ValueError) as error:
            self.controller.problem = str(error)
            self.status.setText(str(error))
        self.refresh()

    def add(self):
        if self.controller.problem or not self.controller.window.automation.owner:
            self.status.setText(self.controller.problem or 'Manage watches in the instance that owns automation.')
            return
        if len(self.controller.watches) >= 200:
            self.status.setText('At most 200 folder watches can be saved.')
            return
        dialog = WatchEditor(self.controller, self)
        try:
            while dialog.exec():
                try: item = dialog.value()
                except ValueError as error:
                    self.controller.window.error(str(error))
                    continue
                self.controller.watches[item['id']] = item
                self.commit()
                break
        finally: dialog.deleteLater()

    def toggle(self):
        identity = self.selected()
        if identity:
            item = self.controller.watches[identity]
            item['enabled'] = not item['enabled']
            self.controller.states.setdefault(identity, {})['next'] = 0
            self.commit()

    def remove(self):
        identity = self.selected()
        if identity:
            self.controller.watches.pop(identity)
            self.controller.states.pop(identity, None)
            self.controller.observations.pop(identity, None)
            self.commit()
