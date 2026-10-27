"""Cloud account setup using each provider's guided configuration questions."""
import uuid
from threading import Event

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel,
    QLineEdit, QComboBox, QPushButton, QTreeWidget, QTreeWidgetItem, QMessageBox, QScrollArea)

from .cloud import PROVIDERS, operation_error, cloud_path


def setup_error(details):
    """Describe known failures without exposing provider URLs or credentials."""
    text = str(details).lower()
    if 'invalid_grant' in text:
        return operation_error(details)
    if 'objecthandle is invalid' in text:
        return ('Microsoft returned a drive entry that it could not open (ObjectHandle is Invalid). '
                'Select Next, choose OneDrive, then select the entry labeled default drive.')
    if 'failed to query root for drive' in text:
        return 'OneDrive could not open the selected drive. Check that the signed-in Microsoft account can open it in OneDrive. Select Next to choose a drive again.'
    if 'failed to query available drives' in text or 'no drives found' in text:
        return 'OneDrive could not find an accessible drive. Open OneDrive in your browser to finish account setup, then select Next to continue.'
    return 'The provider could not complete this setup step. Check account access and permissions, then select Next to retry or Cancel to restart authorization.'


class CloudAccountWizard(QDialog):
    def __init__(self, window, parent=None, provider=None, identity=None):
        super().__init__(parent or window)
        self.window = window
        self.editing = identity is not None
        self.service = window.cloud_service.begin_edit(identity) if self.editing else window.cloud_service
        self.identity = identity or uuid.uuid4().hex
        self.worker = None
        self.state = None
        self.pending_result = None
        self.started = False
        self.completed = False
        self.busy = False
        self.cancel_event = Event()
        self.setWindowTitle('Edit Cloud Account' if self.editing else 'Add Cloud Account')
        self.resize(650, 510)
        outer = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit()
        self.provider = QComboBox()
        self.provider.addItems(PROVIDERS)
        if provider in PROVIDERS:
            self.provider.setCurrentText(provider)
        if self.editing:
            account = self.service.accounts()[self.identity]
            self.name.setText(account['name'])
            self.provider.setCurrentText(account['provider'])
            self.provider.setEnabled(False)
        form.addRow('Account name', self.name)
        form.addRow('Provider', self.provider)
        outer.addLayout(form)
        self.help = QLabel('Choose a provider and name this account. Setup will ask for provider credentials or open browser authorization.\n\nFor OneDrive Business choose a business drive; for SharePoint choose the site’s document library. Credentials are stored in an encrypted configuration.')
        self.help.setTextFormat(Qt.TextFormat.PlainText)
        self.help.setWordWrap(True)
        if self.editing:
            self.help.setText('Edit this account’s name and connection settings. Existing values are used as defaults. '
                'Changes are saved only when setup finishes; Cancel keeps the original account. '
                'Saved sites stay linked to this account. Bucket and starting-folder paths are set in Site Manager.')
        self.help.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        help_scroll = QScrollArea()
        help_scroll.setWidgetResizable(True)
        help_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        help_scroll.setWidget(self.help)
        outer.addWidget(help_scroll, 1)
        self.answer = QLineEdit()
        self.choices = QComboBox()
        outer.addWidget(self.answer)
        outer.addWidget(self.choices)
        self.answer.hide()
        self.choices.hide()
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        outer.addWidget(self.status)
        buttons = QHBoxLayout()
        buttons.addStretch()
        self.next = QPushButton('Next')
        self.next.clicked.connect(self.advance)
        self.cancel = QPushButton('Cancel')
        self.cancel.clicked.connect(self.reject)
        buttons.addWidget(self.next)
        buttons.addWidget(self.cancel)
        outer.addLayout(buttons)

    def advance(self):
        if self.busy:
            return
        if not self.name.text().strip():
            self.status.setText('Enter an account name.')
            return
        if self.state is None:
            backend, defaults = PROVIDERS[self.provider.currentText()]
            endpoint = 'config/create'
            parameters = dict(name='c' + self.identity, type=backend, parameters=defaults,
                              opt={'nonInteractive': True, 'all': True})
            if self.editing:
                endpoint = 'config/update'
                parameters = dict(name='c' + self.identity, parameters={},
                                  opt={'nonInteractive': True, 'all': True})
        elif self.state:
            if self.pending_result is not None:
                answer = self.pending_result
            elif self.choices.isVisible():
                index = self.choices.currentIndex()
                answer = (self.choices.currentData() if index >= 0 and
                          self.choices.currentText() == self.choices.itemText(index)
                          else self.choices.currentText())
            else:
                answer = self.answer.text()
            endpoint = 'config/update'
            parameters = dict(name='c' + self.identity, parameters={}, opt={'nonInteractive': True,
                'continue': True, 'state': self.state, 'result': str(answer if answer is not None else '')})
        else:
            return
        self.busy = True
        self.started = True
        self.next.setEnabled(False)
        self.name.setEnabled(False)
        self.provider.setEnabled(False)
        self.answer.setEnabled(False)
        self.choices.setEnabled(False)
        self.status.setText('Working… Complete browser authorization if requested. You can cancel here.')
        def work(_worker):
            return self.service.job(endpoint, parameters, cancel=self.cancel_event, timeout=600)
        def failed(error):
            self.busy = False
            self.next.setEnabled(True)
            self.answer.setEnabled(True)
            self.choices.setEnabled(True)
            self.status.setText(str(error))
            if self.cancel_event.is_set():
                self.cleanup()
                super(CloudAccountWizard, self).reject()
        def done(result):
            self.busy = False
            self.answer.clear()
            if self.cancel_event.is_set():
                self.cleanup()
                super(CloudAccountWizard, self).reject()
                return
            self.state = result.get('State', '')
            self.next.setEnabled(True)
            self.answer.setEnabled(True)
            self.choices.setEnabled(True)
            option = result.get('Option')
            self.pending_result = None
            if not self.state:
                try:
                    self.finish_account()
                except (ValueError, RuntimeError, OSError) as error:
                    self.status.setText(str(error))
                    self.name.setEnabled(True)
                    self.provider.setEnabled(not self.editing)
                    self.next.setText('Save')
                    self.next.clicked.disconnect()
                    self.next.clicked.connect(self.save_completed)
                    return
                self.completed = True
                self.accept()
                return
            if not option:
                # ConfigError and ConfigResult legitimately return a state with
                # no question. Continue with Result, never a stale UI answer.
                self.pending_result = str(result.get('Result') or '')
                self.answer.hide()
                self.choices.hide()
                self.help.setText('Cloud account setup needs another step.')
                self.status.setText(setup_error(result['Error']) if result.get('Error')
                                    else 'Select Next to continue setup.')
                return
            self.help.setText(option.get('Help', option.get('Name', '')))
            default = option.get('Default', '')
            if self.editing and option.get('Name') == 'config_refresh_token':
                default = False
            default = str(default).lower() if isinstance(default, bool) else str(default if default is not None else '')
            secret = option.get('IsPassword') or option.get('Sensitive')
            self.answer.setEchoMode(QLineEdit.EchoMode.Password if secret else QLineEdit.EchoMode.Normal)
            self.answer.setAccessibleName(option.get('Name', 'Account setting'))
            self.choices.setAccessibleName(option.get('Name', 'Account setting'))
            self.answer.setText(default)
            examples = option.get('Examples') or []
            use_choices = bool(examples and not secret)
            self.answer.setVisible(not use_choices)
            self.choices.setVisible(use_choices)
            self.choices.clear()
            self.choices.setEditable(not bool(option.get('Exclusive')))
            for example in examples:
                value = str(example.get('Value', ''))
                label = example.get('Help', '').replace('\n', ' ')
                self.choices.addItem(value + (' — ' + label if label else ''), value)
            selected = self.choices.findData(default)
            if selected >= 0:
                self.choices.setCurrentIndex(selected)
            elif use_choices and self.choices.isEditable():
                self.choices.setCurrentIndex(-1)
                self.choices.setEditText(default)
            self.status.setText(setup_error(result['Error']) if result.get('Error') else ('A value is required.' if option.get('Required') else 'Optional: keep the default unless your provider requires a change.'))
        self.worker = self.window.run(work, done, failed)

    def finish_account(self):
        self.service.finish_account(self.identity, self.name.text(), self.provider.currentText())
        if self.editing:
            if getattr(self.window, 'active', None) or any(
                    worker is not self.worker for worker in getattr(self.window, 'workers', ())):
                raise RuntimeError('Wait for current operations to finish, then select Save.')
            self.service.commit_edit()
            self.service.discard_edit()

    def save_completed(self):
        try:
            self.finish_account()
            self.completed = True
            self.accept()
        except (ValueError, RuntimeError, OSError) as error:
            self.status.setText(str(error))

    def cleanup(self):
        if self.editing:
            self.service.discard_edit()
            return
        try:
            self.service.call('config/delete', {'name': 'c' + self.identity}, timeout=5)
        except (OSError, RuntimeError):
            pass

    def reject(self):
        if self.busy:
            self.cancel_event.set()
            self.cancel.setEnabled(False)
            self.status.setText('Cancelling account setup…')
            return
        if (self.started or self.editing) and not self.completed:
            self.cleanup()
        super().reject()

    def closeEvent(self, event):
        if self.busy:
            self.reject()
            event.ignore()
        else:
            super().closeEvent(event)


class CloudAccountTest(QDialog):
    def __init__(self, window, identity, parent=None):
        super().__init__(parent or window)
        self.window = window
        self.identity = identity
        self.busy = False
        self.closing = False
        self.cancel_event = Event()
        account = window.cloud_service.accounts()[identity]
        self.setWindowTitle('Test Cloud Connection')
        self.resize(600, 260)
        outer = QVBoxLayout(self)
        heading = QLabel(account['name'] + ' (' + account['provider'] + ')')
        heading.setTextFormat(Qt.TextFormat.PlainText)
        outer.addWidget(heading)
        self.path = QComboBox()
        self.path.setEditable(True)
        self.path.setAccessibleName('Remote path to test')
        roots = list(dict.fromkeys(site.get('root') or '/' for site in window.sites.values()
            if site.get('cloud_account') == identity))
        self.path.addItems(roots or ['/'])
        form = QFormLayout()
        form.addRow('Remote path', self.path)
        outer.addLayout(form)
        help_text = QLabel('Tests account access by listing this folder. For public object storage, '
            'enter /bucket-or-container/folder rather than /. Saved-site paths are offered above. '
            'This test does not upload or change remote files.')
        help_text.setWordWrap(True)
        outer.addWidget(help_text)
        self.status = QLabel('Ready to test.')
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        outer.addWidget(self.status)
        outer.addStretch()
        buttons = QHBoxLayout()
        self.test_button = QPushButton('Test Connection')
        self.test_button.clicked.connect(self.start_test)
        self.cancel_button = QPushButton('Cancel Test')
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_test)
        close = QPushButton('Close')
        close.clicked.connect(self.reject)
        for button in (self.test_button, self.cancel_button, close):
            buttons.addWidget(button)
        outer.addLayout(buttons)

    def start_test(self):
        if self.busy:
            return
        try:
            remote = cloud_path(self.path.currentText())
            if self.identity not in self.window.cloud_service.accounts():
                raise ValueError('The cloud account no longer exists.')
        except (ValueError, OSError) as error:
            self.status.setText(str(error))
            return
        self.cancel_event = Event()
        self.busy = True
        self.path.setEnabled(False)
        self.test_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.status.setText('Connecting and checking folder access…')
        def work(_):
            return self.window.cloud_service.job('operations/list', {
                'fs': 'c' + self.identity + ':', 'remote': remote,
                'opt': {'recurse': False}}, cancel=self.cancel_event, timeout=60)
        def finish(message):
            self.busy = False
            self.path.setEnabled(True)
            self.test_button.setEnabled(True)
            self.cancel_button.setEnabled(False)
            self.status.setText('Test cancelled.' if self.cancel_event.is_set() else message)
            if self.closing:
                super(CloudAccountTest, self).reject()
        def done(result):
            finish(f"Connection successful. Listed {len(result.get('list') or [])} entries. "
                   'Read access verified; write access was not tested.')
        self.window.run(work, done, lambda error: finish('Connection failed: ' + str(error)))

    def cancel_test(self):
        if self.busy:
            self.cancel_event.set()
            self.cancel_button.setEnabled(False)
            self.status.setText('Cancelling test…')

    def reject(self):
        if self.busy:
            self.closing = True
            self.cancel_test()
            return
        super().reject()

    def closeEvent(self, event):
        if self.busy:
            self.reject()
            event.ignore()
        else:
            super().closeEvent(event)


class CloudAccounts(QDialog):
    def __init__(self, window, parent=None):
        super().__init__(parent or window)
        self.window = window
        self.setWindowTitle('Cloud Accounts')
        self.resize(680, 430)
        outer = QVBoxLayout(self)
        self.tree = QTreeWidget()
        self.tree.setAccessibleName('Cloud accounts')
        self.tree.setHeaderLabels(['Account', 'Provider'])
        self.tree.setRootIsDecorated(False)
        self.tree.itemDoubleClicked.connect(lambda *_: self.edit())
        outer.addWidget(self.tree)
        buttons = QHBoxLayout()
        for label, callback in (('Add…', self.add), ('Edit…', self.edit), ('Test Connection…', self.test_connection), ('Remove', self.remove), ('Close', self.accept)):
            button = QPushButton(label)
            button.clicked.connect(callback)
            if label == 'Edit…':
                self.edit_button = button
            if label == 'Test Connection…':
                self.test_button = button
            buttons.addWidget(button)
        outer.addLayout(buttons)
        self.tree.itemSelectionChanged.connect(self.update_selection)
        self.refresh()

    def update_selection(self):
        selected = self.tree.currentItem() is not None
        self.edit_button.setEnabled(selected)
        self.test_button.setEnabled(selected)

    def test_connection(self):
        row = self.tree.currentItem()
        if row is None:
            return
        try:
            CloudAccountTest(self.window, row.data(0, Qt.ItemDataRole.UserRole), self).exec()
        except (KeyError, ValueError, OSError) as error:
            QMessageBox.warning(self, 'Test Cloud Connection', str(error))

    def refresh(self):
        selected = self.tree.currentItem()
        identity_before = selected.data(0, Qt.ItemDataRole.UserRole) if selected else None
        self.tree.clear()
        self.edit_button.setEnabled(False)
        self.test_button.setEnabled(False)
        try:
            for identity, account in self.window.cloud_service.accounts().items():
                row = QTreeWidgetItem([account['name'], account['provider']])
                row.setData(0, Qt.ItemDataRole.UserRole, identity)
                self.tree.addTopLevelItem(row)
                if identity == identity_before:
                    self.tree.setCurrentItem(row)
            self.tree.resizeColumnToContents(0)
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, 'Cloud Accounts', str(error))

    def add(self):
        if CloudAccountWizard(self.window, self).exec():
            self.refresh()

    def edit(self):
        row = self.tree.currentItem()
        if row is None:
            return
        if getattr(self.window, 'active', None) or getattr(self.window, 'workers', None):
            QMessageBox.information(self, 'Edit cloud account', 'Wait for current operations to finish before editing a cloud account.')
            return
        try:
            dialog = CloudAccountWizard(self.window, self, identity=row.data(0, Qt.ItemDataRole.UserRole))
            if dialog.exec():
                self.refresh()
        except (ValueError, RuntimeError, OSError) as error:
            QMessageBox.warning(self, 'Edit cloud account', str(error))

    def remove(self):
        row = self.tree.currentItem()
        if row is None:
            return
        identity = row.data(0, Qt.ItemDataRole.UserRole)
        if any(site.get('cloud_account') == identity for site in self.window.sites.values()):
            QMessageBox.warning(self, 'Cloud Accounts', 'This account is used by a saved site. Change or remove that site first.')
            return
        if QMessageBox.question(self, 'Remove cloud account', 'Remove this account’s authorization from FluxFTP? Remote files will not be deleted.') != QMessageBox.StandardButton.Yes:
            return
        self.window.run(lambda _: self.window.cloud_service.remove_account(identity), lambda _: self.refresh())
