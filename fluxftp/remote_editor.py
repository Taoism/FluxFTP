"""Bounded UTF-8 remote text viewing and explicit save-back editing."""
from dataclasses import replace
from .site_connections import transfer_connection
from pathlib import Path
from tempfile import TemporaryDirectory

from PySide6.QtWidgets import (QDialog, QVBoxLayout, QPlainTextEdit, QLabel,
    QDialogButtonBox, QMessageBox)

from .core import Cancelled
from .transfer_options import TransferOptions

MAX_TEXT_BYTES = 8 * 1024 * 1024


def text_payload(data):
    if len(data) > MAX_TEXT_BYTES:
        raise ValueError('The text viewer supports files up to 8 MB.')
    if b'\0' in data:
        raise ValueError('This file is binary; download it to view with an appropriate application.')
    try:
        text = data.decode('utf-8-sig')
    except UnicodeDecodeError as error:
        raise ValueError('The text viewer supports UTF-8 files. Download this file to open it in another application.') from error
    newline = '\r\n' if '\r\n' in text else '\r' if '\r' in text else '\n'
    return text.replace('\r\n', '\n').replace('\r', '\n'), newline, data.startswith(b'\xef\xbb\xbf')


def fetch_text(window, site, password, path, options, worker, side):
    with TemporaryDirectory(prefix='fluxftp-view-') as folder:
        local = Path(folder) / 'text'
        with transfer_connection(window, site, password, 'download') as connection:
            connection.transfer_options = options
            connection.trace.display_side = side
            size = connection.size(path)
            if size is not None and size > MAX_TEXT_BYTES:
                raise ValueError('The text viewer supports files up to 8 MB.')
            def progress(done, total):
                if done > MAX_TEXT_BYTES or total > MAX_TEXT_BYTES:
                    raise ValueError('The text viewer supports files up to 8 MB.')
                worker.report(done, total)
            connection.transfer(local, path, False, worker.cancel, progress, False)
        data = local.read_bytes()
        text_payload(data)
        return data


class RemoteTextDialog(QDialog):
    def __init__(self, window, site, password, path, data, options, side, editable):
        super().__init__(window)
        self.owner, self.site, self.password, self.path = window, site, password, path
        self.options, self.side, self.original = options, side, data
        self.saving = False
        self.editable = editable
        text, self.newline, self.bom = text_payload(data)
        self.setWindowTitle(('Edit File — ' if editable else 'View File — ') + path)
        self.resize(900, 650)
        rows = QVBoxLayout(self)
        rows.addWidget(QLabel(f'{site.name}: {path}'))
        self.editor = QPlainTextEdit()
        self.editor.setPlainText(text)
        self.editor.setReadOnly(not editable)
        self.editor.document().setModified(False)
        rows.addWidget(self.editor)
        self.status = QLabel('UTF-8 text · Save uploads to the original remote path.' if editable else 'Read-only UTF-8 text')
        rows.addWidget(self.status)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        if editable:
            self.save_button = self.buttons.addButton(QDialogButtonBox.StandardButton.Save)
            self.save_button.clicked.connect(self.save_remote)
        self.buttons.rejected.connect(self.close)
        rows.addWidget(self.buttons)

    def save_remote(self):
        if self.saving or not self.editor.document().isModified():
            return
        data = self.editor.toPlainText().replace('\n', self.newline).encode('utf-8')
        if self.bom:
            data = b'\xef\xbb\xbf' + data
        if len(data) > MAX_TEXT_BYTES:
            self.status.setText('The editor supports files up to 8 MB.')
            return
        self.saving = True
        self.owner.directory_cache.clear(self.site)
        self.editor.setReadOnly(True)
        self.buttons.setEnabled(False)
        self.status.setText('Checking remote file and saving…')
        def task(worker):
            current = fetch_text(self.owner, self.site, self.password, self.path, self.options, worker, self.side)
            if current != self.original:
                raise ValueError('The remote file changed since it was opened. Reopen it before saving; your edits remain here.')
            if worker.cancel.is_set():
                raise Cancelled('Cancelled')
            with TemporaryDirectory(prefix='fluxftp-edit-') as folder:
                local = Path(folder) / 'text'
                local.write_bytes(data)
                with transfer_connection(self.owner, self.site, self.password, 'upload') as connection:
                    connection.transfer_options = self.options
                    connection.trace.display_side = self.side
                    connection.transfer(local, self.path, True, worker.cancel, worker.report, False)
        def finish(error=None):
            self.owner.directory_cache.clear(self.site)
            self.saving = False
            self.editor.setReadOnly(False)
            self.buttons.setEnabled(True)
            if error is None:
                self.original = data
                self.editor.document().setModified(False)
                self.status.setText('Saved to remote server.')
            else:
                self.status.setText(str(error))
        self.owner.run(task, lambda _: finish(), finish)

    def closeEvent(self, event):
        if self.saving:
            event.ignore()
            return
        if self.editor.document().isModified() and QMessageBox.question(self, 'Unsaved edits',
                'Discard the unsaved edits?', QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Discard:
            event.ignore()
            return
        event.accept()

    def reject(self):
        self.close()


def open_remote_text(window, pane, entry, editable):
    if pane.busy or pane.site is None:
        return
    if entry.is_link or entry.is_dir:
        window.error('The text viewer requires a regular file.')
        return
    site, password, path, side = pane.site, pane.password, pane.join(entry.name), int(pane.number)
    try:
        options = replace(TransferOptions.from_settings(window.preferences), mode='Binary', crc_auto=False,
                          keep_file_time=False, stall_resume=False, exists_enabled=False)
    except ValueError as error:
        window.error(str(error))
        return
    pane.busy = True
    def done(data):
        pane.busy = False
        dialog = RemoteTextDialog(window, site, password, path, data, options, side, editable)
        window.retain_mouse_window(dialog)
    def failed(error):
        pane.busy = False
        window.error(str(error))
    window.run(lambda worker: fetch_text(window, site, password, path, options, worker, side), done, failed)
