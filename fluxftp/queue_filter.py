"""Queue view filters and bounded background search through disk buffers."""
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import (QWidget, QHBoxLayout, QLineEdit, QComboBox, QPushButton,
    QLabel, QDialog, QVBoxLayout, QTreeWidget, QTreeWidgetItem, QHeaderView)
from .queue_tree import rows, STATE_ROLE


def matches(item, query, state):
    actual = 'held' if item.get('state', 'queued') == 'queued' and item.get('held') else item.get('state', 'queued')
    return (state == 'All' or actual == state.lower()) and query.casefold() in ' '.join(
        str(item.get(key, '')) for key in ('name', 'src', 'dst')).casefold()


def search_page(items, buffers, query, state, offset, cancel, limit=200):
    result = []
    seen = 0
    def candidates():
        yield from items
        for path, overrides in buffers:
            from contextlib import nullcontext
            context = nullcontext(path.db) if hasattr(path, 'db') else closing(sqlite3.connect(Path(path).as_uri() + '?mode=ro', uri=True))
            with context as db:
                for payload, in db.execute('SELECT payload FROM items ORDER BY priority,position,id'):
                    item = json.loads(payload)
                    item.update(overrides)
                    yield item
    try:
        with closing(candidates()) as stream:
            for item in stream:
                if cancel.is_set():
                    return [], False
                if matches(item, query, state):
                    if seen >= offset:
                        result.append(item)
                        if len(result) > limit:
                            return result[:limit], True
                    seen += 1
    finally:
        for reader, _ in buffers:
            if hasattr(reader, 'close'): reader.close()
    return result, False


class QueueSearch(QDialog):
    def __init__(self, owner):
        super().__init__(owner.window)
        self.owner = owner
        self.offset = 0
        self.worker = None
        self.setWindowTitle('Search all queued files')
        self.resize(950, 450)
        layout = QVBoxLayout(self)
        self.label = QLabel('Includes files buffered on disk. Results are read-only; refresh to include queue changes.')
        layout.addWidget(self.label)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(['Name', 'Source path', 'Target path', 'Status'])
        self.tree.setRootIsDecorated(False)
        self.tree.header().setStretchLastSection(False)
        for column in (0, 1, 2):
            self.tree.header().setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.tree)
        controls = QHBoxLayout()
        self.previous = QPushButton('Previous')
        self.next = QPushButton('Next')
        self.refresh = QPushButton('Refresh')
        close = QPushButton('Close')
        for button in (self.previous, self.next, self.refresh, close): controls.addWidget(button)
        layout.addLayout(controls)
        self.previous.clicked.connect(lambda: self.load(max(0, self.offset - 200)))
        self.next.clicked.connect(lambda: self.load(self.offset + 200))
        self.refresh.clicked.connect(lambda: self.load(0))
        close.clicked.connect(self.close)
        self.load(0)

    def load(self, offset):
        self.offset = offset
        window = self.owner.window
        items, buffers = [], []
        for job in window.queue_jobs:
            if job.get('buffered') and job.get('state') == 'queued':
                overrides = {}
                if job.get('held'): overrides['held'] = True
                elif job.get('buffer_release'): overrides['held'] = False
                buffers.append((job['buffer'].open_reader(), overrides))
            elif not job.get('buffered'):
                items.append({k: job.get(k) for k in ('name', 'src', 'dst', 'state', 'held')})
        query, state = self.owner.query.text(), self.owner.status.currentText()
        for button in (self.previous, self.next, self.refresh): button.setEnabled(False)
        self.label.setText('Searching…')
        self.worker = window.run(lambda worker: search_page(items, buffers, query, state, offset, worker.cancel),
                                 self.loaded, self.failed)

    def loaded(self, result):
        self.worker = None
        items, more = result
        self.tree.clear()
        for item in items:
            row = QTreeWidgetItem(self.tree, [str(item.get(k) or '') for k in ('name', 'src', 'dst')] +
                ['Held' if item.get('held') and item.get('state') == 'queued' else item.get('state', 'queued')])
            for column in (0, 1, 2): row.setToolTip(column, row.text(column))
        self.label.setText(f'Results {self.offset + 1 if items else 0}–{self.offset + len(items)} · Includes buffered files · Read-only')
        self.previous.setEnabled(self.offset > 0)
        self.next.setEnabled(more)
        self.refresh.setEnabled(True)

    def failed(self, message):
        self.worker = None
        self.label.setText('Queue changed or search failed. Refresh to retry: ' + str(message))
        self.refresh.setEnabled(True)

    def closeEvent(self, event):
        if self.worker: self.worker.cancel.set()
        super().closeEvent(event)


class QueueFilter(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        bar = QWidget()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(2, 0, 2, 0)
        self.query = QLineEdit()
        self.query.setAccessibleName('Transfer queue filename or path filter')
        self.query.setPlaceholderText('Filter loaded queue rows by name or path…')
        self.query.setClearButtonEnabled(True)
        self.status = QComboBox()
        self.status.addItems(['All', 'Queued', 'Held', 'Active', 'Failed', 'Complete', 'Skipped', 'Cancelled'])
        self.status.setAccessibleName('Queue status filter')
        all_files = QPushButton('Search all files…')
        all_files.setToolTip('Search the whole queue, including pending files buffered on disk')
        all_files.clicked.connect(self.search)
        for widget in (self.query, self.status, all_files): layout.addWidget(widget)
        window.queue_panel.rows.insertWidget(2, bar)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(180)
        self.timer.timeout.connect(self.apply)
        self.query.textChanged.connect(self.schedule)
        self.status.currentTextChanged.connect(self.schedule)
        window.queue.model().dataChanged.connect(self.schedule)
        window.queue.model().rowsInserted.connect(self.schedule)
        self.dialogs = []

    def schedule(self, *_):
        if not self.timer.isActive(): self.timer.start()

    def apply(self):
        query, state = self.query.text(), self.status.currentText()
        jobs = {id(job['row']): job for job in self.window.queue_jobs}
        all_rows = list(rows(self.window.queue))
        for row in reversed(all_rows):
            item = jobs.get(id(row), dict(name=row.text(0), src=row.text(5), dst=row.text(6),
                                        state=row.data(0, STATE_ROLE) or 'queued'))
            visible = matches(item, query, state) or any(not row.child(i).isHidden() for i in range(row.childCount()))
            row.setHidden(not visible)
            if not visible: row.setSelected(False)

    def search(self):
        dialog = QueueSearch(self)
        self.dialogs.append(dialog)
        dialog.show()
