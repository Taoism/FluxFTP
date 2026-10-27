"""Site property pages and reusable list editors."""
import json
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QGroupBox, QCheckBox, QComboBox, QSpinBox, QLineEdit, QLabel, QPushButton,
    QTreeWidget, QTreeWidgetItem, QDialog, QDialogButtonBox, QMessageBox,
    QFileDialog, QScrollArea)

from .site_options import decode
from .ascii_rules import validate_rule, read_rules


class RecordEditor(QWidget):
    def __init__(self, kind, parent=None):
        super().__init__(parent)
        self.kind = kind
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.tree = QTreeWidget()
        self.tree.setAccessibleName('Site bookmarks' if kind == 'bookmarks' else 'Site ' + kind.replace('_', ' ') + ' rules')
        self.tree.setRootIsDecorated(False)
        self.tree.setHeaderLabels(['Name', 'Remote Path', 'Local Path'] if kind == 'bookmarks' else ['Mask', 'Regular expression'])
        layout.addWidget(self.tree, 1)
        buttons = QVBoxLayout()
        self.buttons = {}
        actions = [('Add', lambda: self.edit(False)), ('Edit', lambda: self.edit(True)), ('Delete', self.delete)]
        if kind == 'priority':
            actions += [('Move Up', lambda: self.move(-1)), ('Move Down', lambda: self.move(1))]
        if kind != 'bookmarks':
            actions += [('Export', self.export), ('Import', self.import_file)]
        for title, callback in actions:
            button = QPushButton(title)
            button.clicked.connect(callback)
            buttons.addWidget(button)
            self.buttons[title] = button
        buttons.addStretch()
        layout.addLayout(buttons)
        self.tree.itemSelectionChanged.connect(self.selection)
        self.tree.itemDoubleClicked.connect(lambda *_: self.edit(True))
        self.selection()

    def selection(self):
        row = self.tree.currentItem()
        for name in ('Edit', 'Delete', 'Move Up', 'Move Down'):
            if name in self.buttons:
                self.buttons[name].setEnabled(row is not None)

    def records(self):
        return [self.tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole) for i in range(self.tree.topLevelItemCount())]

    def load(self, records):
        self.tree.clear()
        for record in records:
            values = [record['name'], record['path'], record.get('local', '')] if self.kind == 'bookmarks' else [record['pattern'], 'Yes' if record['regex'] else 'No']
            row = QTreeWidgetItem(values)
            row.setData(0, Qt.ItemDataRole.UserRole, dict(record))
            self.tree.addTopLevelItem(row)
        self.selection()

    def edit(self, editing):
        row = self.tree.currentItem() if editing else None
        if editing and row is None:
            return
        record = row.data(0, Qt.ItemDataRole.UserRole) if row else {}
        dialog = QDialog(self)
        dialog.setWindowTitle(('Edit' if editing else 'Add') + (' Bookmark' if self.kind == 'bookmarks' else ' Rule'))
        dialog.resize(460, 200)
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        controls = {}
        keys = [('Name', 'name'), ('Remote Path', 'path'), ('Local Path', 'local')] if self.kind == 'bookmarks' else [('Mask', 'pattern')]
        for label, key in keys:
            controls[key] = QLineEdit(record.get(key, '/' if key == 'path' else ''))
            form.addRow(label, controls[key])
        regex = QCheckBox('Regular expression')
        regex.setChecked(record.get('regex', False))
        if self.kind != 'bookmarks':
            form.addRow(regex)
        layout.addLayout(form)
        status = QLabel()
        layout.addWidget(status)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        def accept():
            try:
                value = {key: control.text().strip() for key, control in controls.items()}
                if self.kind == 'bookmarks':
                    decode(json.dumps({'Site/bookmarks': [value]}))
                else:
                    value = validate_rule(value['pattern'], regex.isChecked())
                records = self.records()
                if row:
                    records[self.tree.indexOfTopLevelItem(row)] = value
                else:
                    records.append(value)
                self.load(records)
                dialog.accept()
            except ValueError as error:
                status.setText(str(error))
        buttons.accepted.connect(accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def delete(self):
        row = self.tree.currentItem()
        if row:
            self.tree.takeTopLevelItem(self.tree.indexOfTopLevelItem(row))

    def move(self, delta):
        row = self.tree.currentItem()
        if row:
            index = self.tree.indexOfTopLevelItem(row)
            if 0 <= index + delta < self.tree.topLevelItemCount():
                self.tree.takeTopLevelItem(index)
                self.tree.insertTopLevelItem(index + delta, row)
                self.tree.setCurrentItem(row)

    def export(self):
        path, _ = QFileDialog.getSaveFileName(self, 'Export rules', '', 'JSON (*.json)')
        if path:
            try:
                Path(path).write_text(json.dumps(self.records(), indent=2), encoding='utf-8')
            except OSError as error:
                QMessageBox.warning(self, 'Export failed', str(error))

    def import_file(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Import rules', '', 'JSON (*.json)')
        if path:
            try:
                if Path(path).stat().st_size > 65536:
                    raise ValueError('Rule file is too large.')
                self.load(read_rules(Path(path).read_text(encoding='utf-8')))
            except (OSError, ValueError) as error:
                QMessageBox.warning(self, 'Import failed', str(error))


class PropertyPages:
    def __init__(self, dialog):
        self.dialog = dialog
        self.controls = {}
        self.lists = {}

    def page(self, title):
        page, layout = self.dialog._property_page(title)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        content = QWidget()
        body = QVBoxLayout(content)
        body.setContentsMargins(2, 4, 6, 4)
        scroll.setWidget(content)
        layout.addWidget(scroll)
        return page, body

    @staticmethod
    def note(layout, text):
        label = QLabel(text)
        label.setWordWrap(True)
        layout.addWidget(label)

    def choice(self, form, label, key, choices):
        control = QComboBox()
        control.addItem('Use Global Setting', None)
        for title, value in choices:
            control.addItem(title, value)
        form.addRow(label, control)
        self.controls[key] = control
        return control

    def boolean(self, form, label, key):
        return self.choice(form, label, key, [('Yes', True), ('No', False)])

    def number(self, form, label, key, default, maximum=3600, minimum=0):
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        override = QCheckBox('Override')
        value = QSpinBox()
        value.setRange(minimum, maximum)
        value.setValue(default)
        value.setEnabled(False)
        override.toggled.connect(value.setEnabled)
        layout.addWidget(override)
        layout.addWidget(value)
        form.addRow(label, row)
        self.controls[key] = (override, value, default)

    def text(self, form, label, key):
        control = QLineEdit()
        form.addRow(label, control)
        self.controls[key] = control
        return control

    @staticmethod
    def form(layout, title):
        group = QGroupBox(title)
        form = QFormLayout(group)
        layout.addWidget(group)
        return form

    @staticmethod
    def unavailable(form, label, checked=False):
        control = QCheckBox(label)
        control.setChecked(checked)
        control.setEnabled(False)
        control.setToolTip('Not configurable in this build.')
        form.addRow(control)
        return control

    def build(self, name):
        page, layout = self.page(name)
        if name == 'Bookmark':
            self.lists['Site/bookmarks'] = RecordEditor('bookmarks')
            layout.addWidget(self.lists['Site/bookmarks'])
        elif name == 'URL':
            form = self.form(layout, 'HTTP URL Mapping')
            self.text(form, 'HTTP URL', 'Site/http_url').setPlaceholderText('https://example.com/files')
            self.text(form, 'FTP Web Root Path', 'Site/web_root').setPlaceholderText('/public_html')
            self.note(layout, 'Use Copy Web URL in the remote file menu for files under this root.')
            layout.addWidget(QLabel('Connection URL (without password)'))
            self.dialog.url_preview = QLineEdit()
            self.dialog.url_preview.setAccessibleName('Site URL preview')
            self.dialog.url_preview.setReadOnly(True)
            layout.addWidget(self.dialog.url_preview)
            for field in ('host', 'username', 'root'):
                self.dialog.fields[field].textChanged.connect(self.dialog._update_url_preview)
            self.dialog.protocol.currentTextChanged.connect(self.dialog._update_url_preview)
        elif name == 'Display':
            form = self.form(layout, 'General')
            self.choice(form, 'Pane title', 'Site/show_name', [('Use site name', True), ('Use left/right indicator', False)])
        elif name == 'Connection':
            form = self.form(layout, 'Connection')
            self.number(form, 'Max retries', 'Connection/max_retries', 2, 100)
            self.number(form, 'Connect retry delay (seconds)', 'Connection/retry_delay', 3)
            self.number(form, 'Connection timeout (seconds)', 'Connection/connect_timeout', 30, minimum=1)
            self.number(form, 'FTP reply timeout (seconds)', 'Connection/command_timeout', 200, minimum=1)
            form = self.form(layout, 'Keep Alive')
            self.boolean(form, 'TCP keep alive', 'Connection/tcp_keepalive')
            self.boolean(form, 'Reuse browsing connections', 'Connection/persistent_browsing')
            self.boolean(form, 'Idle FTP/FTPS NOOP', 'Connection/ftp_keepalive')
            self.number(form, 'Idle NOOP interval (seconds)', 'Connection/ftp_keepalive_interval', 60, 3600, 5)
            self.number(form, 'Interval (seconds)', 'Connection/keepalive_interval', 30, minimum=1)
            form = self.form(layout, 'FEAT Option')
            self.boolean(form, 'Send FEAT command', 'Connection/send_feat')
            self.boolean(form, 'Use MLSD if available', 'Connection/use_mlsd')
            self.note(layout, 'Resume support is detected during transfer. Unsupported resume follows the File Exists policy.')
        elif name == 'Account':
            owner = self.dialog.key_owner()
            choices = [(name, name) for name in sorted(owner.sites)] if owner else []
            form = self.form(layout, 'Transfer accounts')
            for route in ('upload', 'download'):
                self.choice(form, route.title() + ' account', 'Account/' + route + '_site', choices).setItemText(0, 'Use main login')
            self.note(layout, 'Choose a saved site with the same host, port and protocol and a different username/password. Browsing keeps the main login. Transfer connections use the selected account credentials; paths and other settings stay with this site. Chained account overrides are not followed.')
        elif name == 'Ident':
            form = self.form(layout, 'Ident identity')
            self.text(form, 'Name (blank inherits global)', 'Connection/ident_name')
            self.text(form, 'System (blank inherits global)', 'Connection/ident_system')
            self.note(layout, 'Uses the global Ident listener and port. Enable Ident in Options → Connection → Ident. Each connection returns its own site identity; proxied connections are excluded.')
        elif name == 'Proxy Server':
            form = self.form(layout, 'Proxy Server')
            self.choice(form, 'Connection proxy', 'Connection/proxy_enabled', [('Bypass proxy server', False), ('Enable proxy', True)])
            owner = self.dialog.key_owner()
            profiles = json.loads(owner.preferences.value('Connection/proxy_profiles', '[]', type=str)) if owner else []
            self.choice(form, 'Saved proxy profile', 'Connection/proxy_profile', [(p['name'], p['id']) for p in profiles])
            self.note(layout, 'Create profiles and save their passwords in Options → Connection → Proxy Server. Selecting a profile enables it unless Bypass is selected. Global address exceptions still apply.')
        elif name == 'List Mode':
            form = self.form(layout, 'General')
            from .listing_options import ENCODINGS, MODES
            self.choice(form, 'FTP filename encoding', 'Connection/filename_encoding', [(v, v) for v in MODES])
            self.choice(form, 'Automatic fallback', 'Connection/encoding_fallback', [(v, v) for v in ENCODINGS])
            self.number(form, 'SFTP read-ahead requests', 'Connection/sftp_read_aheads', 50, 128, 1)
            self.number(form, 'Rows per UI batch', 'Connection/list_batch_size', 300, 2000, 100)
            self.boolean(form, 'Initially keep server order (skip sorting)', 'Connection/list_server_order')
            self.boolean(form, 'MLSD essential facts only (type, size, date)', 'Connection/mlsd_essential')
            self.choice(form, 'Directory listing', 'Connection/list_mode', [(v, v) for v in ('Default (LIST)', 'LIST only', 'Show Hidden Files', 'Custom', 'STAT', 'STAT -l')])
            self.text(form, 'Custom LIST command', 'Connection/list_command').setPlaceholderText('LIST -al')
            self.boolean(form, 'Enable UPnP', 'General/upnp_enabled')
            self.note(layout, 'STAT listings use the control connection and require server support; STAT -l is a server-specific variant.')
            self.note(layout, 'Automatic uses UTF-8 when advertised by FEAT, otherwise the selected fallback. Forced UTF-8 takes priority. Explicit encodings affect FTP/FTPS names and paths; SFTP/cloud remain Unicode. Higher read-ahead may help distant SFTP servers; lower it for compatibility. Large listings load in short UI batches. Click a column to sort when using server order.')
            form = self.form(layout, 'Cache')
            self.boolean(form, 'Use directory cache', 'Site/cache')
        elif name == 'Transfer':
            form = self.form(layout, 'Filename Convert')
            for route, label in (('upload', 'When Uploading'), ('download', 'When Downloading'), ('fxp', 'When FXPing / relaying')):
                self.choice(form, label, 'Transfer/' + route + '_case', [(v, v) for v in ('Unchanged', 'Lowercase', 'Uppercase')])
            form = self.form(layout, 'XDUPE / FXP Method')
            self.boolean(form, 'Enable XDUPE', 'Transfer/xdupe').setItemText(0, 'Disabled (default)')
            self.choice(form, 'FXP data role', 'Transfer/fxp_role', [(v, v) for v in ('Auto', 'Passive', 'Active')]).setItemText(0, 'Auto (default)')
            self.note(layout, 'Auto uses a passive destination. Explicit roles must be complementary across the two sites. Protected FXP still requires SSCN or CPSV support.')
            form = self.form(layout, 'Uploading and Downloading')
            self.boolean(form, 'Force active mode transfer', 'Transfer/force_active')
            self.boolean(form, 'Atomic remote upload', 'Transfer/atomic_upload')
            self.choice(form, 'Verify file hashes', 'Transfer/hash_verification', [(v, v) for v in ('Off', 'When available', 'Required')])
            self.boolean(form, 'Send NOOP during transfer', 'Connection/transfer_noop')
            form = self.form(layout, 'TCP/IP Buffer Size')
            self.number(form, 'Send (KB)', 'Transfer/send_kib', 2048, 65536, 1)
            self.number(form, 'Receive (KB)', 'Transfer/receive_kib', 2048, 65536, 1)
        elif name in ('Skip List', 'Priority'):
            form = self.form(layout, 'General')
            if name == 'Skip List':
                self.boolean(form, 'Enable Skip List', 'Transfer/skip_enabled')
                self.boolean(form, 'Skip Empty Directory', 'Transfer/skip_empty')
            self.note(layout, 'Masks support wildcards and regular expressions. Allow rules take precedence over skip rules.' if name == 'Skip List' else 'First matching rule has priority. Rules run from top to bottom.')
            keys = [('Skip List', 'Transfer/skip_rules'), ('Allow List', 'Transfer/allow_rules')] if name == 'Skip List' else [('Priority', 'Transfer/priority_rules')]
            for title, key in keys:
                override = QCheckBox('Override global ' + title.lower())
                layout.addWidget(override)
                editor = RecordEditor('priority' if name == 'Priority' else 'rules')
                editor.setMinimumHeight(150)
                editor.setEnabled(False)
                override.toggled.connect(editor.setEnabled)
                layout.addWidget(editor)
                self.lists[key] = (override, editor)
        elif name == 'File Exists':
            self.note(layout, 'Use Global Setting inherits the current global rule for each comparison.')
            form = self.form(layout, 'Interactive conflicts')
            self.boolean(form, 'Ask before replacing each existing file', 'Transfer/conflict_prompt')
            for route, title in (('upload', 'Uploading'), ('download', 'Downloading'), ('fxp', 'FXP / cloud relay')):
                form = self.form(layout, title)
                for relation in ('smaller', 'same', 'larger'):
                    self.choice(form, 'Target file is ' + relation, f'Transfer/{route}_{relation}', [(v, v) for v in ('Auto Resume', 'Auto Skip', 'Auto Rename', 'Auto Overwrite', 'Ask')])
        elif name == 'Notification':
            form = self.form(layout, 'Notification')
            self.boolean(form, 'Monitor for new files and folders', 'Monitor/enabled').setItemText(0, 'Disabled (default)')
            self.text(form, 'Remote folder (blank uses site root)', 'Monitor/path')
            self.number(form, 'Poll interval (seconds)', 'Monitor/interval', 60, 86400, 30)
            self.note(layout, 'Read-only polling while FluxFTP or its background scheduler is running. The first listing establishes a baseline. Later new immediate children appear in the activity log and tray notification. No files are transferred or deleted. Save credentials and verify server trust first.')
        layout.addStretch()
        return page

    def load(self, raw):
        values = decode(raw)
        owner = self.dialog.key_owner()
        from .transfer_options import TransferOptions
        defaults = TransferOptions.from_settings(owner.preferences) if owner else TransferOptions()
        for key, control in self.controls.items():
            value = values.get(key)
            if isinstance(control, tuple):
                toggle, spin, default = control
                toggle.setChecked(value is not None)
                spin.setValue(default if value is None else value)
            elif isinstance(control, QComboBox):
                if value is not None and control.findData(value) < 0:
                    control.addItem(str(value) + ' (missing profile)', value)
                control.setCurrentIndex(max(0, control.findData(value)))
            else:
                control.setText(value or '')
        for key, control in self.lists.items():
            if isinstance(control, tuple):
                toggle, editor = control
                toggle.setChecked(key in values)
                editor.load(read_rules(values.get(key, getattr(defaults, key.split('/')[-1], '[]'))))
            else:
                control.load(values.get(key, []))

    def dump(self):
        values = {}
        for key, control in self.controls.items():
            if isinstance(control, tuple):
                value = control[1].value() if control[0].isChecked() else None
            elif isinstance(control, QComboBox):
                value = control.currentData()
            else:
                value = control.text().strip() or None
            if value is not None:
                values[key] = value
        for key, control in self.lists.items():
            if isinstance(control, tuple):
                if control[0].isChecked():
                    values[key] = json.dumps(control[1].records())
            else:
                values[key] = control.records()
        result = json.dumps(values, ensure_ascii=False)
        decode(result)
        return result
