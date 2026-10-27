"""Saved deployment profiles, read-only previews and explicit queue approval."""
from dataclasses import asdict, replace
import json
from pathlib import Path
import time
import uuid

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QHBoxLayout,
    QLineEdit, QComboBox, QPushButton, QLabel, QTreeWidget, QTreeWidgetItem,
    QDialogButtonBox, QFileDialog, QInputDialog, QMessageBox)

from .automation import saved_connection
from .deployments import validate_profile, preview, revision_path, read_revision, rollback_preview
from .sync_profiles import atomic_json


class ProfileEditor(QDialog):
    def __init__(self, window, profile=None):
        super().__init__(window)
        self.setWindowTitle('Deployment Profile')
        form = QFormLayout(self)
        self.name, self.source, self.destination = QLineEdit(), QLineEdit(), QLineEdit('/')
        self.site, self.verification = QComboBox(), QComboBox()
        self.site.addItems(sorted(n for n, s in window.sites.items() if not s.get('_disabled')))
        self.verification.addItems(['Checksum readback', 'Size only'])
        self.excludes = QLineEdit('.git;*.tmp;*.part;*.fluxpart')
        for title, control in (('Name', self.name), ('Local source folder', self.source), ('Saved destination site', self.site),
            ('Remote destination folder', self.destination), ('Exclusions (; separated)', self.excludes), ('Verify uploaded files', self.verification)):
            control.setAccessibleName(title)
            form.addRow(title, control)
        browse = QPushButton('Choose source folder…')
        def choose():
            path = QFileDialog.getExistingDirectory(self, 'Deployment source', self.source.text())
            if path: self.source.setText(path)
        browse.clicked.connect(choose)
        form.addRow(browse)
        note = QLabel('Changed files overwrite the destination only after preview approval. Previous files are backed up locally. '
            'Destination-only files and empty folders are retained. Checksum readback may download files when native hashes are unavailable.')
        note.setWordWrap(True)
        form.addRow(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        if profile:
            for key in ('name', 'source', 'destination'): getattr(self, key).setText(profile[key])
            self.site.setCurrentText(profile['site'])
            self.verification.setCurrentText(profile['verification'])
            self.excludes.setText(';'.join(profile['excludes']))
        self.resize(700, 420)

    def value(self):
        source = Path(self.source.text().strip())
        if not source.is_dir(): raise ValueError('Choose an existing local source folder.')
        return validate_profile(dict(name=self.name.text().strip(), source=str(source.absolute()), site=self.site.currentText(),
            destination=self.destination.text().strip(), excludes=[p.strip() for p in self.excludes.text().split(';') if p.strip()],
            verification=self.verification.currentText()))


class DeploymentDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window, self.worker, self.closing, self.plan = window, None, False, None
        self.path = window.sites_file.with_name('deployment-profiles.json')
        self.profiles = {}
        if self.path.exists():
            if self.path.stat().st_size > 4_000_000: raise ValueError('Deployment profiles exceed 4 MB.')
            data = json.loads(self.path.read_text('utf-8'))
            if not isinstance(data, dict) or len(data) > 200: raise ValueError('Invalid deployment profiles.')
            self.profiles = {key: validate_profile(value) for key, value in data.items()}
        self.setWindowTitle('Deployment Profiles')
        self.resize(1000, 660)
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        self.profile = QComboBox()
        self.profile.setAccessibleName('Saved deployment profile')
        top.addWidget(self.profile, 1)
        self.edit_buttons = []
        for label, callback in (('New…', lambda: self.edit(False)), ('Edit…', lambda: self.edit(True)), ('Remove profile', self.remove)):
            button = QPushButton(label)
            button.clicked.connect(callback)
            top.addWidget(button)
            self.edit_buttons.append(button)
        layout.addLayout(top)
        self.description = QLabel()
        self.description.setTextFormat(Qt.TextFormat.PlainText)
        self.description.setWordWrap(True)
        layout.addWidget(self.description)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(['Relative path', 'Planned action', 'Source bytes', 'Previous bytes'])
        self.tree.setColumnWidth(0, 380)
        self.tree.setColumnWidth(1, 230)
        self.tree.setColumnWidth(2, 120)
        self.tree.setRootIsDecorated(False)
        self.tree.setAccessibleName('Deployment preview')
        layout.addWidget(self.tree)
        self.status = QLabel('Select a profile, then preview. No remote files change during preview.')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        self.preview_button, self.rollback_button, self.queue_button, self.close_button = [QPushButton(t) for t in
            ('Preview deployment', 'Preview rollback…', 'Queue approved plan', 'Close')]
        for button in (self.preview_button, self.rollback_button, self.queue_button, self.close_button): buttons.addWidget(button)
        layout.addLayout(buttons)
        self.preview_button.clicked.connect(self.start)
        self.rollback_button.clicked.connect(self.select_rollback)
        self.queue_button.clicked.connect(self.enqueue)
        self.close_button.clicked.connect(self.reject)
        self.profile.currentTextChanged.connect(self.invalidate)
        self.refresh()

    def refresh(self):
        selected = self.profile.currentText()
        self.profile.clear()
        self.profile.addItems(sorted(self.profiles))
        if selected in self.profiles: self.profile.setCurrentText(selected)
        self.invalidate()

    def invalidate(self, *_):
        self.plan = None
        self.tree.clear()
        item = self.profiles.get(self.profile.currentText())
        self.description.setText(f'{item["source"]} → {item["site"]}: {item["destination"]}\nVerification: {item["verification"]}' if item else 'Create a deployment profile to begin.')
        self.controls()

    def controls(self):
        for control in (self.profile, self.preview_button, self.rollback_button, *self.edit_buttons): control.setEnabled(not self.worker)
        self.queue_button.setEnabled(not self.worker and self.plan is not None)

    def save(self, profiles):
        if not self.window.automation.owner: raise ValueError('Manage deployment profiles in the instance that owns automation.')
        if len(profiles) > 200: raise ValueError('At most 200 deployment profiles are supported.')
        atomic_json(self.path, profiles)
        self.profiles = profiles

    def edit(self, existing):
        name = self.profile.currentText() if existing else None
        dialog = ProfileEditor(self.window, self.profiles.get(name))
        try:
            while dialog.exec():
                try:
                    item = dialog.value()
                    if item['name'] != name and item['name'] in self.profiles: raise ValueError('A profile already has this name.')
                    profiles = dict(self.profiles)
                    if name: profiles.pop(name, None)
                    profiles[item['name']] = item
                    self.save(profiles)
                    self.refresh()
                    self.profile.setCurrentText(item['name'])
                    break
                except (OSError, ValueError) as error: self.window.error(str(error))
        finally: dialog.deleteLater()

    def remove(self):
        profiles = dict(self.profiles)
        profiles.pop(self.profile.currentText(), None)
        try: self.save(profiles); self.refresh()
        except (OSError, ValueError) as error: self.window.error(str(error))

    def start(self, checked=False, revision=None):
        if self.worker: return
        profile = self.profiles.get(self.profile.currentText())
        if revision:
            try: profile = read_revision(revision_path(self.window, revision))['plan']['profile']
            except (OSError, ValueError, KeyError) as error: self.window.error(str(error)); return
        if not profile: return
        try:
            connection = saved_connection(self.window, profile['site'], unattended=True, direction='upload')
            connection.site = replace(connection.site, timezone='')
        except Exception as error: self.window.error(str(error)); return
        self.plan = None
        self.tree.clear()
        self.status.setText('Scanning and checking file contents… No remote files are being changed.')
        def work(worker):
            with connection:
                if revision:
                    journal, rows = rollback_preview(revision_path(self.window, revision), connection, worker.cancel)
                    return dict(journal['plan'], rollback=True), rows
                files = preview(profile, connection, worker.cancel)
                plan = dict(id=uuid.uuid4().hex, profile=profile, files=files, rollback=False)
                return plan, [dict(relative=f['relative'], action='Overwrite (backup first)' if f['destination'] else 'Upload new file',
                    source=f['source'], destination=f['destination']) for f in files]
        def done(result):
            plan, rows = result
            self.plan = plan if plan['files'] and any(not row['action'].startswith('Already') for row in rows) else None
            self.connection = connection
            for row in rows[:10000]:
                self.tree.addTopLevelItem(QTreeWidgetItem([row['relative'], row['action'],
                    str((row.get('source') or {}).get('size', '—')), str((row.get('destination') or {}).get('size', '—'))]))
            self.status.setText(f'{len(rows)} file(s) reviewed. Queueing holds the plan until Transfer Queue starts it. '
                'Rollback is guarded against later destination changes; partial/interrupted writes may need manual review.')
            self.finish()
        def failed(error):
            from .diagnostics import redact
            self.status.setText(redact(str(error), (connection.password, (connection.proxy or {}).get('password', ''))))
            self.finish()
        self.worker = self.window.run(work, done, failed, raw_failure=True, autostart=False)
        self.controls()
        self.worker.start()

    def finish(self):
        self.worker = None
        self.controls()
        if self.closing: super().reject()

    def select_rollback(self):
        root = self.window.sites_file.parent / 'deployments'
        revisions = []
        if root.exists():
            for path in sorted(root.iterdir(), key=lambda p: p.name, reverse=True):
                try:
                    journal = read_revision(revision_path(self.window, path.name))
                    revisions.append((journal['created'], path.name, journal))
                except (OSError, ValueError, KeyError, TypeError): continue
        revisions.sort(reverse=True)
        choices = [f'{time.strftime("%Y-%m-%d %H:%M", time.localtime(created))} · {j["plan"]["profile"]["name"]} · {j["state"]} · {identity}' for created, identity, j in revisions[:100]]
        if not choices:
            self.status.setText('No deployment rollback journals are available.')
            return
        chosen, ok = QInputDialog.getItem(self, 'Preview rollback', 'Deployment revision', choices, 0, False)
        if ok: self.start(revision=revisions[choices.index(chosen)][1])

    def enqueue(self):
        if not self.plan or self.worker: return
        plan, connection, w = self.plan, self.connection, self.window
        action = 'Rollback' if plan['rollback'] else 'Deploy'
        if QMessageBox.question(self, 'Approve deployment plan',
            f'{action} the reviewed plan for {plan["profile"]["site"]}: {plan["profile"]["destination"]}? '
            'Rollback restores backed-up files and deletes files created by that deployment. The queued operation starts only when you start Transfer Queue.'
            if plan['rollback'] else f'Queue deployment of {len(plan["files"])} reviewed file(s) to {plan["profile"]["site"]}: {plan["profile"]["destination"]}? Existing files will be backed up locally before overwrite.') != QMessageBox.StandardButton.Yes:
            return
        item = dict(src=plan['profile']['source'], dst=plan['profile']['destination'], name=action + ': ' + plan['profile']['name'],
            directory=True, size=sum(f['source']['size'] for f in plan['files']), resume=False, state='queued', held=True,
            retries=0, workspace=w.tabs.currentIndex(), source_side=1, destination_side=2, source_site=None,
            destination_site=asdict(connection.site), deployment=plan)
        previous, paused = {id(job) for job in w.queue_jobs}, w.paused
        w.restore_queue_items([item])
        w.paused = paused
        for job in w.queue_jobs:
            if id(job) not in previous: job.update(source_password='', destination_password=connection.password, needs_credentials=False)
        w.save_queue_state()
        w.show_transfer_queue()
        self.accept()

    def reject(self):
        if self.worker:
            self.closing = True
            self.worker.cancel.set()
            self.status.setText('Cancelling preview; waiting for the current network operation…')
        else: super().reject()

    def closeEvent(self, event):
        if self.worker: self.reject(); event.ignore()
        else: super().closeEvent(event)


def show_deployments(window):
    try: dialog = DeploymentDialog(window)
    except (OSError, ValueError, KeyError, TypeError) as error:
        window.error('Could not read deployment profiles: ' + str(error))
        return
    dialog.exec()
    dialog.deleteLater()
