"""Tray commands share application actions and the ordinary transfer queue."""
import posixpath
from pathlib import Path
from types import SimpleNamespace
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QIcon, QPixmap, QPainter, QColor, QPen
from PySide6.QtWidgets import (QMenu, QDialog, QFormLayout, QComboBox, QLineEdit,
    QDialogButtonBox, QFileDialog, QPushButton, QHBoxLayout, QMessageBox, QInputDialog)
from shiboken6 import isValid
from .core import PROTOCOLS, safe_name, Cancelled


def tray_icons(dark):
    icons = []
    for active in (False, True):
        icon = QIcon()
        for size in (16, 24, 32, 48):
            image = QPixmap(size, size)
            image.fill(Qt.GlobalColor.transparent)
            painter = QPainter(image)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.scale(size / 32, size / 32)
            painter.setPen(QPen(QColor('#e4eef9' if dark else '#244c78'), 2.5))
            painter.drawRoundedRect(QRectF(3, 4, 26, 20), 3, 3)
            painter.drawLine(11, 28, 21, 28)
            painter.drawLine(16, 24, 16, 28)
            painter.setPen(QPen(QColor('#62d6a4' if dark else '#087d50') if active else
                                QColor('#e4eef9' if dark else '#244c78'), 2.5))
            painter.drawLine(8, 11, 23, 11)
            painter.drawLine(19, 7, 23, 11)
            painter.drawLine(19, 15, 23, 11)
            painter.drawLine(24, 18, 9, 18)
            painter.drawLine(13, 14, 9, 18)
            painter.drawLine(13, 22, 9, 18)
            painter.end()
            icon.addPixmap(image)
        icons.append(icon)
    return icons


def install_tray_menu(window):
    menu = QMenu(window)
    restore = menu.addAction('Restore main window', window.restore_from_tray)
    menu.setDefaultAction(restore)
    menu.addSeparator()
    for route in ('Upload', 'Download'):
        menu.addAction(window.queue_commands.actions[f'Limit {route} Speed'])
    menu.addSeparator()
    completed = menu.addMenu('On Transfer Completed')
    for action, key, value, default in window.tools_commands.choices:
        if key == 'Transfer/completed':
            completed.addAction(action)
    completed.aboutToShow.connect(window.tools_commands.update)
    menu.addSeparator()
    clipboard = menu.addMenu('Clipboard')
    clipboard.addAction(window.edit_commands.actions['Copy Selected Names'])
    clipboard.addAction(window.tools_commands.copy_path)
    clipboard.addAction(window.tools_commands.paste)
    clipboard.aboutToShow.connect(window.tools_commands.update)
    menu.addSeparator()
    menu.addAction(window.tools_commands.actions['Manual Download from Site…'])
    menu.addSeparator()
    menu.addAction(window.queue_commands.actions['Queue Information…'])
    menu.addSeparator()
    menu.addAction(window.about_action)
    menu.addSeparator()
    menu.addAction('Exit', window.close)
    menu.aboutToShow.connect(window.queue_commands.update)
    menu.aboutToShow.connect(window.tools_commands.update)
    window.tray.setContextMenu(menu)
    window.tray_menu = menu


class ManualDownload(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.setWindowTitle('Manual Download from Site')
        self.resize(540, 210)
        form = QFormLayout(self)
        self.site = QComboBox()
        self.site.addItems(sorted(name for name, data in window.sites.items() if not data.get('_disabled')))
        self.remote = QLineEdit('/')
        self.remote.setPlaceholderText('/folder/file or /folder/subfolder')
        local_pane = next((p for p in window.current_panes() if p.mode == 'Local'), None)
        self.local = QLineEdit(local_pane.path if local_pane else str(Path.home() / 'Downloads'))
        row = QHBoxLayout()
        row.addWidget(self.local)
        browse = QPushButton('Browse…')
        browse.clicked.connect(self.browse)
        row.addWidget(browse)
        form.addRow('Saved site', self.site)
        form.addRow('Remote file or folder', self.remote)
        form.addRow('Download to', row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('Add to queue')
        buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(self.site.count() > 0)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def browse(self):
        path = QFileDialog.getExistingDirectory(self, 'Download folder', self.local.text())
        if path:
            self.local.setText(path)


def manual_download(window):
    dialog = ManualDownload(window)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        dialog.deleteLater()
        return
    site_name, remote, folder = dialog.site.currentText(), dialog.remote.text().strip(), dialog.local.text().strip()
    dialog.deleteLater()
    queue_manual_download(window, site_name, remote, folder)


def queue_manual_download(window, site_name, remote, folder):
    try:
        data = window.sites.get(site_name)
        if not data or data.get('_disabled'):
            raise ValueError('Select an enabled saved site.')
        if not remote.startswith('/') or '\x00' in remote or '\n' in remote or '\r' in remote:
            raise ValueError('Enter an absolute remote file or folder path.')
        remote = posixpath.normpath(remote)
        name = posixpath.basename(remote)
        safe_name(name)
        target = Path(folder).expanduser().resolve()
        if not target.is_dir():
            raise ValueError('Choose an existing local download folder.')
        site = window.site_from_profile(data)
        workspace = window.tabs.currentWidget()
        if window.workspace_busy(workspace):
            raise ValueError('Wait for active work in this workspace before adding a manual download.')
        if site.protocol == PROTOCOLS[4] and window.preferences.value('Confirm/unencrypted', True, type=bool):
            if QMessageBox.question(window, 'Unencrypted connection',
                    'Plain FTP sends your password and files without encryption. Continue?') != QMessageBox.StandardButton.Yes:
                return
        if not window.ensure_proxy_credentials(window, site.host, site):
            return
        password = window.get_site_password(data)
        if site.username.casefold() == 'anonymous' and not password:
            password = window.preferences.value('General/anonymous_email', 'anonymous@fluxftp.local', type=str)
        elif not password and not site.cloud_account and not (site.managed_key and site.protocol == PROTOCOLS[3]):
            password, accepted = QInputDialog.getText(window, 'Manual Download from Site',
                f'Password or key passphrase for {site.username}@{site.host}:', QLineEdit.EchoMode.Password)
            if not accepted:
                return
    except (ValueError, OSError, RuntimeError) as error:
        window.error(str(error))
        return
    if not isValid(workspace) or window.tabs.indexOf(workspace) < 0 or window.workspace_busy(workspace):
        window.statusBar().showMessage('Workspace changed or became busy; manual download cancelled.', 5000)
        return
    workspace._manual_download_pending = True
    def lookup(worker):
        if worker.cancel.is_set():
            raise Cancelled('Cancelled')
        with window.new_connection(site, password) as connection:
            entries = connection.list(posixpath.dirname(remote) or '/')
        if worker.cancel.is_set():
            raise Cancelled('Cancelled')
        entry = next((entry for entry in entries if entry.name == name), None)
        if entry is None:
            raise ValueError('The remote file or folder was not found.')
        return entry
    def done(entry):
        workspace._manual_download_pending = False
        if not isValid(workspace) or window.tabs.indexOf(workspace) < 0:
            return
        source = SimpleNamespace(mode='Remote', site=site, password=password, busy=False,
                                 join=lambda _: remote, number=1)
        dest = SimpleNamespace(mode='Local', site=None, password='', busy=False,
                               join=lambda value: str(target / safe_name(value)),
                               parentWidget=lambda: workspace, number=2)
        previous = len(window.queue_jobs)
        window.enqueue_items(source, dest, [entry], start=False)
        if len(window.queue_jobs) > previous:
            window.statusBar().showMessage('Manual download added to the held queue. Use Transfer Queue to start.', 7000)
    def failed(error):
        workspace._manual_download_pending = False
        window.error(str(error))
    window.run(lookup, done, failed)
