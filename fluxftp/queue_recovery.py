"""Preview and recover unfinished queues without starting transfers implicitly."""
from datetime import datetime
import sqlite3

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QVBoxLayout, QLabel,
    QTreeWidget, QTreeWidgetItem, QListWidget, QListWidgetItem, QPushButton,
    QMessageBox, QStyle)

from .queue_io import background, persist, checked
from .core import Cancelled


class LoadQueueDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.setWindowTitle('Load Queue')
        self.resize(820, 490)
        self.setMinimumSize(610, 350)
        outer = QVBoxLayout(self)
        body = QHBoxLayout()
        left = QVBoxLayout()
        self.heading = QLabel('Queue content')
        left.addWidget(self.heading)
        self.preview = QTreeWidget()
        self.preview.setHeaderLabels(['Name', 'Size', 'Source path', 'Target path', 'Remark'])
        self.preview.setSelectionMode(QTreeWidget.SelectionMode.NoSelection)
        self.preview.setColumnWidth(0, 170)
        self.preview.setColumnWidth(1, 80)
        self.preview.setColumnWidth(2, 220)
        self.preview.setColumnWidth(3, 220)
        left.addWidget(self.preview, 1)
        body.addLayout(left, 1)
        right = QVBoxLayout()
        right.addWidget(QLabel('Queue files'))
        self.files = QListWidget()
        self.files.setMaximumWidth(230)
        right.addWidget(self.files, 1)
        self.buttons = {}
        for title, callback in (
            ('Load', lambda: self.load(False)),
            ('Load to New Window', lambda: self.load(False, new_window=True)),
            ('Load All', lambda: self.load(True)),
            ('Start All', lambda: self.load(True, start=True)),
            ('Delete', lambda: self.delete(False)),
            ('Delete All', lambda: self.delete(True)), ('Close', self.reject)):
            button = QPushButton(title)
            button.clicked.connect(callback)
            self.buttons[title] = button
            right.addWidget(button)
        body.addLayout(right)
        outer.addLayout(body, 1)
        self.status = QLabel('Load restores a paused queue. Start All begins transfers. Delete removes queue entries only.')
        self.status.setWordWrap(True)
        outer.addWidget(self.status)
        self.files.currentItemChanged.connect(self.refresh_preview)
        self.refresh()

    def refresh(self):
        current = self.files.currentItem()
        workspace = current.data(Qt.ItemDataRole.UserRole) if current else None
        self.files.clear()
        saved = self.window.deferred_queue
        for index in sorted({job['workspace'] for job in saved}):
            item = QListWidgetItem(f'{self.window.queue_file.name} (workspace {index + 1})')
            item.setData(Qt.ItemDataRole.UserRole, index)
            self.files.addItem(item)
            if index == workspace:
                self.files.setCurrentItem(item)
        if self.files.currentRow() < 0 and self.files.count():
            self.files.setCurrentRow(0)
        for title, button in self.buttons.items():
            if title != 'Close':
                button.setEnabled(bool(saved) and not self.window.active)
        self.refresh_preview()

    def selected_jobs(self, all_jobs=False):
        item = self.files.currentItem()
        workspace = item.data(Qt.ItemDataRole.UserRole) if item else None
        return [job for job in self.window.deferred_queue if all_jobs or job['workspace'] == workspace]

    def refresh_preview(self, *_):
        self.preview.clear()
        try:
            stamp = datetime.fromtimestamp(self.window.queue_file.stat().st_mtime).strftime('%m/%d/%Y %H:%M:%S')
            self.heading.setText(f'Queue content (Datetime: {stamp})')
        except OSError:
            self.heading.setText('Queue content')
        groups = {}
        selected = self.selected_jobs()
        for job in selected[:256]:
            source, target = job['source_site'], job['destination_site']
            route = 'Remote → Remote' if source and target else 'Download' if source else 'Upload' if target else 'Local copy'
            size = self.window.file_size(job['size']) if job.get('size') is not None else '—'
            remark = route + (' · Failed' if job['state'] == 'failed' else ' · Unfinished')
            row = QTreeWidgetItem([job['name'], size, job['src'], job['dst'], remark])
            row.setIcon(0, self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon if job['directory'] else QStyle.StandardPixmap.SP_FileIcon))
            group = job.get('queue_group')
            if group:
                if group['id'] not in groups:
                    parent = QTreeWidgetItem([group['name'], '—', group['src'], group['dst'], route])
                    parent.setIcon(0, self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon))
                    self.preview.addTopLevelItem(parent)
                    groups[group['id']] = parent
                groups[group['id']].addChild(row)
            else:
                self.preview.addTopLevelItem(row)
        self.preview.expandAll()
        if len(selected) > 256:
            self.heading.setText(self.heading.text() + f' · showing 256 of {len(selected):,} files')

    def load(self, all_jobs=False, new_window=False, start=False):
        w = self.window
        selected = self.selected_jobs(all_jobs)
        if not selected or w.active:
            return
        if new_window and w.tabs.count() >= 100:
            self.status.setText('Close a workspace before loading into a new one (100-workspace limit).')
            return
        previous = list(w.deferred_queue)
        existing = {id(job) for job in w.queue_jobs}
        w._queue_commit = True
        try:
            # Restore copies each live job; buffered jobs are serialized by the
            # preparation worker. Avoid duplicating a complete 100k-item graph.
            items = selected
            if new_window:
                w.add_workspace()
                index = w.tabs.currentIndex()
                def copy_items(worker):
                    result = []
                    for item in selected:
                        checked(worker.cancel)
                        result.append(dict(item, workspace=index))
                    return result
                items = background(w, 'Preparing saved queue…', copy_items) if len(selected) > 256 else [dict(item, workspace=index) for item in selected]
            w.restore_queue_items(items)
            identities = {id(item) for item in selected}
            w.deferred_queue = [item for item in previous if id(item) not in identities]
            # Explicit recovery choices persist even if automatic saving is disabled.
            persist(w)
        except (OSError, ValueError, TypeError, Cancelled, sqlite3.Error) as error:
            w.discard_queue_jobs([job for job in w.queue_jobs if id(job) not in existing])
            w.deferred_queue = previous
            self.status.setText(f'Could not load saved queue: {error}')
            return
        finally:
            w._queue_commit = False
        self.refresh()
        if start:
            self.accept()
            QTimer.singleShot(0, w.play_queue)
        elif not w.deferred_queue:
            self.accept()
        else:
            self.status.setText(f'Loaded {len(selected)} item(s), paused.')

    def delete(self, all_jobs=False):
        w = self.window
        selected = self.selected_jobs(all_jobs)
        if not selected or w.active:
            return
        if QMessageBox.question(self, 'Delete saved queue',
                f'Remove {len(selected)} saved queue item(s)? Source files and downloaded files will be kept.') != QMessageBox.StandardButton.Yes:
            return
        previous = list(w.deferred_queue)
        identities = {id(item) for item in selected}
        w.deferred_queue = [item for item in previous if id(item) not in identities]
        try:
            persist(w)
        except OSError as error:
            w.deferred_queue = previous
            self.status.setText(f'Could not remove saved queue: {error}')
            return
        self.refresh()

    def reject(self):
        if self.window.deferred_queue:
            self.window.statusBar().showMessage('Saved queue kept. Open Tools → Queue → Load Queue from file to review it.', 15000)
        super().reject()
