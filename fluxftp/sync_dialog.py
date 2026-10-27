"""Recursive synchronization preview; mutations are queued only after review."""
import posixpath
from contextlib import ExitStack
from dataclasses import asdict, replace
from threading import Event

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QSpinBox, QPushButton, QTreeWidget, QTreeWidgetItem, QCheckBox, QInputDialog, QMessageBox,
    QScrollArea, QWidget, QGridLayout)

from .core import Cancelled
from .folder_sync import scan, compare, joined, stamp, check_roots


def show_sync(window):
    panes = window.current_panes()
    if len(panes) != 2 or any(p.busy or (p.mode == 'Remote' and p.site is None) for p in panes):
        window.error('Wait for both panes to load and connect each remote pane before synchronizing.')
        return
    sites = [replace(p.site, timezone='') if p.mode == 'Remote' else None for p in panes]
    if all(sites) and not any(s.cloud_account for s in sites) and any(s.protocol == 'SFTP · SSH' for s in sites):
        window.error('SFTP server-to-server synchronization requires relay support. Use a local folder as one side.')
        return
    try:
        check_roots(*sites, *(p.path for p in panes))
    except ValueError as error:
        window.error(str(error))
        return
    try: dialog = SyncDialog(window, panes, sites)
    except (OSError, ValueError, TypeError, KeyError) as error:
        window.error('Could not read synchronization profiles: ' + str(error))
        return
    try: dialog.exec()
    finally: dialog.deleteLater()


class SyncDialog(QDialog):
    PAGE = 200

    def __init__(self, window, panes, sites):
        super().__init__(window)
        self.window, self.panes, self.sites = window, panes, sites
        self.roots = [p.path for p in panes]
        self.passwords = [p.password for p in panes]
        self.generations = [p.generation for p in panes]
        self.listings = None
        self.plan = []
        self.offset = 0
        self.scanning = False
        self.close_when_done = False
        self.cancel = Event()
        from .sync_profiles import SyncProfiles, pair_key
        self.profile_store = SyncProfiles(window.sites_file.with_name('sync-profiles.json'))
        self.pair_key = pair_key(sites, self.roots)
        self.setWindowTitle('Synchronize Folders')
        self.resize(1100, 700)
        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)
        scroll.setWidget(content)
        outer.addWidget(scroll)
        for side, root, site in zip(('Left', 'Right'), self.roots, sites):
            label = QLabel(f'{side}: {site.name if site else "Local"} · {root}')
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
            layout.addWidget(label)
        note = QLabel('Review copies, conflicts and optional mirror file deletions. Changes are checked again before execution. '
                      'Checksum mode requires a common native hash supported by both endpoints. Mirror keeps folders and never deletes recursively.')
        note.setWordWrap(True)
        layout.addWidget(note)
        options = QGridLayout()
        self.mode, self.rule = QComboBox(), QComboBox()
        self.mode.addItems(['Left → Right', 'Right → Left', 'Two-way'])
        self.rule.addItems(['Size and time', 'Size only', 'Checksum'])
        self.mirror = QCheckBox('Review destination-only file deletions (one-way mirror)')
        self.mirror.setAccessibleName('Review mirror file deletions')
        self.mode.setAccessibleName('Synchronization direction')
        self.rule.setAccessibleName('Synchronization comparison rule')
        self.tolerance = QSpinBox()
        self.tolerance.setRange(0, 3600)
        self.tolerance.setValue(2)
        self.tolerance.setSuffix(' s')
        from .accessibility import name_control
        name_control(self.tolerance, 'Timestamp tolerance')
        for column, (label, control) in enumerate([('Direction', self.mode), ('Compare', self.rule), ('Time tolerance', self.tolerance)]):
            options.addWidget(QLabel(label), 0, column * 2)
            options.addWidget(control, 0, column * 2 + 1)
        self.scan_button = QPushButton('Scan / Refresh preview')
        self.scan_button.clicked.connect(self.start_scan)
        options.addWidget(self.scan_button, 1, 0, 1, 6)
        layout.addLayout(options)
        layout.addWidget(self.mirror)
        profiles = QHBoxLayout()
        self.profile = QComboBox()
        self.profile.setAccessibleName('Saved synchronization profile')
        self.profile.addItems(sorted(self.profile_store.data['profiles']))
        profiles.addWidget(self.profile)
        self.profile_buttons = []
        for title, callback in (('Load', self.load_profile), ('Save…', self.save_profile),
                                ('Delete', self.delete_profile), ('Save scan baseline', self.save_baseline)):
            button = QPushButton(title)
            button.setAccessibleName(title + ' synchronization profile' if title != 'Save scan baseline' else title)
            button.clicked.connect(callback)
            profiles.addWidget(button)
            self.profile_buttons.append(button)
        layout.addLayout(profiles)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(['Relative path', 'Comparison', 'Left bytes', 'Right bytes', 'Planned action', 'Since baseline'])
        self.tree.setRootIsDecorated(False)
        self.tree.setMinimumHeight(180)
        self.tree.setAccessibleName('Synchronization preview')
        self.tree.setColumnWidth(0, 310)
        self.tree.setColumnWidth(1, 280)
        self.tree.setColumnWidth(4, 150)
        layout.addWidget(self.tree)
        self.status = QLabel('Scan both folders to build a preview. No files will be changed by the scan.')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        resolution = QHBoxLayout()
        self.conflicts = QComboBox()
        self.conflicts.addItems(['Skip', 'Left → Right', 'Right → Left', 'Newer file wins'])
        self.conflicts.setAccessibleName('Bulk conflict resolution')
        self.resolve_button = QPushButton('Apply to all conflicts')
        self.resolve_button.clicked.connect(self.resolve_conflicts)
        resolution.addWidget(self.conflicts)
        resolution.addWidget(self.resolve_button)
        layout.addLayout(resolution)
        buttons = QHBoxLayout()
        self.previous, self.next = QPushButton('Previous'), QPushButton('Next')
        self.previous.clicked.connect(lambda: self.page(-self.PAGE))
        self.next.clicked.connect(lambda: self.page(self.PAGE))
        self.queue_button = QPushButton('Queue planned changes')
        self.queue_button.clicked.connect(self.enqueue)
        self.close_button = QPushButton('Close')
        self.close_button.clicked.connect(self.reject)
        for button in (self.previous, self.next, self.queue_button, self.close_button): buttons.addWidget(button)
        outer.addLayout(buttons)
        self.mode.currentTextChanged.connect(self.recompare)
        self.rule.currentTextChanged.connect(self.recompare)
        self.tolerance.valueChanged.connect(self.recompare)
        self.mirror.toggled.connect(self.recompare)
        self.controls()
        from .accessibility import fit_dialog
        fit_dialog(self)

    def controls(self):
        for widget in (self.mode, self.rule, self.tolerance, self.scan_button, self.resolve_button, self.conflicts, self.profile, self.mirror, *self.profile_buttons):
            widget.setEnabled(not self.scanning)
        self.queue_button.setEnabled(not self.scanning and any(row['action'] != 'Skip' for row in self.plan))
        self.previous.setEnabled(not self.scanning and self.offset > 0)
        self.next.setEnabled(not self.scanning and self.offset + self.PAGE < len(self.plan))
        self.close_button.setText('Cancel scan' if self.scanning else 'Close')
        self.mirror.setEnabled(not self.scanning and self.mode.currentText() != 'Two-way')
        self.profile_buttons[-1].setEnabled(not self.scanning and self.listings is not None)

    def valid(self):
        return all(p.path == root and p.generation == generation and not p.busy and
                   (replace(p.site, timezone='') if p.mode == 'Remote' and p.site else None) == site
                   for p, root, generation, site in zip(self.panes, self.roots, self.generations, self.sites))

    def start_scan(self):
        if self.scanning: return
        if not self.valid():
            self.status.setText('A pane changed. Close this dialog and open a new synchronization preview.')
            return
        self.scanning = True
        self.listings = None
        self.plan = []
        self.tree.clear()
        self.cancel.clear()
        self.status.setText('Scanning both folders… No files are being changed.')
        self.controls()
        hash_requested = self.rule.currentText() == 'Checksum'
        def task(worker):
            with ExitStack() as stack:
                connections = []
                for site, password, root in zip(self.sites, self.passwords, self.roots):
                    if self.cancel.is_set(): raise Cancelled('Synchronization scan cancelled')
                    connection = stack.enter_context(self.window.new_connection(site, password)) if site else None
                    connections.append(connection)
                algorithm = None
                if hash_requested:
                    from .transfer_reliability import algorithms, ALGORITHMS
                    common = algorithms(connections[0]) & algorithms(connections[1])
                    algorithm = next((a for a in ALGORITHMS if a in common), None)
                    if not algorithm: raise ValueError('No common native checksum algorithm. Choose metadata comparison or a server with hash support.')
                return [scan(connection, root, self.cancel, hash_algorithm=algorithm) for connection, root in zip(connections, self.roots)]
        def done(result):
            self.scanning = False
            if self.close_when_done:
                super(SyncDialog, self).reject()
                return
            self.listings = result
            self.recompare()
        def failed(message):
            self.scanning = False
            self.status.setText(str(message))
            self.controls()
            if self.close_when_done: super(SyncDialog, self).reject()
        self.window.run(task, done, failed)

    def reject(self):
        if self.scanning:
            self.close_when_done = True
            self.cancel.set()
            self.status.setText('Cancelling scan; waiting for the current directory request to return…')
            return
        super().reject()

    def recompare(self, *_):
        self.controls()
        if self.listings is None: return
        if self.rule.currentText() == 'Checksum' and any(not info.get('digest') for listing in self.listings for info in listing.values() if not info['directory'] and not info['link']):
            self.plan = []
            self.tree.clear()
            self.status.setText('Scan again to compute checksums for both folders.')
            self.controls()
            return
        self.plan = compare(*self.listings, self.mode.currentText(), self.rule.currentText(), self.tolerance.value())
        from .sync_profiles import enrich_plan
        enrich_plan(self.plan, *self.listings, self.mode.currentText(), self.mirror.isChecked(),
                    self.profile_store.data['baselines'].get(self.pair_key))
        self.offset = 0
        self.render()

    def page(self, delta):
        self.offset = max(0, self.offset + delta)
        self.render()

    def render(self):
        self.tree.clear()
        for item in self.plan[self.offset:self.offset + self.PAGE]:
            sizes = [('Folder' if info['directory'] else str(info['size'])) if info else '—' for info in (item['left'], item['right'])]
            row = QTreeWidgetItem([item['path'], item['reason'], *sizes, '', item.get('change', '')])
            row.setToolTip(0, item['path'])
            row.setToolTip(1, item['reason'])
            self.tree.addTopLevelItem(row)
            action = QComboBox()
            action.setAccessibleName('Action for ' + item['path'])
            action.addItems(item['choices'])
            action.setCurrentText(item['action'])
            action.setEnabled(len(item['choices']) > 1)
            action.currentTextChanged.connect(lambda value, item=item: self.choose(item, value))
            self.tree.setItemWidget(row, 4, action)
        self.summary()

    def choose(self, item, value):
        item['action'] = value
        self.summary()

    def summary(self):
        count = sum(row['action'] != 'Skip' for row in self.plan)
        unresolved = sum(row['reason'].startswith('Conflict') and row['action'] == 'Skip' for row in self.plan)
        self.status.setText(f'{len(self.plan):,} entries · {count:,} planned changes · {unresolved:,} conflicts skipped. '
                            f'Showing {self.offset + 1 if self.plan else 0}–{min(len(self.plan), self.offset + self.PAGE)}. '
                            'Queued changes are held until you choose Transfer Queue.')
        self.controls()

    def resolve_conflicts(self):
        choice = self.conflicts.currentText()
        for row in self.plan:
            if not row['reason'].startswith('Conflict'): continue
            action = choice
            if choice == 'Newer file wins':
                a, b = stamp(row['left']['modified']), stamp(row['right']['modified'])
                action = 'Skip' if a is None or b is None or abs(a - b) <= self.tolerance.value() else 'Left → Right' if a > b else 'Right → Left'
            row['action'] = action if action in row['choices'] else 'Skip'
        self.render()

    def descriptors(self):
        for row in self.plan:
            if row['action'] == 'Skip': continue
            deleting = row['action'].startswith('Delete')
            side = 0 if row['action'] in ('Left → Right', 'Delete right') else 1
            source, destination = (row['left'], row['right']) if side == 0 else (row['right'], row['left'])
            site, other = self.sites[side], self.sites[1-side]
            yield dict(src=joined(self.roots[side], row['path'], site is not None),
                dst=joined(self.roots[1-side], row['path'], other is not None),
                name=('Delete: ' if deleting else '') + posixpath.basename(row['path']), directory=False if deleting else source['directory'], size=0 if deleting else source['size'],
                resume=False, state='queued', held=True, retries=0, workspace=self.window.tabs.currentIndex(),
                source_site=asdict(site) if site else None, destination_site=asdict(other) if other else None,
                source_side=side + 1, destination_side=2 - side,
                sync=dict(source_root=self.roots[side], destination_root=self.roots[1-side],
                          relative=row['path'], source=source, destination=destination,
                          **({'operation': 'delete'} if deleting else {})))

    def enqueue(self):
        if self.scanning or not self.valid():
            self.status.setText('The panes changed. Close this dialog and build a new preview.')
            return
        items = list(self.descriptors())
        if not items: return
        deletions = sum(item['sync'].get('operation') == 'delete' for item in items)
        if deletions and QMessageBox.question(self, 'Review mirror deletions',
                f'Queue permanent deletion of {deletions} destination-only file(s) shown in this preview? '
                'These files will be deleted when you start the queue. Folders are retained.') != QMessageBox.StandardButton.Yes:
            return
        w = self.window
        paused = w.paused
        previous = {id(job) for job in w.queue_jobs}
        w.restore_queue_items(items)
        w.paused = paused
        # Reuse only this session's credentials; durable queue/history never save them.
        for job in w.queue_jobs:
            if id(job) not in previous and job.get('sync') and job.get('needs_credentials'):
                matches = [next((i for i, site in enumerate(self.sites) if site == job[key + '_site']), None)
                           for key in ('source', 'destination')]
                if all(index is not None for index in matches):
                    for key, index in zip(('source', 'destination'), matches): job[key + '_password'] = self.passwords[index]
                    job['needs_credentials'] = False
        w.save_queue_state()
        w.show_transfer_queue()
        w.statusBar().showMessage(f'{len(items)} synchronization changes queued and held. Transfer Queue starts them.', 10000)
        self.accept()

    def save_profile(self):
        name, ok = QInputDialog.getText(self, 'Save synchronization profile', 'Profile name')
        if not ok or not name.strip(): return
        self.profile_store.data['profiles'][name.strip()[:100]] = dict(pair=self.pair_key, roots=self.roots,
            sites=[s.name if s else 'Local' for s in self.sites], mode=self.mode.currentText(),
            rule=self.rule.currentText(), tolerance=self.tolerance.value(), mirror=self.mirror.isChecked())
        self.persist_profiles()
        self.profile.clear()
        self.profile.addItems(sorted(self.profile_store.data['profiles']))
        self.profile.setCurrentText(name.strip()[:100])

    def load_profile(self):
        item = self.profile_store.data['profiles'].get(self.profile.currentText())
        if not item: return
        if item.get('pair') != self.pair_key:
            self.status.setText('Open this profile’s folders in the panes first: ' + ' | '.join(
                f'{site}: {root}' for site, root in zip(item.get('sites', []), item.get('roots', []))))
            return
        self.mode.setCurrentText(item.get('mode', 'Left → Right'))
        self.rule.setCurrentText(item.get('rule', 'Size and time'))
        self.tolerance.setValue(item.get('tolerance', 2))
        self.mirror.setChecked(item.get('mirror', False))
        self.recompare()

    def delete_profile(self):
        self.profile_store.data['profiles'].pop(self.profile.currentText(), None)
        self.persist_profiles()
        self.profile.removeItem(self.profile.currentIndex())

    def persist_profiles(self):
        try:
            if not self.window.automation.owner: raise ValueError('Save synchronization settings in the instance that owns automation.')
            self.profile_store.save()
        except (OSError, ValueError) as error: self.window.error(str(error))

    def save_baseline(self):
        if self.listings is None or self.scanning: return
        self.profile_store.data['baselines'][self.pair_key] = self.listings
        self.persist_profiles()
        self.recompare()
