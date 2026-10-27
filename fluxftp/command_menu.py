"""Server command menu, review/send dialog, and editable command templates."""
import json
import re

from PySide6.QtCore import QObject, Qt
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPlainTextEdit,
    QDialogButtonBox, QInputDialog, QLineEdit, QTreeWidget, QTreeWidgetItem, QPushButton, QStyle)

from .command_catalog import BUILTIN_COMMANDS
from .drftpd_commands import DRFTPD_COMMANDS
from .core import Cancelled, PROTOCOLS
from .server_commands import (compile_commands, execute_commands, public_text,
    validate_lines, CREDENTIAL, TOKEN)


def catalog_entries(nodes, prefix=()):
    for node in nodes:
        path = (*prefix, node['name'])
        if 'children' in node:
            yield from catalog_entries(node['children'], path)
        else:
            yield '/'.join(path), node['lines']


class CommandMenu(QObject):
    def __init__(self, window, tools_menu):
        super().__init__(window)
        self.window = window
        self.menu = tools_menu.addMenu('Command')
        self.catalog = [*BUILTIN_COMMANDS, DRFTPD_COMMANDS]
        self.defaults = dict(catalog_entries(self.catalog))
        self.overrides = {}
        self.custom = {}
        try:
            saved = json.loads(window.preferences.value('Commands/templates', '{}', type=str))
            for key, target in (('overrides', self.overrides), ('custom', self.custom)):
                for name, lines in saved.get(key, {}).items():
                    if isinstance(name, str) and isinstance(lines, list) and all(isinstance(s, str) for s in lines):
                        validate_lines(lines)
                        target[name] = lines
        except (ValueError, TypeError, AttributeError):
            self.overrides.clear()
            self.custom.clear()
        self.rebuild()
        self.menu.aboutToShow.connect(self.update)
        self.designer_action = tools_menu.addAction('Command Designer…', self.designer)
        self.designer_action.setShortcut('F4')
        self.raw_action = tools_menu.addAction('Send Raw Command…', self.raw)
        self.raw_action.setShortcut('Ctrl+R')
        tools_menu.addSeparator()
        tools_menu.aboutToShow.connect(self.update)

    def rebuild(self):
        self.menu.clear()
        self.actions = {}
        def add_nodes(menu, nodes, prefix=()):
            if menu is not self.menu:
                menu.aboutToShow.connect(self.update)
            for node in nodes:
                path = (*prefix, node['name'])
                if 'children' in node:
                    sub = menu.addMenu(node['name'])
                    sub.setIcon(self.window.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon))
                    add_nodes(sub, node['children'], path)
                else:
                    key = '/'.join(path)
                    action = menu.addAction(node['name'], lambda checked=False, key=key: self.invoke(key))
                    self.actions[key] = action
        add_nodes(self.menu, self.catalog)
        if self.custom:
            self.menu.addSeparator()
            custom_menu = self.menu.addMenu('Custom')
            custom_menu.aboutToShow.connect(self.update)
            for name in self.custom:
                key = 'Custom/' + name
                self.actions[key] = custom_menu.addAction(name, lambda checked=False, key=key: self.invoke(key))

    def snapshot(self):
        snapshot = self.window.ftp_commands.snapshot()
        return snapshot if snapshot and snapshot[1].protocol in (*PROTOCOLS[:3], PROTOCOLS[4]) else None

    def valid(self, snapshot):
        return self.window.ftp_commands.valid(snapshot) and snapshot[0].selected() == snapshot[5]

    def lines(self, key):
        if key.startswith('Custom/'):
            return self.custom[key[7:]]
        return self.overrides.get(key, self.defaults[key])

    def update(self):
        snapshot = self.snapshot()
        for key, action in self.actions.items():
            text = '\n'.join(self.lines(key))
            ready = bool(snapshot)
            if '%f' in text or '%o' in text:
                ready = ready and bool(snapshot[5])
            if ready and '%o' in text:
                ready = all(entry.user for entry in snapshot[5])
            action.setEnabled(ready)
        self.raw_action.setEnabled(bool(snapshot))
        if hasattr(self.window, 'ftp_command_input'):
            self.window.ftp_command_input.setEnabled(bool(snapshot))
            self.window.ftp_command_send.setEnabled(bool(snapshot))
            target = (f'Pane {snapshot[0].number}: {snapshot[1].name} — {snapshot[3]}' if snapshot
                      else 'Select a connected FTP or FTPS pane to send a command.')
            self.window.ftp_command_input.setToolTip(target)
            self.window.ftp_command_send.setToolTip(target)

    def prompt(self, question, secret):
        value, accepted = QInputDialog.getText(self.window, 'Command argument', question,
            QLineEdit.EchoMode.Password if secret else QLineEdit.EchoMode.Normal)
        return value if accepted else None

    def invoke(self, key):
        snapshot = self.snapshot()
        if snapshot:
            self.prepare(snapshot, key, self.lines(key))

    def raw(self):
        snapshot = self.snapshot()
        if not snapshot:
            return
        text, accepted = QInputDialog.getMultiLineText(self.window, 'Send Raw Command',
            'FTP commands, one per line. No command history is saved.\nUse the transfer controls for uploads/downloads.')
        if accepted and text.strip():
            self.prepare(snapshot, 'Raw FTP commands', text.splitlines(), raw=True)

    def inline_raw(self):
        snapshot = self.snapshot()
        text = self.window.ftp_command_input.currentText().strip()
        if not snapshot or not text:
            return
        side = int(snapshot[0].number)
        def finished(result):
            for line in result.splitlines():
                if line.startswith('> '):
                    line = 'FTP ' + line
                elif line[:1].isdigit():
                    line = 'FTP < ' + line
                elif line.startswith('Stopped after'):
                    line = 'ERROR: ' + line
                self.window.append_ftp_message(line, side)
        if self.prepare(snapshot, 'Raw FTP command', [text], raw=True, finished=finished):
            self.window.ftp_command_input.setEditText('')

    def prepare(self, snapshot, title, lines, raw=False, finished=None):
        try:
            # Literal percent signs in raw commands are not template syntax.
            plan = compile_commands([line.replace('%', '%%') for line in lines] if raw else lines,
                                    snapshot[5], self.prompt)
        except Cancelled:
            return
        except ValueError as error:
            self.window.error(str(error))
            return
        if not self.valid(snapshot):
            self.window.statusBar().showMessage('The pane or selection changed. Choose the command again.', 6000)
            return
        if not self.review(snapshot, title, plan) or not self.valid(snapshot):
            return
        def task(connection, cancel):
            return execute_commands(connection, snapshot[3], plan, cancel)
        self.window.ftp_commands.run(snapshot, title, task,
            finished or (lambda result: self.window.ftp_commands.report(title, result)), mutation=True)
        return True

    def review(self, snapshot, title, plan):
        dialog = QDialog(self.window)
        dialog.setWindowTitle(title)
        dialog.resize(740, 440)
        layout = QVBoxLayout(dialog)
        destination = QLabel(f'Send to {snapshot[1].name} ({snapshot[1].host})\nDirectory: {snapshot[3]}')
        destination.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(destination)
        text = QPlainTextEdit(public_text('\n'.join(plan.lines), plan.secrets))
        text.setReadOnly(True)
        layout.addWidget(text)
        layout.addWidget(QLabel('Commands run in order on a separate connection and stop on failure.\n'
            'Earlier changes are not rolled back. The server controls which commands your account may use.'))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('Send')
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        return dialog.exec() == QDialog.DialogCode.Accepted

    def save(self):
        self.window.preferences.setValue('Commands/templates', json.dumps(
            {'overrides': self.overrides, 'custom': self.custom}))
        self.rebuild()

    def designer(self):
        dialog = QDialog(self.window)
        dialog.setWindowTitle('Command Designer')
        dialog.resize(950, 620)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel('Edit command templates; Save changes applies immediately.\n'
            '%d[prompt] asks for an argument; %1/%2 reuse the first/second answer; '
            '%f is each selected name; %o is its owner; %% is a literal %.'))
        search = QLineEdit()
        search.setPlaceholderText('Find a command…')
        layout.addWidget(search)
        body = QHBoxLayout()
        tree = QTreeWidget()
        tree.setHeaderLabel('Server / command')
        editor = QPlainTextEdit()
        body.addWidget(tree, 1)
        body.addWidget(editor, 1)
        layout.addLayout(body)
        selected = [None]
        def fill():
            tree.clear()
            groups = {}
            for key in [*self.defaults, *('Custom/' + name for name in self.custom)]:
                parts = key.split('/')
                parent = tree.invisibleRootItem()
                for index, part in enumerate(parts[:-1]):
                    prefix = '/'.join(parts[:index+1])
                    if prefix not in groups:
                        groups[prefix] = QTreeWidgetItem(parent, [part])
                    parent = groups[prefix]
                row = QTreeWidgetItem(parent, [parts[-1]])
                row.setData(0, Qt.ItemDataRole.UserRole, key)
        def choose(current, previous):
            key = current.data(0, Qt.ItemDataRole.UserRole) if current else None
            selected[0] = key
            editor.setPlainText('\n'.join(self.lines(key)) if key else '')
            editor.setEnabled(bool(key))
        def apply():
            key = selected[0]
            if not key:
                return
            lines = editor.toPlainText().splitlines()
            try:
                validate_lines(lines)
                for line in lines:
                    credential = CREDENTIAL.search(line) or re.match(r'^SITE\s+CHANGE\s+.*?\bPASSWD\b', line, re.I)
                    if credential and TOKEN.sub('', line[credential.end():]).strip():
                        raise ValueError('Use prompted arguments for credential commands; do not save literal credentials.')
                compile_commands(lines, [type('Item', (), {'name': 'file', 'user': 'owner'})()], lambda *_: 'argument')
            except ValueError as error:
                self.window.error(str(error))
                return
            if key.startswith('Custom/'):
                self.custom[key[7:]] = lines
            else:
                self.overrides[key] = lines
            self.save()
            self.window.statusBar().showMessage('Command template saved.', 5000)
        def reset():
            key = selected[0]
            if not key:
                return
            if key.startswith('Custom/'):
                self.custom.pop(key[7:], None)
            else:
                self.overrides.pop(key, None)
            self.save()
            fill()
        def add():
            name, accepted = QInputDialog.getText(dialog, 'Add command', 'Command name:')
            name = name.strip()
            if accepted and name and '/' not in name:
                if name in self.custom:
                    self.window.error('A custom command already uses this name.')
                    return
                self.custom[name] = ['SITE HELP']
                self.save()
                fill()
        def filter_tree():
            needle = search.text().casefold()
            def visit(row, prefix=''):
                path = prefix + '/' + row.text(0).casefold()
                matches = [visit(row.child(i), path) for i in range(row.childCount())]
                visible = any(matches) if matches else needle in path
                row.setHidden(not visible)
                if needle and matches:
                    row.setExpanded(True)
                return visible
            for i in range(tree.topLevelItemCount()):
                visit(tree.topLevelItem(i))
        tree.currentItemChanged.connect(choose)
        search.textChanged.connect(filter_tree)
        actions = QHBoxLayout()
        for label, callback in [('Add custom command', add), ('Save changes', apply),
                                ('Restore default / Delete custom', reset)]:
            button = QPushButton(label)
            button.clicked.connect(callback)
            actions.addWidget(button)
        layout.addLayout(actions)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(dialog.reject)
        layout.addWidget(close)
        fill()
        dialog.exec()
