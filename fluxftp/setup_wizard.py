"""Guided saved-site setup, read-only connection test and workspace opening."""
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QWizard, QWizardPage, QVBoxLayout, QFormLayout,
    QComboBox, QLineEdit, QPushButton, QFileDialog, QLabel, QMessageBox)
from .automation import remote_path, saved_connection, list_folder
from .server_commands import public_text


def label(text):
    item = QLabel(text)
    item.setWordWrap(True)
    item.setTextFormat(Qt.TextFormat.PlainText)
    return item


class SetupWizard(QWizard):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.worker = None
        self.tested = None
        self.test_site = self.test_password = None
        self.closing = False
        self.setWindowTitle('FluxFTP Wizard')
        self.resize(700, 500)
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        page = QWizardPage()
        page.setTitle('Choose or create a saved site')
        layout = QVBoxLayout(page)
        layout.addWidget(label('Set up an FTP, FTPS, SFTP or cloud connection, test a remote folder, '
            'then open it alongside a local folder. No files are transferred by this wizard.'))
        self.site = QComboBox()
        self.site.setAccessibleName('Wizard saved site')
        layout.addWidget(self.site)
        for title, callback in (('Site Manager…', self.manage_sites), ('Cloud Accounts…', self.cloud_accounts)):
            button = QPushButton(title)
            button.clicked.connect(callback)
            layout.addWidget(button)
        layout.addWidget(label('For cloud storage, add an account in Cloud Accounts, then create a saved site '
            'that uses that account in Site Manager. Settings saved in those dialogs remain if you cancel this wizard.'))
        layout.addStretch()
        self.addPage(page)
        page = QWizardPage()
        page.setTitle('Choose folders and test the connection')
        form = QFormLayout(page)
        self.remote = QLineEdit('/')
        self.local = QLineEdit(str(Path.home()))
        form.addRow('Remote folder', self.remote)
        form.addRow('Local folder', self.local)
        browse = QPushButton('Choose local folder…')
        browse.clicked.connect(self.browse)
        form.addRow(browse)
        form.addRow(label('Public S3/Azure/Swift storage needs a bucket/container path rather than /. '
            'Testing lists the folder and verifies read access. Write access is not tested.'))
        self.test_button = QPushButton('Test Connection')
        self.test_button.clicked.connect(self.test)
        form.addRow(self.test_button)
        self.status = label('Ready to test.')
        self.status.setAccessibleName('Wizard connection test result')
        form.addRow(self.status)
        self.addPage(page)
        page = QWizardPage()
        page.setTitle('Ready to browse and transfer')
        layout = QVBoxLayout(page)
        self.summary = label('')
        layout.addWidget(self.summary)
        layout.addWidget(label('Finish opens a new workspace with your local folder on the left and the tested remote folder '
            'on the right. Select files and use the transfer controls when you are ready.\n\n'
            'For repeatable work, open Tools → Script Designer or Scheduled Automation. '
            'Command Designer provides FTP/FTPS command templates.'))
        layout.addStretch()
        self.addPage(page)
        self.site.currentTextChanged.connect(self.site_changed)
        self.remote.textChanged.connect(self.invalidate)
        self.currentIdChanged.connect(self.page_changed)
        self.refresh_sites()

    def refresh_sites(self):
        chosen = self.site.currentText()
        self.site.blockSignals(True)
        self.site.clear()
        self.site.addItems(sorted(n for n, s in self.window.sites.items() if not s.get('_disabled')))
        if self.site.findText(chosen) >= 0:
            self.site.setCurrentText(chosen)
        self.site.blockSignals(False)
        self.site_changed()

    def site_changed(self, *_):
        profile = self.window.sites.get(self.site.currentText(), {})
        self.remote.setText(profile.get('root') or '/')
        if profile.get('local_path'):
            self.local.setText(profile['local_path'])
        self.invalidate()

    def invalidate(self, *_):
        self.tested = None
        self.status.setText('Ready to test.')

    def manage_sites(self):
        self.window.manage_sites()
        self.refresh_sites()

    def cloud_accounts(self):
        from .cloud_accounts import CloudAccounts
        dialog = CloudAccounts(self.window)
        dialog.exec()
        dialog.deleteLater()
        self.refresh_sites()

    def browse(self):
        path = QFileDialog.getExistingDirectory(self, 'Local folder', self.local.text())
        if path:
            self.local.setText(path)

    def key(self):
        # Changes in Site Manager invalidate previously successful tests.
        return (self.site.currentText(), self.remote.text().strip(),
                repr(self.window.sites.get(self.site.currentText())))

    def test(self):
        if self.worker:
            return
        self.tested = None
        try:
            path = remote_path(self.remote.text().strip())
            connection = saved_connection(self.window, self.site.currentText(), unattended=False)
            from .core import PROTOCOLS
            if connection.site.protocol == PROTOCOLS[4] and self.window.preferences.value('Confirm/unencrypted', True, type=bool):
                if QMessageBox.question(self, 'Unencrypted connection', 'Plain FTP sends your password and files without encryption. Continue?') != QMessageBox.StandardButton.Yes:
                    return
        except (ValueError, OSError, RuntimeError) as error:
            self.status.setText(str(error))
            return
        key = self.key()
        self.test_site, self.test_password = connection.site, connection.password
        self.set_busy(True)
        self.status.setText('Testing connection…')
        def work(worker):
            with connection:
                return len(list_folder(connection, path, worker.cancel))
        def done(count):
            if not self.closing:
                self.tested = key
                self.status.setText(f'Connection successful. Listed {count} entries; read access verified.')
            self.finished_test()
        def failed(error):
            secrets = (connection.password, (getattr(connection, 'proxy', None) or {}).get('password', ''))
            self.status.setText('Connection failed: ' + public_text(str(error), secrets)[:500])
            self.finished_test()
        self.worker = self.window.run(work, done, failed, raw_failure=True, autostart=False)
        self.worker.start()

    def set_busy(self, busy):
        for widget in (self.site, self.remote, self.local, self.test_button, self.button(QWizard.WizardButton.BackButton),
                       self.button(QWizard.WizardButton.NextButton)):
            widget.setEnabled(not busy)

    def finished_test(self):
        self.worker = None
        self.set_busy(False)
        if self.closing:
            super().reject()

    def validateCurrentPage(self):
        if self.worker:
            return False
        if self.currentId() == 0 and not self.site.currentText():
            QMessageBox.information(self, 'Saved site required', 'Create a saved site in Site Manager first.')
            return False
        if self.currentId() >= 1:
            if self.tested != self.key():
                QMessageBox.information(self, 'Test connection', 'Run a successful connection test before continuing.')
                return False
            if not Path(self.local.text()).is_dir():
                QMessageBox.information(self, 'Local folder', 'Choose an existing local folder.')
                return False
        return True

    def page_changed(self, page):
        if page == 2:
            self.summary.setText(f'Site: {self.site.currentText()}\nRemote folder: {self.remote.text()}\nLocal folder: {self.local.text()}\n\nRead access verified.')

    def accept(self):
        if not self.validateCurrentPage():
            return
        if self.window.tabs.count() >= 100:
            QMessageBox.information(self, 'Workspace limit', 'Close a workspace before finishing the wizard.')
            return
        self.window.add_workspace()
        left, right = self.window.current_panes()
        left.local_path = str(Path(self.local.text()).resolve())
        left.change_mode('Local')
        left.navigate(left.local_path)
        right.set_remote(self.test_site, self.test_password, self.remote.text().strip())
        right.refresh()
        self.window.save_workspaces()
        super().accept()

    def reject(self):
        if self.worker:
            self.closing = True
            self.worker.cancel.set()
            self.status.setText('Cancelling — waiting for the connection test to stop…')
            return
        super().reject()

    def closeEvent(self, event):
        if self.worker:
            self.reject()
            event.ignore()
        else:
            super().closeEvent(event)


def show_wizard(window):
    dialog = SetupWizard(window)
    dialog.exec()
    dialog.deleteLater()
