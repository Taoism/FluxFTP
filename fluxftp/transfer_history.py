"""Bounded, credential-free transfer history and a paged desktop browser."""
import json
import sqlite3
import time
from contextlib import closing
from dataclasses import asdict
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLineEdit,
    QComboBox, QTreeWidget, QTreeWidgetItem, QPushButton, QLabel, QSpinBox,
    QApplication, QMessageBox, QFileDialog)

from .diagnostics import redact


class HistoryStore:
    def __init__(self, path):
        self.path = Path(path)

    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=2)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA secure_delete=ON')
        db.execute('''CREATE TABLE IF NOT EXISTS transfers (
            id INTEGER PRIMARY KEY, finished REAL NOT NULL, outcome TEXT NOT NULL,
            route TEXT NOT NULL, src TEXT NOT NULL, dst TEXT NOT NULL,
            size INTEGER, elapsed REAL NOT NULL, detail TEXT NOT NULL, descriptor TEXT NOT NULL)''')
        db.execute('CREATE INDEX IF NOT EXISTS history_result ON transfers(outcome, id)')
        db.execute('CREATE INDEX IF NOT EXISTS history_age ON transfers(finished)')
        if 'verification' not in {row[1] for row in db.execute('PRAGMA table_info(transfers)')}:
            try: db.execute("ALTER TABLE transfers ADD COLUMN verification TEXT NOT NULL DEFAULT '{}'")
            except sqlite3.OperationalError as error:
                if 'duplicate column' not in str(error).lower(): raise
        from .verification_reports import schema
        schema(db)
        return db

    @staticmethod
    def prune(db, days, count):
        db.execute('DELETE FROM transfers WHERE finished < ?', (time.time() - max(1, days) * 86400,))
        cutoff = db.execute('SELECT id FROM transfers ORDER BY id DESC LIMIT 1 OFFSET ?', (max(1, count),)).fetchone()
        if cutoff: db.execute('DELETE FROM transfers WHERE id <= ?', (cutoff[0],))
        db.execute('DELETE FROM verification_files WHERE finished < ?', (time.time() - max(1, days) * 86400,))
        db.execute("DELETE FROM verification_files WHERE finished < ? AND report_id NOT IN (SELECT COALESCE(json_extract(verification,'$.report_id'),'') FROM transfers)", (time.time()-86400,))

    def append(self, records, days=90, count=10000):
        from collections import deque
        # A large cancellation used to repeatedly insert/prune the entire batch.
        # Only its retained tail can survive the configured history limit.
        cutoff = time.time() - max(1, days) * 86400
        retained = deque((record for record in records if record['finished'] >= cutoff), maxlen=max(1, count))
        with closing(self.connect()) as db, db:
            for record in retained:
                db.execute('INSERT INTO transfers(finished,outcome,route,src,dst,size,elapsed,detail,descriptor,verification) VALUES (?,?,?,?,?,?,?,?,?,?)',
                    (record['finished'], record['outcome'], record['route'], record['src'], record['dst'],
                     record['size'], record['elapsed'], record['detail'], json.dumps(record['descriptor'], ensure_ascii=False), json.dumps(record.get('verification', {}))))
            self.prune(db, days, count)

    def retain(self, days, count):
        with closing(self.connect()) as db, db:
            self.prune(db, days, count)
        # Reclaim disk space when the user explicitly reduces retention.
        with closing(self.connect()) as db:
            db.execute('VACUUM')

    def page(self, search='', outcome='All', offset=0, limit=100):
        where, args = [], []
        if outcome != 'All':
            where.append('outcome=?')
            args.append(outcome.lower())
        if search:
            where.append('(instr(lower(src),lower(?)) OR instr(lower(dst),lower(?)) OR instr(lower(detail),lower(?)))')
            args.extend([search] * 3)
        clause = ' WHERE ' + ' AND '.join(where) if where else ''
        with closing(self.connect()) as db:
            total = db.execute('SELECT count(*) FROM transfers' + clause, args).fetchone()[0]
            rows = db.execute('SELECT * FROM transfers' + clause + ' ORDER BY id DESC LIMIT ? OFFSET ?',
                              (*args, min(100, max(1, limit)), max(0, offset))).fetchall()
        return [dict(row) for row in rows], total

    def clear(self):
        with closing(self.connect()) as db, db:
            db.execute('DELETE FROM transfers')
            db.execute('DELETE FROM verification_files')
        with closing(self.connect()) as db:
            db.execute('VACUUM')


def descriptor(window, job):
    """Only queue fields, never runtime passwords, tasks or worker objects."""
    item = {key: job[key] for key in ('src', 'dst', 'directory')}
    item.update(name=job.get('name') or Path(job['src']).name, resume=job.get('resume', False),
                state='queued', retries=0, workspace=max(0, window.tabs.indexOf(job['workspace'])),
                size=job['row'].data(4, Qt.ItemDataRole.UserRole),
                source_side=job.get('source_side', 1), destination_side=job.get('destination_side', 2))
    for key in ('source_site', 'destination_site'):
        item[key] = asdict(job[key]) if job.get(key) else None
    if job.get('sync'):
        item['sync'] = job['sync']
    if job.get('watch'):
        item['watch'] = job['watch']
    if job.get('deployment'):
        item['deployment'] = job['deployment']
    if job.get('crc_job'):
        item.update(crc_job=True, crc_root=job.get('crc_root', ''), crc_attempt=0)
    return item


class TransferHistory(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.store = HistoryStore(window.sites_file.with_name('transfer-history.sqlite'))

    def limits(self):
        p = self.window.preferences
        return (max(1, min(3650, p.value('History/days', 90, type=int))),
                max(100, min(1000000, p.value('History/count', 10000, type=int))))

    def write(self, records):
        try:
            self.store.append(records, *self.limits())
        except (OSError, sqlite3.Error, ValueError, TypeError, KeyError) as error:
            self.window.log.appendPlainText(f'Transfer history could not be saved: {type(error).__name__}')

    def record(self, job, outcome=None, elapsed=0, detail=None):
        outcome = outcome or job.get('transfer_outcome') or job.get('state')
        if outcome not in ('complete', 'skipped', 'failed', 'cancelled') or 'src' not in job:
            return
        if getattr(self.window, 'watch_uploads', None):
            self.window.watch_uploads.completed(job, outcome)
        # A folder scan is not a completed transfer of the files it just queued.
        if job.get('buffered') or (job.get('queue_group_parent') and outcome == 'complete' and not job.get('sync')):
            return
        item = descriptor(self.window, job)
        item['dst'] = job.get('actual_dst', item['dst'])
        secrets = (job.get('source_password', ''), job.get('destination_password', ''))
        self.write([dict(finished=time.time(), outcome=outcome, route=job['route'], src=item['src'], dst=item['dst'],
            size=item['size'], elapsed=max(0, elapsed),
            detail=redact(detail if detail is not None else job['row'].toolTip(7), secrets)[:4096], descriptor=item,
            verification=job.get('verification', {}))])

    def cancelled(self, jobs):
        """Record unloaded descriptors without materializing a large queue."""
        def records():
            for job in jobs:
                items = job['buffer'] if job.get('buffered') else [descriptor(self.window, job)] if 'src' in job else []
                for item in items:
                    if getattr(self.window, 'watch_uploads', None):
                        self.window.watch_uploads.completed(item, 'cancelled')
                    yield dict(finished=time.time(), outcome='cancelled', route=job['route'], src=item['src'], dst=item['dst'],
                               size=item.get('size'), elapsed=0, detail='Cancelled before transfer', descriptor=item)
        self.write(records())

    def skipped(self, job, path, reason, directory):
        item = dict(job, src=path, name=Path(path).name, directory=directory)
        import posixpath
        relative = posixpath.relpath(path, job['src']) if job.get('source_site') else str(Path(path).relative_to(job['src']))
        item['dst'] = posixpath.join(job['dst'], relative.replace('\\', '/')) if job.get('destination_site') else str(Path(job['dst']) / relative)
        saved = descriptor(self.window, item)
        saved['size'] = None
        self.write([dict(finished=time.time(), outcome='skipped', route=job['route'], src=path, dst=saved['dst'],
                         size=None, elapsed=0, detail=redact(reason, (job.get('source_password', ''), job.get('destination_password', '')))[:4096], descriptor=saved)])

    def retry(self, records):
        w = self.window
        queued = 0
        seen = set()
        def identity(item):
            return (item['src'], item['dst'], json.dumps(item['source_site'], sort_keys=True), json.dumps(item['destination_site'], sort_keys=True))
        requested = {identity(json.loads(record['descriptor'])) for record in records if record['outcome'] == 'failed'}
        buffered = set()
        for parent in w.queue_jobs:
            if parent.get('buffered') and parent.get('state') == 'queued':
                for item in parent['buffer']:
                    key = identity(item)
                    if key in requested: buffered.add(key)
        for record in records:
            if record['outcome'] != 'failed':
                continue
            item = json.loads(record['descriptor'])
            if item.get('sync'):
                # Retain the approved preview fingerprints on retry.
                from .folder_sync import validate_spec
                validate_spec(item['sync'])
            key = identity(item)
            if key in seen or key in buffered:
                continue
            seen.add(key)
            matches = [j for j in w.queue_jobs if j.get('src') == item['src'] and j.get('dst') == item['dst']
                       and all((asdict(j[k]) if j.get(k) else None) == item[k] for k in ('source_site', 'destination_site'))
                       and j.get('state') in ('queued', 'active', 'failed')]
            if any(j.get('state') in ('queued', 'active') or j.get('editing') or w.queue_commands.active_job(j) for j in matches):
                continue
            if matches:
                job = matches[0]
                job.update(state='queued', held=True, scheduled_at=None, suppress_retry=False, retries=0, crc_attempt=0)
                if job not in w.pending:
                    w.pending.append(job)
                job['row'].setText(7, 'Queued from history · Held')
            else:
                item.update(state='queued', held=True, workspace=w.tabs.currentIndex(), retries=0, scheduled_at=None)
                was_paused = w.paused
                w.restore_queue_items([item])
                w.paused = was_paused
            queued += 1
        w.save_queue_state()
        w.show_transfer_queue()
        return queued

    def show(self):
        try:
            self.store.retain(*self.limits())
            dialog = HistoryDialog(self)
            try: dialog.exec()
            finally: dialog.deleteLater()
        except (OSError, sqlite3.Error, ValueError) as error:
            self.window.error(f'Cannot open transfer history: {error}')


class HistoryDialog(QDialog):
    def __init__(self, controller):
        super().__init__(controller.window)
        self.controller, self.offset = controller, 0
        self.setWindowTitle('Transfer History')
        self.resize(1100, 650)
        layout = QVBoxLayout(self)
        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText('Search source, destination or result details')
        self.filter = QComboBox()
        self.filter.addItems(['All', 'Complete', 'Skipped', 'Failed', 'Cancelled'])
        filters.addWidget(self.search)
        filters.addWidget(self.filter)
        layout.addLayout(filters)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(['Finished (local)', 'Result', 'Route', 'Bytes', 'Seconds', 'Source', 'Destination', 'Details'])
        self.tree.setRootIsDecorated(False)
        for column, width in enumerate((170, 75, 70, 80, 60, 220, 220)):
            self.tree.setColumnWidth(column, width)
        self.tree.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        layout.addWidget(self.tree)
        self.summary = QLabel()
        layout.addWidget(self.summary)
        controls = QHBoxLayout()
        self.previous = QPushButton('Previous')
        self.next = QPushButton('Next')
        for button, callback in ((self.previous, lambda: self.page(-100)), (self.next, lambda: self.page(100))):
            button.clicked.connect(callback)
            controls.addWidget(button)
        for label, callback in [('Refresh', self.refresh), ('Copy source paths', lambda: self.copy('src')),
                                ('Copy destination paths', lambda: self.copy('dst'))]:
            button = QPushButton(label)
            button.clicked.connect(callback)
            controls.addWidget(button)
        self.retry_button = QPushButton('Queue failed again')
        self.retry_button.clicked.connect(self.retry)
        controls.addWidget(self.retry_button)
        layout.addLayout(controls)
        reports = QHBoxLayout()
        for title, selected in (('Export selected verification report…', True), ('Export filtered verification report…', False)):
            button = QPushButton(title)
            button.clicked.connect(lambda checked=False, selected=selected: self.export(selected))
            reports.addWidget(button)
        layout.addLayout(reports)
        retention = QHBoxLayout()
        self.days, self.count = QSpinBox(), QSpinBox()
        self.days.setRange(1, 3650)
        self.count.setRange(100, 1000000)
        days, count = controller.limits()
        self.days.setValue(days)
        self.count.setValue(count)
        for label, widget in [('Keep days', self.days), ('Maximum records', self.count)]:
            retention.addWidget(QLabel(label))
            retention.addWidget(widget)
        for label, callback in [('Apply retention', self.retain), ('Clear history', self.clear), ('Close', self.accept)]:
            button = QPushButton(label)
            button.clicked.connect(callback)
            retention.addWidget(button)
        layout.addLayout(retention)
        self.delay = QTimer(self)
        self.delay.setSingleShot(True)
        self.delay.setInterval(250)
        self.delay.timeout.connect(self.reset)
        self.search.textChanged.connect(lambda: self.delay.start())
        self.filter.currentTextChanged.connect(self.reset)
        self.tree.itemSelectionChanged.connect(lambda: self.retry_button.setEnabled(any(r['outcome'] == 'failed' for r in self.selected())))
        self.refresh()

    def selected(self):
        return [row.data(0, Qt.ItemDataRole.UserRole) for row in self.tree.selectedItems()]

    def export(self, selection):
        ids = [row['id'] for row in self.selected()] if selection else None
        if selection and not ids:
            self.summary.setText('Select history rows to export, or choose Export filtered.')
            return
        path, chosen = QFileDialog.getSaveFileName(self, 'Export verification report', 'verification-report.json', 'JSON (*.json);;CSV (*.csv)')
        if not path: return
        if not Path(path).suffix: path += '.csv' if chosen.startswith('CSV') else '.json'
        from .verification_reports import export_report
        from .queue_io import background
        try:
            search, outcome = self.search.text(), self.filter.currentText()
            count = background(self.controller.window, 'Exporting verification report…',
                lambda worker: export_report(self.controller.store, path, search, outcome, ids, worker.cancel))
            self.summary.setText(f'Exported {count} file/result rows. Verification reflects checks actually performed.')
        except Exception as error:
            self.summary.setText('Report export failed: ' + type(error).__name__)

    def reset(self, *_):
        self.offset = 0
        self.refresh()

    def page(self, delta):
        self.offset = max(0, self.offset + delta)
        self.refresh()

    def refresh(self):
        try:
            records, total = self.controller.store.page(self.search.text(), self.filter.currentText(), self.offset)
            if total and self.offset >= total:
                self.offset = (total - 1) // 100 * 100
                records, total = self.controller.store.page(self.search.text(), self.filter.currentText(), self.offset)
            self.tree.clear()
            for record in records:
                row = QTreeWidgetItem([time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(record['finished'])),
                    record['outcome'].title(), record['route'], str(record['size']) if record['size'] is not None else '—',
                    f"{record['elapsed']:.2f}", record['src'], record['dst'], record['detail']])
                row.setData(0, Qt.ItemDataRole.UserRole, record)
                for column in (5, 6, 7): row.setToolTip(column, row.text(column))
                self.tree.addTopLevelItem(row)
            self.summary.setText(f'{self.offset + 1 if records else 0}–{self.offset + len(records)} of {total} records')
            self.previous.setEnabled(self.offset > 0)
            self.next.setEnabled(self.offset + len(records) < total)
            self.retry_button.setEnabled(False)
        except (OSError, sqlite3.Error) as error:
            self.summary.setText(f'Cannot read history: {error}')

    def copy(self, field):
        QApplication.clipboard().setText('\n'.join(record[field] for record in self.selected()))

    def retry(self):
        try:
            count = self.controller.retry(self.selected())
            self.summary.setText(f'{count} transfer(s) queued and held. Use Transfer Queue to start; credentials are requested when needed.')
        except (OSError, sqlite3.Error, ValueError, KeyError, TypeError) as error:
            self.summary.setText(f'Cannot queue history selection: {error}')

    def retain(self):
        try:
            self.controller.store.retain(self.days.value(), self.count.value())
            p = self.controller.window.preferences
            p.setValue('History/days', self.days.value())
            p.setValue('History/count', self.count.value())
            self.reset()
        except (OSError, sqlite3.Error) as error:
            self.summary.setText(f'Cannot apply retention: {error}')

    def clear(self):
        if QMessageBox.question(self, 'Clear transfer history', 'Delete all history records? Transferred files and queued transfers are kept.',
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                                QMessageBox.StandardButton.Cancel) == QMessageBox.StandardButton.Yes:
            try:
                self.controller.store.clear()
                self.reset()
            except (OSError, sqlite3.Error) as error:
                self.summary.setText(f'Cannot clear history: {error}')
