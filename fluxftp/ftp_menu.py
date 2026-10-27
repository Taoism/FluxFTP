"""FTP menu commands and reserved entries for reference-driven dialogs."""
import fnmatch
import os
import posixpath
import re
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Event

from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (QStyle, QInputDialog, QMessageBox, QFileDialog,
    QProgressDialog, QDialog, QVBoxLayout, QPlainTextEdit, QDialogButtonBox)
from .core import Cancelled
from .remote_tasks import scan_directory, move_items


class FTPMenu(QObject):
    def __init__(self, window, menus):
        super().__init__(window)
        self.window = window
        self.menu = menus.addMenu('FTP')
        self.menu.setStyleSheet('QMenu::item:disabled { color: #9ba2aa; }')
        self.actions = {}
        self.add('Quick Connect Dialog', 'F11', self.quick_dialog)
        self.menu.addSeparator()
        self.add('Attributes(CHMOD)', 'Ctrl+O', self.chmod)
        self.actions['Attributes(CHMOD)'].setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
        self.add('Move to…', callback=self.move)
        self.add('Modify Date of FTP files…', callback=self.modify_date)
        self.menu.addSeparator()
        self.add('Calculate FTP Space Used…', callback=lambda: self.scan('space'))
        self.add('Export Directory Tree List…', callback=lambda: self.scan('export'))
        self.menu.addSeparator()
        find = self.add('Find Files at Site…', callback=lambda: self.scan('find'))
        find.setIcon(window.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogContentsView))
        self.menu.addSeparator()
        self.add('Clean Current Site Cache', callback=lambda: self.clean_cache(False))
        self.add('Clean All Sites Cache', callback=lambda: self.clean_cache(True))
        self.menu.aboutToShow.connect(self.update)

    def pane(self):
        return self.window.mark_commands.pane()

    def snapshot(self, selected=False):
        pane = self.pane()
        if pane is None or pane.busy or pane.mode != 'Remote' or pane.site is None:
            return None
        entries = pane.selected()
        if selected and (not entries or any(entry.is_link for entry in entries)):
            return None
        return (pane, pane.site, pane.password, pane.path, pane.generation, entries)

    def valid(self, snapshot):
        pane, site, password, path, generation, entries = snapshot
        return (self.pane() is pane and not pane.busy and pane.site is site and pane.password == password
                and pane.path == path and pane.generation == generation and pane.mode == 'Remote')

    def update(self, *_):
        ready = self.snapshot()
        selected = self.snapshot(True)
        for label in ('Attributes(CHMOD)', 'Move to…', 'Modify Date of FTP files…'):
            self.actions[label].setEnabled(bool(selected))
        self.actions['Modify Date of FTP files…'].setEnabled(bool(selected) and not any(entry.is_dir for entry in selected[5]))
        from .cloud import CLOUD_PROTOCOLS
        if ready and ready[1].protocol in CLOUD_PROTOCOLS:
            self.actions['Attributes(CHMOD)'].setEnabled(False)
            self.actions['Modify Date of FTP files…'].setEnabled(False)
        for label in ('Calculate FTP Space Used…', 'Export Directory Tree List…', 'Find Files at Site…'):
            self.actions[label].setEnabled(bool(ready))
        pane = self.pane()
        self.actions['Clean Current Site Cache'].setEnabled(bool(pane and pane.mode == 'Remote' and pane.site))

    def clean_cache(self, all_sites):
        pane = self.pane()
        if not all_sites and (pane is None or pane.mode != 'Remote' or pane.site is None):
            return
        count = self.window.directory_cache.clear(None if all_sites else pane.site)
        self.window.statusBar().showMessage(f'Cleared {count} cached directory listing(s). F5 refreshes the displayed listing.', 8000)

    def confirm(self, snapshot, title, message):
        return (QMessageBox.question(self.window, title, message,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel) == QMessageBox.StandardButton.Yes and self.valid(snapshot))

    def run(self, snapshot, title, task, finished, mutation=False):
        if not self.valid(snapshot):
            return
        pane, site, password, path, generation, entries = snapshot
        if mutation:
            self.window.directory_cache.clear(site)
        pane.busy = True
        self.update()
        progress = QProgressDialog(title, 'Cancel', 0, 0, self.window)
        progress.setWindowTitle(title)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setWindowModality(Qt.WindowModality.NonModal)
        cancellation = Event()
        def work(worker):
            if cancellation.is_set():
                raise Cancelled('Cancelled')
            with self.window.new_connection(site, password) as connection:
                connection.trace.display_side = int(pane.number)
                if cancellation.is_set():
                    raise Cancelled('Cancelled')
                return task(connection, cancellation)
        def cleanup():
            pane.busy = False
            progress.close()
            progress.deleteLater()
            if mutation:
                self.window.directory_cache.clear(site)
                pane.refresh()
            self.update()
        def done(result):
            cleanup()
            finished(result)
        def failed(message):
            cleanup()
            self.window.error(str(message))
        worker = self.window.run(work, done, failed, autostart=False)
        progress.canceled.connect(cancellation.set)
        progress.show()
        worker.start()

    def chmod(self):
        snapshot = self.snapshot(True)
        if snapshot is None:
            return
        value, accepted = QInputDialog.getText(self.window, 'Attributes (CHMOD)',
            'Octal permissions (0000–7777); applies only to selected items:')
        if not accepted:
            return
        if not re.fullmatch(r'[0-7]{3,4}', value.strip()):
            self.window.error('Enter three or four octal digits, such as 644 or 0755.')
            return
        mode = int(value, 8)
        if not self.confirm(snapshot, 'Change permissions', f'Apply {mode:04o} to {len(snapshot[5])} selected item(s)?'):
            return
        self.mutate_selected(snapshot, 'Changing permissions', lambda connection, path: connection.chmod(path, mode))

    def modify_date(self):
        snapshot = self.snapshot(True)
        if snapshot is None:
            return
        if any(entry.is_dir for entry in snapshot[5]):
            self.window.error('Select files only to modify their dates.')
            return
        value, accepted = QInputDialog.getText(self.window, 'Modify Date of FTP files',
            'New modification time in UTC (YYYY-MM-DD HH:MM:SS):',
            text=datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'))
        if not accepted:
            return
        try:
            stamp = datetime.strptime(value.strip(), '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc).timestamp()
        except (ValueError, OverflowError):
            self.window.error('Enter a valid UTC date and time in YYYY-MM-DD HH:MM:SS format.')
            return
        if self.confirm(snapshot, 'Modify file dates', f'Set {len(snapshot[5])} file(s) to {value.strip()} UTC?'):
            self.mutate_selected(snapshot, 'Changing modification times', lambda connection, path: connection.set_modified(path, stamp))

    def mutate_selected(self, snapshot, title, operation):
        def task(connection, cancel):
            count = 0
            for entry in snapshot[5]:
                if cancel.is_set():
                    raise Cancelled(f'Cancelled after {count} item(s).')
                try:
                    operation(connection, posixpath.join(snapshot[3], entry.name))
                except Exception as error:
                    raise RuntimeError(f'{count} item(s) changed before failure: {error}') from error
                count += 1
            return count
        self.run(snapshot, title, task, lambda count: self.window.statusBar().showMessage(f'Updated {count} item(s)', 8000), True)

    def move(self):
        snapshot = self.snapshot(True)
        if snapshot is None:
            return
        destination, accepted = QInputDialog.getText(self.window, 'Move to', 'Existing absolute directory on the same server:')
        if not accepted:
            return
        if not destination.startswith('/') or any(ch in destination for ch in ('\r', '\n', '\x00')):
            self.window.error('Enter an absolute remote directory path.')
            return
        destination = posixpath.normpath(destination)
        if self.confirm(snapshot, 'Move remote items', f'Move {len(snapshot[5])} item(s) to {destination}?\nExisting destination names will be refused.'):
            self.run(snapshot, 'Moving items', lambda connection, cancel: move_items(connection, snapshot[3], snapshot[5], destination, cancel),
                     lambda count: self.window.statusBar().showMessage(f'Moved {count} item(s)', 8000), True)

    def report(self, title, text):
        dialog = QDialog(self.window)
        dialog.setWindowTitle(title)
        dialog.resize(850, 550)
        layout = QVBoxLayout(dialog)
        content = QPlainTextEdit()
        content.setReadOnly(True)
        content.setPlainText(text)
        layout.addWidget(content)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.close)
        layout.addWidget(buttons)
        self.window.retain_mouse_window(dialog)

    def scan(self, kind):
        snapshot = self.snapshot()
        if snapshot is None:
            return
        mask, output = '*', None
        if kind == 'find':
            mask, accepted = QInputDialog.getText(self.window, 'Find Files at Site',
                f'Filename masks (semicolon-separated), recursively below {snapshot[3]}:', text='*')
            if not accepted or not mask.strip():
                return
        elif kind == 'export':
            output, _ = QFileDialog.getSaveFileName(self.window, 'Export Directory Tree List', 'directory-tree.txt', 'Text files (*.txt)')
            if not output:
                return
        title = {'space': 'Calculate FTP Space Used', 'export': 'Export Directory Tree List', 'find': 'Find Files at Site'}[kind]
        def finished(records):
            # A failed or cancelled scan never exports an incomplete listing.
            if kind == 'space':
                files = [entry for _, entry in records if not entry.is_dir and not entry.is_link]
                self.report(title, f'{snapshot[3]}\n\n{sum(entry.size for entry in files):,} bytes\n{len(files):,} files\n'
                    f'{sum(entry.is_dir and not entry.is_link for _, entry in records):,} directories\n'
                    'Symbolic links are not followed or counted as file contents. Sizes are those reported by the server.')
            elif kind == 'find':
                masks = [part.strip().casefold() for part in mask.split(';') if part.strip()]
                matches = [path for path, entry in records if not entry.is_dir and any(
                    pattern in ('*', '*.*') or fnmatch.fnmatchcase(entry.name.casefold(), pattern) for pattern in masks)]
                self.report(title, f'{len(matches):,} matching files below {snapshot[3]}\n\n' + '\n'.join(matches))
            else:
                temporary = None
                try:
                    with NamedTemporaryFile('w', encoding='utf-8', dir=Path(output).parent, delete=False) as stream:
                        temporary = Path(stream.name)
                        stream.write(snapshot[3] + '\n')
                        for path, entry in sorted(records, key=lambda item: item[0]):
                            relative = posixpath.relpath(path, snapshot[3])
                            depth = relative.count('/')
                            stream.write('  ' * (depth + 1) + posixpath.basename(path) +
                                (' [link]' if entry.is_link else '/' if entry.is_dir else f' ({entry.size} bytes)') + '\n')
                    os.replace(temporary, output)
                    temporary = None
                    self.window.statusBar().showMessage(f'Exported {len(records):,} entries to {output}', 10000)
                except OSError as error:
                    self.window.error(str(error))
                finally:
                    if temporary is not None:
                        temporary.unlink(missing_ok=True)
        self.run(snapshot, title, lambda connection, cancel: scan_directory(connection, snapshot[3], cancel), finished)

    def quick_dialog(self):
        pane = self.window.mark_commands.pane()
        if pane is not None:
            pane.connect_site()

    def add(self, label, shortcut='', callback=None):
        action = QAction(label, self)
        if shortcut:
            action.setShortcut(shortcut)
        if callback is None:
            action.setEnabled(False)
        else:
            action.triggered.connect(lambda checked=False: callback())
        self.menu.addAction(action)
        self.actions[label] = action
        return action
