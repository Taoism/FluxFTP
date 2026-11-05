"""Filtered recursive remote search with browse and download actions."""
import fnmatch
import posixpath
import re
import time
from datetime import datetime

from PySide6.QtCore import Qt, QDate
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QCheckBox, QDateEdit, QTreeWidget, QTreeWidgetItem, QPushButton, QFormLayout,
    QMessageBox)

from .core import Entry


def size_bytes(value):
    value = value.strip()
    if not value:
        return None
    match = re.fullmatch(r'(\d+(?:\.\d+)?)\s*(B|KB|MB|GB|TB|KIB|MIB|GIB|TIB)?', value, re.I)
    if not match:
        raise ValueError(f'Invalid size “{value}”. Use a value such as 500 KB or 2 MB.')
    unit = (match.group(2) or 'B').upper()
    power = {'B': 0, 'KB': 1, 'MB': 2, 'GB': 3, 'TB': 4,
             'KIB': 1, 'MIB': 2, 'GIB': 3, 'TIB': 4}[unit]
    base = 1000 if unit.endswith('B') and not unit.endswith('IB') else 1024
    return int(float(match.group(1)) * base ** power)


def modified_epoch(value):
    value = str(value or '').strip()
    if not value:
        return None
    if re.fullmatch(r'\d{9,12}(?:\.\d+)?', value):
        try:
            return float(value)
        except (ValueError, OverflowError):
            return None
    formats = ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d',
               '%Y/%m/%d %H:%M:%S', '%m-%d-%Y %I:%M %p', '%m/%d/%Y %I:%M %p',
               '%b %d %Y', '%b %d %H:%M')
    for pattern in formats:
        try:
            parsed = datetime.strptime(value, pattern)
            if '%Y' not in pattern:
                parsed = parsed.replace(year=datetime.now().year)
            return parsed.timestamp()
        except (ValueError, OverflowError, OSError):
            pass
    return None


class RemoteSearchDialog(QDialog):
    def __init__(self, window, snapshot):
        super().__init__(window)
        self.window = window
        self.snapshot = snapshot
        self.setWindowTitle('Search Remote Files')
        self.resize(960, 600)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f'Search below {snapshot[3]} on {snapshot[1].name}.'))
        form = QFormLayout()
        self.pattern = QLineEdit('*')
        self.pattern.setPlaceholderText('Filename masks, separated by semicolons')
        form.addRow('Filename masks:', self.pattern)
        sizes = QHBoxLayout()
        self.minimum = QLineEdit()
        self.minimum.setPlaceholderText('Any')
        self.maximum = QLineEdit()
        self.maximum.setPlaceholderText('Any')
        sizes.addWidget(QLabel('At least'))
        sizes.addWidget(self.minimum)
        sizes.addWidget(QLabel('At most'))
        sizes.addWidget(self.maximum)
        form.addRow('File size:', sizes)
        dates = QHBoxLayout()
        self.use_dates = QCheckBox('Limit modification date')
        self.from_date = QDateEdit(QDate.currentDate().addMonths(-1))
        self.from_date.setCalendarPopup(True)
        self.to_date = QDateEdit(QDate.currentDate())
        self.to_date.setCalendarPopup(True)
        dates.addWidget(self.use_dates)
        dates.addWidget(QLabel('From'))
        dates.addWidget(self.from_date)
        dates.addWidget(QLabel('through'))
        dates.addWidget(self.to_date)
        dates.addStretch(1)
        form.addRow('Modified:', dates)
        layout.addLayout(form)
        self.results = QTreeWidget()
        self.results.setHeaderLabels(['Name', 'Size', 'Modified', 'Remote path'])
        self.results.setRootIsDecorated(False)
        self.results.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        self.results.header().setStretchLastSection(True)
        layout.addWidget(self.results, 1)
        self.status = QLabel('Enter filters and select Search. Symbolic links are skipped; search is limited to 100,000 entries.')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        self.search_button = QPushButton('Search')
        self.open_button = QPushButton('Open Containing Folder')
        self.download_button = QPushButton('Download to Other Pane')
        self.close_button = QPushButton('Close')
        for button in (self.search_button, self.open_button, self.download_button, self.close_button):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.search_button.clicked.connect(self.start_search)
        self.open_button.clicked.connect(self.open_folder)
        self.download_button.clicked.connect(self.download)
        self.close_button.clicked.connect(self.close)
        self.results.itemDoubleClicked.connect(lambda *_: self.open_folder())
        self.results.itemSelectionChanged.connect(self.update_actions)
        self.use_dates.toggled.connect(lambda enabled: (self.from_date.setEnabled(enabled), self.to_date.setEnabled(enabled)))
        self.use_dates.setChecked(False)
        self.update_actions()

    def start_search(self):
        from .remote_tasks import scan_directory
        try:
            masks = [item.strip().casefold() for item in self.pattern.text().split(';') if item.strip()]
            if not masks:
                raise ValueError('Enter one or more filename masks.')
            lower, upper = size_bytes(self.minimum.text()), size_bytes(self.maximum.text())
            if lower is not None and upper is not None and lower > upper:
                raise ValueError('Minimum file size cannot exceed maximum file size.')
        except ValueError as error:
            self.window.error(str(error))
            return
        self.results.clear()
        self.search_button.setEnabled(False)
        self.status.setText('Searching remote folders…')
        start_date = datetime.combine(self.from_date.date().toPython(), datetime.min.time()).timestamp() if self.use_dates.isChecked() else None
        end_date = datetime.combine(self.to_date.date().addDays(1).toPython(), datetime.min.time()).timestamp() if self.use_dates.isChecked() else None
        def task(connection, cancel):
            found = []
            for path, entry in scan_directory(connection, self.snapshot[3], cancel):
                if entry.is_dir or entry.is_link or not any(fnmatch.fnmatchcase(entry.name.casefold(), mask) for mask in masks):
                    continue
                if lower is not None and entry.size < lower or upper is not None and entry.size > upper:
                    continue
                if start_date is not None or end_date is not None:
                    stamp = modified_epoch(entry.modified)
                    if stamp is None or start_date is not None and stamp < start_date or end_date is not None and stamp >= end_date:
                        continue
                found.append((path, entry))
            return found
        def done(records):
            for path, entry in records[:10000]:
                row = QTreeWidgetItem([entry.name, self.window.file_size(entry.size), entry.modified, path])
                row.setData(0, Qt.ItemDataRole.UserRole, (path, entry))
                self.results.addTopLevelItem(row)
            self.status.setText(f'{len(records):,} matching file(s). Double-click a row to open its containing folder.')
            self.search_button.setEnabled(True)
            self.update_actions()
        def failed(error):
            self.status.setText(f'Search failed: {error}')
            self.search_button.setEnabled(True)
        self.window.ftp_commands.run(self.snapshot, 'Search Remote Files', task, done, failure=failed)
        # FTPMenu.run restores the pane on completion; keep a reference to avoid closing this window.
        self.window.retain_mouse_window(self)

    def selected(self):
        row = self.results.currentItem()
        return row.data(0, Qt.ItemDataRole.UserRole) if row else None

    def update_actions(self):
        selected = self.selected() is not None
        self.open_button.setEnabled(selected)
        other = self.window.current_panes()[1 if self.snapshot[0].number == '1' else 0]
        self.download_button.setEnabled(selected and not other.busy and (other.mode == 'Local' or other.site is not None))

    def open_folder(self):
        result = self.selected()
        if not result:
            return
        path, _entry = result
        self.snapshot[0].navigate(posixpath.dirname(path) or '/')

    def download(self):
        result = self.selected()
        if not result:
            return
        source_pane = self.snapshot[0]
        dest = self.window.current_panes()[1 if source_pane.number == '1' else 0]
        if source_pane.site is not self.snapshot[1] or source_pane.password != self.snapshot[2]:
            QMessageBox.warning(self, 'Search expired', 'The source connection changed. Run the search again before downloading.')
            return
        path, entry = result
        source = type('RemoteSearchSource', (), {'mode': 'Remote', 'site': self.snapshot[1], 'password': self.snapshot[2],
            'busy': False, 'join': lambda _self, _name: path})()
        self.window.enqueue_items(source, dest, [Entry(entry.name, False, entry.size, entry.modified)], confirmed=True)
        self.status.setText(f'Queued download: {path}')

