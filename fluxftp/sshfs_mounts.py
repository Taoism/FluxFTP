"""Optional SSHFS mounts backed by saved, host-key-verified SFTP sites."""
from __future__ import annotations

import os
import re
import shutil
import tempfile
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Signal
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout)


WINDOWS_REQUIREMENT = (
    'SSHFS-Win and WinFsp are required on Windows. Install both, then restart FluxFTP. '
    'The SSHFS executable must be available as sshfs.exe or in the standard SSHFS-Win folder.'
)
UNIX_REQUIREMENT = (
    'The sshfs executable and a working FUSE installation are required. Install the '
    'packages provided for your operating system, then restart FluxFTP.'
)


def sshfs_program():
    program = shutil.which('sshfs') or shutil.which('sshfs.exe')
    if program:
        return program
    if os.name == 'nt':
        for base in (os.environ.get('ProgramFiles', r'C:\Program Files'),
                     os.environ.get('ProgramW6432', r'C:\Program Files')):
            candidate = Path(base) / 'SSHFS-Win' / 'bin' / 'sshfs.exe'
            if candidate.is_file():
                return str(candidate)
    return ''


def windows_drive_letters_in_use():
    """Use the Windows logical-drive bitmask, including disconnected mappings."""
    if os.name != 'nt':
        return set()
    import ctypes
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.GetLogicalDrives.restype = ctypes.c_uint32
    mask = kernel32.GetLogicalDrives()
    if not mask:
        raise ctypes.WinError(ctypes.get_last_error())
    return {chr(ord('A') + index) for index in range(26) if mask & (1 << index)}


def _known_hosts_file(window, profile):
    """Build a temporary OpenSSH trust file from FluxFTP's existing trust sources."""
    candidates = [Path(window.ssh_trust.path), Path.home() / '.ssh' / 'known_hosts']
    configured = str(profile.get('known_hosts') or '').strip()
    if configured:
        candidates.append(Path(configured).expanduser())
    lines, seen = [], set()
    for source in candidates:
        try:
            if source.is_symlink() or not source.is_file() or source.stat().st_size > 4_000_000:
                continue
            for line in source.read_text('utf-8', errors='strict').splitlines():
                line = line.strip()
                if line and not line.startswith('#') and line not in seen:
                    seen.add(line)
                    lines.append(line)
        except (OSError, UnicodeError):
            continue
    if not lines:
        raise ValueError('This site has no trusted SSH host key. Connect with FluxFTP first and verify the fingerprint.')
    folder = window.sites_file.parent / 'sshfs-trust'
    folder.mkdir(parents=True, exist_ok=True)
    fd, filename = tempfile.mkstemp(prefix='known-hosts-', dir=folder)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
            stream.write('\n'.join(lines) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        if os.name != 'nt':
            os.chmod(filename, 0o600)
        return Path(filename)
    except BaseException:
        try:
            os.unlink(filename)
        except OSError:
            pass
        raise


class SSHFSMounts(QObject):
    changed = Signal()

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.mounts = []

    def start(self, site_name, remote_path, mount_point):
        profile = self.window.sites.get(site_name)
        if not profile or profile.get('_disabled'):
            raise ValueError('Choose an enabled saved SFTP site.')
        site = self.window.site_from_profile(profile)
        if site.protocol != 'SFTP · SSH':
            raise ValueError('SSHFS mounts require a saved SFTP · SSH site.')
        if site.managed_key:
            raise ValueError('SSHFS cannot use FluxFTP-managed keys yet. Select an SSH key file or configure an SSH agent for this site.')
        if not site.username or any(c in site.username for c in '\r\n\x00@:/'):
            raise ValueError('The saved SSH username cannot be used by SSHFS.')
        if not site.host or any(c.isspace() or c in '\r\n\x00' for c in site.host):
            raise ValueError('The saved SSH host is invalid.')
        if not remote_path.startswith('/') or any(c in remote_path for c in '\r\n\x00'):
            raise ValueError('Enter an absolute remote folder path without control characters.')
        if any(item['mount_point'].casefold() == mount_point.casefold() for item in self.mounts):
            raise ValueError('That mount point is already managed by FluxFTP.')
        if os.name == 'nt':
            if not re.fullmatch(r'[A-Za-z]:', mount_point):
                raise ValueError('Choose an unused drive letter, such as R:.')
            if mount_point[0].upper() in windows_drive_letters_in_use():
                raise ValueError('That drive letter is already in use.')
        else:
            target = Path(mount_point).expanduser()
            if not target.is_dir() or any(target.iterdir()):
                raise ValueError('Choose an existing empty local folder as the mount point.')
            mount_point = str(target.absolute())

        program = sshfs_program()
        if not program:
            raise RuntimeError(WINDOWS_REQUIREMENT if os.name == 'nt' else UNIX_REQUIREMENT)
        if site.ssh_key and (Path(site.ssh_key).is_symlink() or not Path(site.ssh_key).is_file()):
            raise ValueError('The SSH identity file saved for this site is unavailable.')
        known_hosts = _known_hosts_file(self.window, profile)
        host = site.host
        if ':' in host and not host.startswith('['):
            host = '[' + host + ']'
        remote = f'{site.username}@{host}:{remote_path}'
        ssh_options = [
            'StrictHostKeyChecking=yes',
            'UserKnownHostsFile=' + known_hosts.as_posix(),
            'GlobalKnownHostsFile=none',
            'BatchMode=yes',
            'PasswordAuthentication=no',
            'KbdInteractiveAuthentication=no',
            'PreferredAuthentications=publickey',
            'ConnectTimeout=10',
        ]
        if site.ssh_key:
            ssh_options.extend(('IdentitiesOnly=yes', 'IdentityFile=' + Path(site.ssh_key).absolute().as_posix()))
        # Keep sshfs attached so FluxFTP owns its lifecycle and can unmount it.
        args = ['-f', remote, mount_point, '-p', str(site.port)]
        for option in ssh_options:
            args.extend(('-o', option))
        process = QProcess(self)
        process.setProgram(program)
        process.setArguments(args)
        if os.name == 'nt':
            environment = QProcessEnvironment.systemEnvironment()
            bin_dir = str(Path(program).parent)
            environment.insert('PATH', bin_dir + os.pathsep + environment.value('PATH'))
            process.setProcessEnvironment(environment)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        record = dict(site=site_name, remote=remote_path, mount_point=mount_point,
                      process=process, known_hosts=known_hosts, output='')
        self.mounts.append(record)
        process.readyReadStandardOutput.connect(lambda r=record: self._read_output(r))
        process.finished.connect(lambda code, status, r=record: self._finished(r, code, status))
        process.errorOccurred.connect(lambda error, r=record: self._process_error(r, error))
        process.start()
        if not process.waitForStarted(3000):
            message = process.errorString()
            self._remove(record)
            raise RuntimeError('Could not start SSHFS: ' + message)
        self.changed.emit()

    def stop(self, record):
        if record not in self.mounts:
            return
        process = record['process']
        if process.state() == QProcess.ProcessState.NotRunning:
            self._remove(record)
            return
        process.terminate()
        if not process.waitForFinished(2500):
            process.kill()
            process.waitForFinished(2500)
        self._remove(record)

    def _read_output(self, record):
        try:
            record['output'] = (record['output'] + bytes(record['process'].readAllStandardOutput()).decode('utf-8', 'replace'))[-4000:]
        except RuntimeError:
            pass
        self.changed.emit()

    def _process_error(self, record, error):
        record['output'] = (record['output'] + '\n' + record['process'].errorString())[-4000:]
        self.changed.emit()

    def _finished(self, record, code, status):
        if record not in self.mounts:
            return
        detail = record['output'].strip()
        self._remove(record)
        if detail and code:
            safe = detail[:1200]
            self.window.statusBar().showMessage(f"SSHFS stopped for {record['mount_point']}: {safe}", 12000)

    def _remove(self, record):
        if record in self.mounts:
            self.mounts.remove(record)
        try:
            record['known_hosts'].unlink(missing_ok=True)
        except OSError:
            pass
        process = record['process']
        process.deleteLater()
        self.changed.emit()

    def show(self):
        dialog = SSHFSMountDialog(self.window, self)
        dialog.exec()
        dialog.deleteLater()


class SSHFSMountDialog(QDialog):
    def __init__(self, window, manager):
        super().__init__(window)
        self.window, self.manager = window, manager
        self.setWindowTitle('SSHFS Mounts')
        self.resize(760, 480)
        layout = QVBoxLayout(self)
        note = QLabel('Mount a saved, verified SFTP site as a local drive or folder. SSHFS-Win + WinFsp are required on Windows; other systems need sshfs + FUSE. Password-only and FluxFTP-managed-key authentication are not supported. Host keys are checked strictly against FluxFTP and saved-site trust.')
        note.setWordWrap(True)
        layout.addWidget(note)

        form = QFormLayout()
        self.site = QComboBox()
        self.site.addItems(sorted(name for name, profile in window.sites.items()
                                  if not profile.get('_disabled') and profile.get('protocol') == 'SFTP · SSH'))
        self.remote = QLineEdit()
        selected = window.sites.get(self.site.currentText(), {})
        self.remote.setText(selected.get('root', '/') or '/')
        self.site.currentTextChanged.connect(self._site_changed)
        form.addRow('Saved SFTP site', self.site)
        form.addRow('Remote folder', self.remote)
        target_row = QHBoxLayout()
        self.target = QLineEdit()
        self.target.setPlaceholderText('Drive letter such as R:' if os.name == 'nt' else 'Existing empty folder')
        browse = QPushButton('Browse…')
        browse.clicked.connect(self._browse)
        target_row.addWidget(self.target, 1)
        target_row.addWidget(browse)
        form.addRow('Local mount point', target_row)
        layout.addLayout(form)
        row = QHBoxLayout()
        self.mount_button = QPushButton('Mount')
        self.unmount_button = QPushButton('Unmount selected')
        row.addWidget(self.mount_button)
        row.addWidget(self.unmount_button)
        row.addStretch()
        layout.addLayout(row)
        self.mount_button.clicked.connect(self._mount)
        self.unmount_button.clicked.connect(self._unmount)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(['Site', 'Remote folder', 'Mount point', 'Status'])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAccessibleName('SSHFS mounts')
        layout.addWidget(self.table, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)
        self.manager.changed.connect(self.refresh)
        self.refresh()

    def _site_changed(self, name):
        profile = self.window.sites.get(name, {})
        self.remote.setText(profile.get('root', '/') or '/')

    def _browse(self):
        if os.name == 'nt':
            QMessageBox.information(self, 'Choose a drive letter', 'Enter an unused drive letter, such as R:.')
            return
        path = QFileDialog.getExistingDirectory(self, 'Choose an empty SSHFS mount folder', self.target.text())
        if path:
            self.target.setText(path)

    def _mount(self):
        try:
            self.manager.start(self.site.currentText(), self.remote.text().strip(), self.target.text().strip())
        except (OSError, RuntimeError, ValueError) as error:
            self.window.error(str(error))

    def _unmount(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self.manager.mounts):
            return
        self.manager.stop(self.manager.mounts[row])

    def refresh(self):
        self.table.setRowCount(len(self.manager.mounts))
        for row, record in enumerate(self.manager.mounts):
            status = 'Mounted' if record['process'].state() == QProcess.ProcessState.Running else 'Starting'
            if record['output'].strip():
                status = record['output'].strip().splitlines()[-1][:180]
            for column, value in enumerate((record['site'], record['remote'], record['mount_point'], status)):
                self.table.setItem(row, column, QTableWidgetItem(value))
        self.table.resizeColumnsToContents()
