"""Key Manager dialogs and GUI-thread password prompts for connection workers."""
from datetime import datetime, time, timezone
from threading import Event

from PySide6.QtCore import QObject, Signal, Slot, QThread, Qt, QDate
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox,
    QTreeWidget, QTreeWidgetItem, QPushButton, QLineEdit, QComboBox, QCheckBox, QLabel,
    QDateEdit, QFileDialog, QDialogButtonBox, QMessageBox, QInputDialog, QMenu)

from .core import Cancelled, PROTOCOLS
from .key_store import KeyStore, FORMATS, RSA_SIZES, CURVES, generate, import_identity


class KeyAccess(QObject):
    requested = Signal(object)

    def __init__(self, window, path):
        super().__init__(window)
        self.window = window
        self.store = KeyStore(path)
        self.pending_prompts = []
        self.prompting = False
        self.requested.connect(self.prompt)

    @Slot(object)
    def prompt(self, request):
        if request['done'].is_set():
            return
        self.pending_prompts.append(request)
        if self.prompting:
            return
        self.prompting = True
        try:
            while self.pending_prompts:
                current = self.pending_prompts.pop(0)
                if current['done'].is_set():
                    continue
                try:
                    password, ok = QInputDialog.getText(self.window, 'Unlock private key',
                        f"Password for {current['name']}:\nRequired for each new connection using this key.",
                        QLineEdit.EchoMode.Password)
                    if not current['done'].is_set():
                        current['password'] = password if ok else None
                finally:
                    current['done'].set()
        finally:
            self.prompting = False

    def resolve(self, site):
        record = self.store.metadata().get(site.managed_key)
        if record is None:
            raise ValueError('Managed key not found. Select an existing key in site properties.')
        expected = 'ssh' if site.protocol in (PROTOCOLS[3], 'SCP · SSH') else 'tls'
        if site.protocol == PROTOCOLS[4] or record['kind'] != expected:
            raise ValueError('The selected key is incompatible with this connection protocol.')
        password = ''
        if record['strong']:
            request = dict(name=record['name'], done=Event(), password=None)
            if QThread.currentThread() == self.thread():
                self.prompt(request)
            else:
                self.requested.emit(request)
                if not request['done'].wait(300):
                    request['done'].set()
                    raise Cancelled('Private key unlock timed out.')
            password = request.pop('password')
            if password is None:
                raise Cancelled('Private key unlock cancelled.')
        result = self.store.unlock(site.managed_key, password)
        if result[0]['expires'] and datetime.fromisoformat(result[0]['expires']) <= datetime.now(timezone.utc):
            raise ValueError('The selected client certificate has expired.')
        return result


class KeyForm(QDialog):
    def __init__(self, manager, importing=False):
        super().__init__(manager)
        self.manager, self.importing = manager, importing
        self.working = False
        self.setWindowTitle('Key Manager')
        self.resize(500, 660 if not importing else 540)
        outer = QVBoxLayout(self)
        self.group = QGroupBox('Import' if importing else 'Create New Certificate (Self-Signed)')
        outer.addWidget(self.group, 1)
        self.form = QFormLayout(self.group)
        self.name = QLineEdit()
        self.form.addRow('Name:', self.name)
        self.format = QComboBox()
        self.format.addItems(('X.509 certificate for FTPS', 'SSH key for SFTP', 'PKCS#12 certificate for FTPS (.p12/.pfx)')
                             if importing else FORMATS)
        self.form.addRow('Type:' if importing else 'Key Format:', self.format)
        self.size = QComboBox()
        self.form.addRow('Key Size:', self.size)
        self.public = QLineEdit()
        self.private = QLineEdit()
        self.public_row = self.file_row(self.public)
        self.private_row = self.file_row(self.private)
        self.form.addRow('Public Key File:', self.public_row)
        self.form.addRow('Private Key File:', self.private_row)
        self.subject = {}
        for name in ('Common Name', 'Organization', 'Organization Unit', 'E-Mail', 'State or Province', 'City/Town', 'Country'):
            field = QLineEdit()
            if name == 'Country':
                field.setMaxLength(2)
                field.setMaximumWidth(60)
            self.subject[name] = field
            self.form.addRow(name + ':', field)
        self.expiry = QDateEdit(QDate.currentDate().addYears(2))
        self.expiry.setCalendarPopup(True)
        self.expiry.setMinimumDate(QDate.currentDate().addDays(1))
        self.form.addRow('Expiration:', self.expiry)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.form.addRow('Password:', self.password)
        self.strong = QCheckBox('Enable strong private key protection.')
        self.form.addRow('', self.strong)
        self.protection_note = QLabel('You will be prompted for this password each time a new connection uses the key.\nPrivate keys are always stored in the encrypted vault.')
        self.protection_note.setWordWrap(True)
        self.protection_note.setMinimumHeight(self.fontMetrics().lineSpacing() * 4)
        self.form.addRow('', self.protection_note)
        self.exportable = QCheckBox('Allow exporting of private key')
        self.form.addRow('', self.exportable)
        self.note = QLabel()
        self.note.setWordWrap(True)
        self.form.addRow(self.note)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.save)
        self.buttons.rejected.connect(self.reject)
        outer.addWidget(self.buttons)
        self.format.currentIndexChanged.connect(self.update_format)
        self.update_format()

    def file_row(self, field):
        from PySide6.QtWidgets import QWidget, QStyle
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(field)
        browse = QPushButton()
        browse.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon))
        browse.setToolTip('Choose key or certificate file')
        def choose():
            path, _ = QFileDialog.getOpenFileName(self, 'Select key or certificate', '', 'Key and certificate files (*)')
            if path:
                field.setText(path)
        browse.clicked.connect(choose)
        layout.addWidget(browse)
        return row

    def update_format(self):
        label = self.format.currentText()
        tls = 'FTPS' in label
        bundle = label.startswith('PKCS#12')
        self.form.setRowVisible(self.size, not self.importing)
        self.form.setRowVisible(self.public_row, self.importing and not bundle)
        self.form.setRowVisible(self.private_row, self.importing)
        self.form.labelForField(self.public_row).setText('Certificate File:' if tls else 'Public Key File:')
        self.form.labelForField(self.private_row).setText('PKCS#12 File:' if bundle else 'Private Key File:')
        for field in self.subject.values():
            self.form.setRowVisible(field, tls and not self.importing)
        self.form.setRowVisible(self.expiry, tls and not self.importing)
        self.size.clear()
        self.size.addItems([str(n) for n in (RSA_SIZES if 'RSA' in label else CURVES if 'ECDSA' in label else [256])])
        if 'RSA' in label:
            self.size.setCurrentText('3072')
        self.size.setEnabled('Ed25519' not in label)
        if not self.importing:
            self.group.setTitle('Create New Certificate (Self-Signed)' if tls else 'Create New ' + label.split(' key')[0] + ' Key')
        self.note.setText(('Self-signed client certificates must be accepted by the server administrator.' if tls and not self.importing else
            'PEM/DER certificates and PEM/PKCS#8 keys; PKCS#12 includes the certificate and private key.' if tls else
            'OpenSSH or PEM/PKCS#8 private keys. Public key is optional on import. DSA and PuTTY PPK are not supported.'))

    def save(self):
        if self.working:
            return
        name, password = self.name.text().strip(), self.password.text()
        if not name or (self.strong.isChecked() and not password):
            QMessageBox.warning(self, 'Key Manager', 'Enter a name and a password when strong protection is enabled.')
            return
        label = self.format.currentText()
        strong, exportable = self.strong.isChecked(), self.exportable.isChecked()
        if self.importing:
            private, public = self.private.text(), self.public.text()
            work = lambda: import_identity('tls' if 'FTPS' in label else 'ssh', private, public, password,
                                           bundle=label.startswith('PKCS#12'))
        else:
            size = int(self.size.currentText())
            subject = {name: field.text() for name, field in self.subject.items()}
            expires = datetime.combine(self.expiry.date().toPython(), time(23, 59, 59), timezone.utc)
            work = lambda: generate(label, size, subject, expires)
        self.working = True
        self.group.setEnabled(False)
        self.buttons.setEnabled(False)
        def task(_worker):
            return work()
        def failed(error):
            self.working = False
            self.group.setEnabled(True)
            self.buttons.setEnabled(True)
            QMessageBox.warning(self, 'Key Manager', str(error))
        def done(material):
            try:
                self.manager.store.add(name, material, strong, password, exportable)
            except (ValueError, RuntimeError, OSError) as error:
                failed(error)
                return
            self.working = False
            self.password.clear()
            self.accept()
        self.manager.window.run(task, done, failed)

    def reject(self):
        if not self.working:
            self.password.clear()
            super().reject()

    def closeEvent(self, event):
        if self.working:
            event.ignore()
        else:
            super().closeEvent(event)


class KeyManager(QDialog):
    def __init__(self, window, parent=None):
        super().__init__(parent or window)
        self.window, self.store = window, window.key_access.store
        self.setWindowTitle('Key Manager')
        self.resize(540, 480)
        outer = QVBoxLayout(self)
        group = QGroupBox('List')
        body = QVBoxLayout(group)
        self.tree = QTreeWidget()
        self.tree.setAccessibleName('Managed SSH keys and TLS certificates')
        self.tree.setRootIsDecorated(False)
        self.tree.setHeaderLabels(['Name', 'Type', 'Expiration', 'Key Size'])
        self.tree.setColumnWidth(0, 175)
        self.tree.setColumnWidth(1, 130)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.context_menu)
        body.addWidget(self.tree)
        outer.addWidget(group, 1)
        buttons = QHBoxLayout()
        for title, callback in (('Generate', lambda: self.edit(False)), ('Import', lambda: self.edit(True))):
            button = QPushButton(title)
            button.clicked.connect(callback)
            buttons.addWidget(button)
        buttons.addStretch()
        close = QPushButton('Close')
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        outer.addLayout(buttons)
        self.refresh()

    def refresh(self):
        self.tree.clear()
        try:
            for identity, record in self.store.metadata().items():
                row = QTreeWidgetItem([record['name'], ('X.509 / ' if record['kind'] == 'tls' else 'SSH / ') + record['algorithm'],
                    record['expires'][:10] or 'None', str(record['bits'])])
                row.setData(0, Qt.ItemDataRole.UserRole, identity)
                row.setToolTip(0, 'Right-click to export the public key/certificate, export an allowed private key, or delete.')
                self.tree.addTopLevelItem(row)
            if self.tree.topLevelItemCount():
                self.tree.resizeColumnToContents(0)
                self.tree.resizeColumnToContents(1)
        except (ValueError, RuntimeError, OSError) as error:
            QMessageBox.warning(self, 'Key Manager', str(error))

    def edit(self, importing):
        dialog = KeyForm(self, importing)
        if dialog.exec():
            self.refresh()

    def context_menu(self, point):
        row = self.tree.itemAt(point)
        if row is None:
            return
        identity = row.data(0, Qt.ItemDataRole.UserRole)
        try:
            record = self.store.metadata()[identity]
            menu = QMenu(self)
            public = menu.addAction('Export Public Key / Certificate…')
            private = menu.addAction('Export Private Key…')
            private.setEnabled(record['exportable'])
            menu.addSeparator()
            delete = menu.addAction('Delete')
            selected = menu.exec(self.tree.viewport().mapToGlobal(point))
            if selected == delete:
                if any(profile.get('managed_key') == identity for profile in self.window.sites.values()):
                    raise ValueError('This key is used by a saved site. Change that site’s managed key first.')
                if QMessageBox.question(self, 'Delete key', 'Permanently delete this managed key?') == QMessageBox.StandardButton.Yes:
                    self.store.remove(identity)
                    self.refresh()
            elif selected in (public, private):
                data = record['public'].encode('ascii')
                if selected == private:
                    password = ''
                    if record['strong']:
                        password, ok = QInputDialog.getText(self, 'Unlock key', 'Current key password:', QLineEdit.EchoMode.Password)
                        if not ok:
                            return
                    export_password, ok = QInputDialog.getText(self, 'Protect export', 'Password for exported private key:', QLineEdit.EchoMode.Password)
                    if not ok:
                        return
                    data = self.store.export_private(identity, password, export_password)
                path, _ = QFileDialog.getSaveFileName(self, 'Export key', '', 'Key files (*.pem *.pub);;All files (*)')
                if path:
                    from pathlib import Path
                    Path(path).write_bytes(data)
        except (ValueError, RuntimeError, OSError) as error:
            QMessageBox.warning(self, 'Key Manager', str(error))
