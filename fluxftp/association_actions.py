"""External applications receive an argument vector, never a shell command."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
from dataclasses import replace

from PySide6.QtWidgets import (QDialog, QVBoxLayout, QLabel, QLineEdit,
    QHBoxLayout, QPushButton, QMessageBox)

from .file_associations import matching_rule
from .transfer_options import TransferOptions
from .core import safe_name, Cancelled
from .site_connections import transfer_connection


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.digest()


def launch_application(program, path):
    if not Path(program).is_file():
        raise ValueError('The associated application no longer exists. Update File Associations.')
    if os.name == 'nt' and Path(program).suffix.casefold() not in ('.exe', '.com'):
        raise ValueError('Choose an executable application (.exe or .com), not a shell script.')
    return subprocess.Popen([program, str(Path(path).absolute())], shell=False,
        cwd=str(Path(path).parent), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)


def download_copy(window, site, password, remote, local, options, side, worker):
    with transfer_connection(window, site, password, 'download') as connection:
        connection.transfer_options = options
        connection.trace.display_side = side
        connection.transfer(local, remote, False, worker.cancel, worker.report, False)


class ExternalFileSession(QDialog):
    def __init__(self, window, site, password, remote, options, side, rule, editable, temporary, local, baseline):
        super().__init__(window)
        self.owner, self.site, self.password, self.remote = window, site, password, remote
        self.options, self.side, self.rule, self.editable = options, side, rule, editable
        self.temporary, self.local, self.baseline = temporary, Path(local), baseline
        self.saving = False
        self.processes = []
        self.setWindowTitle(('Edit File' if editable else 'View File') + ' — ' + remote)
        self.resize(750, 220)
        rows = QVBoxLayout(self)
        label = QLabel('Save in the external editor, then click Upload Changes. Close the external application before finishing this session.'
                       if editable else 'This is a local viewing copy. It will never upload changes. Close the external application before finishing this session.')
        label.setWordWrap(True)
        rows.addWidget(label)
        path = QLineEdit(str(self.local))
        path.setReadOnly(True)
        rows.addWidget(path)
        self.status = QLabel()
        self.status.setWordWrap(True)
        rows.addWidget(self.status)
        controls = QHBoxLayout()
        self.open_button = QPushButton('Open Application')
        self.open_button.clicked.connect(self.open_application)
        controls.addWidget(self.open_button)
        self.upload_button = QPushButton('Upload Changes')
        self.upload_button.setVisible(editable)
        self.upload_button.clicked.connect(self.upload_changes)
        controls.addWidget(self.upload_button)
        close = QPushButton('Finish')
        close.clicked.connect(self.close)
        controls.addWidget(close)
        rows.addLayout(controls)

    def open_application(self):
        try:
            self.processes = [process for process in self.processes if process.poll() is None]
            self.processes.append(launch_application(self.rule['program'], self.local))
            self.status.setText('Opened in the associated application.')
        except (OSError, ValueError) as error:
            self.status.setText(str(error))

    def upload_changes(self):
        if self.saving or not self.editable:
            return
        self.saving = True
        self.owner.directory_cache.clear(self.site)
        self.open_button.setEnabled(False)
        self.upload_button.setEnabled(False)
        self.status.setText('Checking the working copy and remote file…')
        def task(worker):
            if self.local.is_symlink() or not self.local.is_file():
                raise ValueError('The working copy must be an existing regular file.')
            with TemporaryDirectory(prefix='fluxftp-save-') as folder:
                snapshot, comparison = Path(folder) / 'upload', Path(folder) / 'comparison'
                shutil.copyfile(self.local, snapshot)
                updated = file_hash(snapshot)
                if updated != file_hash(self.local):
                    raise ValueError('The application is still changing the working copy. Save it and try again.')
                if updated == self.baseline:
                    return self.baseline
                download_copy(self.owner, self.site, self.password, self.remote, comparison, self.options, self.side, worker)
                if file_hash(comparison) != self.baseline:
                    raise ValueError('The remote file changed since opening. Upload refused; your working copy is retained.')
                if worker.cancel.is_set():
                    raise Cancelled('Cancelled')
                with transfer_connection(self.owner, self.site, self.password, 'upload') as connection:
                    connection.transfer_options = self.options
                    connection.trace.display_side = self.side
                    connection.transfer(snapshot, self.remote, True, worker.cancel, worker.report, False)
                return updated
        def finish(result=None, error=None):
            self.owner.directory_cache.clear(self.site)
            self.saving = False
            self.open_button.setEnabled(True)
            self.upload_button.setEnabled(True)
            if error is None:
                changed = result != self.baseline
                self.baseline = result
                self.status.setText('Changes uploaded to the remote file.' if changed else 'No local changes to upload.')
            else:
                self.status.setText(str(error))
        self.owner.run(task, lambda result: finish(result), lambda error: finish(error=error))

    def closeEvent(self, event):
        if self.saving:
            event.ignore()
            return
        message = 'Close the external application first. Finish this session and delete its working copy?'
        if self.editable:
            message += '\nAny edits not uploaded will be lost.'
        if QMessageBox.question(self, 'Finish file session', message,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Yes:
            event.ignore()
            return
        try:
            self.temporary.cleanup()
        except OSError as error:
            self.status.setText(f'Cannot remove the working copy yet: {error}')
            event.ignore()
            return
        event.accept()

    def reject(self):
        self.close()


def open_associated_file(window, pane, entry, editable):
    """Return False only when no rule matches, allowing built-in fallback."""
    try:
        rule = matching_rule(window.preferences, 'edit' if editable else 'view', entry.name)
        if rule is None:
            return False
        if pane.busy or entry.is_dir:
            return True
        if entry.is_link:
            raise ValueError('File associations require a regular file; symbolic links are unsupported.')
        if not Path(rule['program']).is_file():
            raise ValueError('The associated application no longer exists. Update File Associations.')
        if pane.mode == 'Local':
            processes = getattr(window, '_association_processes', [])
            window._association_processes = [process for process in processes if process.poll() is None]
            window._association_processes.append(launch_application(rule['program'], pane.join(entry.name)))
            return True
        if pane.site is None:
            raise ValueError('Connect the remote pane first.')
        safe_name(entry.name)
        options = replace(TransferOptions.from_settings(window.preferences), mode='Binary', crc_auto=False,
                          keep_file_time=False, stall_resume=False, exists_enabled=False)
        site, password, remote, side = pane.site, pane.password, pane.join(entry.name), int(pane.number)
        temporary = TemporaryDirectory(prefix='fluxftp-association-')
        local = Path(temporary.name) / entry.name
    except (OSError, ValueError, TypeError) as error:
        window.error(str(error))
        return True
    pane.busy = True
    def task(worker):
        download_copy(window, site, password, remote, local, options, side, worker)
        return file_hash(local)
    def done(baseline):
        pane.busy = False
        session = ExternalFileSession(window, site, password, remote, options, side, rule, editable, temporary, local, baseline)
        window.retain_mouse_window(session)
        session.open_application()
    def failed(error):
        pane.busy = False
        temporary.cleanup()
        window.error(str(error))
    window.run(task, done, failed)
    return True
