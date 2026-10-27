"""Ordered filename associations and their preferences controls."""
import fnmatch
import json
import os
from pathlib import Path

from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
    QTreeWidget, QTreeWidgetItem, QHeaderView, QPushButton, QDialog, QFormLayout,
    QLineEdit, QFileDialog, QDialogButtonBox, QMessageBox, QLabel)


def validate_rule(rule):
    if not isinstance(rule, dict) or set(rule) != {'masks', 'program'}:
        raise ValueError('An association needs file masks and an application path.')
    if not all(isinstance(rule[key], str) for key in rule):
        raise ValueError('Association values must be text.')
    masks = [mask.strip() for mask in rule['masks'].split(';') if mask.strip()]
    if not masks or any('/' in mask or '\\' in mask or any(ord(c) < 32 for c in mask) for mask in masks):
        raise ValueError('Enter filename masks, separated by semicolons, without folder paths.')
    program = rule['program'].strip()
    if not program or any(ord(c) < 32 for c in program) or not Path(program).is_absolute():
        raise ValueError('Choose an application using its full path.')
    return dict(masks=';'.join(masks), program=program)


def read_rules(settings, kind):
    raw = json.loads(settings.value('Associations/' + kind, '[]', type=str))
    if not isinstance(raw, list) or len(raw) > 1000:
        raise ValueError('Invalid association list.')
    return [validate_rule(rule) for rule in raw]


def matching_rule(settings, kind, name):
    for rule in read_rules(settings, kind):
        if any(fnmatch.fnmatchcase(name.casefold(), ('*' if mask == '*.*' else mask).casefold())
               for mask in rule['masks'].split(';')):
            return rule
    return None


class AssociationEditor(QDialog):
    def __init__(self, parent, rule=None):
        super().__init__(parent)
        self.setWindowTitle('File association')
        self.resize(620, 160)
        form = QFormLayout(self)
        self.masks = QLineEdit(rule['masks'] if rule else '*.*')
        self.program = QLineEdit(rule['program'] if rule else '')
        form.addRow('File masks', self.masks)
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.program, 1)
        browse = QPushButton('Browse…')
        browse.clicked.connect(self.browse)
        layout.addWidget(browse)
        form.addRow('Open with', row)
        form.addRow(QLabel('Use semicolons to separate masks. The file is passed to the application as one argument.'))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.validate)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def browse(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Choose application', self.program.text(), 'Applications (*.exe);;All files (*)')
        if path:
            self.program.setText(path)

    def validate(self):
        try:
            self.rule = validate_rule(dict(masks=self.masks.text(), program=self.program.text()))
            if not Path(self.rule['program']).is_file():
                raise ValueError('The selected application does not exist.')
            if os.name == 'nt' and Path(self.rule['program']).suffix.casefold() not in ('.exe', '.com'):
                raise ValueError('Choose an executable application (.exe or .com), not a shell script.')
        except ValueError as error:
            QMessageBox.warning(self, 'Invalid association', str(error))
            return
        self.accept()


class AssociationList(QGroupBox):
    def __init__(self, settings, kind, parent=None):
        super().__init__('View File' if kind == 'view' else 'Edit File', parent)
        self.settings, self.kind = settings, kind
        layout = QVBoxLayout(self)
        self.tree = QTreeWidget()
        self.tree.setRootIsDecorated(False)
        self.tree.setHeaderLabels(['File mask(*.*)', 'Open with'])
        self.tree.header().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.tree.setMinimumHeight(120)
        layout.addWidget(self.tree)
        buttons = QHBoxLayout()
        self.buttons = {}
        for label, callback in [('Add', lambda: self.edit(False)), ('Edit', lambda: self.edit(True)),
                                ('Delete', self.delete), ('Move Up', lambda: self.move(-1)),
                                ('Move Down', lambda: self.move(1))]:
            button = QPushButton(label)
            button.clicked.connect(callback)
            buttons.addWidget(button)
            self.buttons[label] = button
        layout.addLayout(buttons)
        self.notice = QLabel()
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        self.invalid = False
        try:
            self.rules = read_rules(settings, kind)
        except (ValueError, TypeError) as error:
            self.rules, self.invalid = [], True
            self.notice.setText(f'Saved associations could not be read: {error} The saved data has been retained.')
        self.notice.setVisible(self.invalid)
        self.tree.itemSelectionChanged.connect(self.update_buttons)
        self.tree.itemDoubleClicked.connect(lambda *_: self.edit(True))
        self.refresh()

    def refresh(self, index=-1):
        self.tree.clear()
        for rule in self.rules:
            item = QTreeWidgetItem([rule['masks'], rule['program']])
            item.setToolTip(1, rule['program'])
            self.tree.addTopLevelItem(item)
        if 0 <= index < len(self.rules):
            self.tree.setCurrentItem(self.tree.topLevelItem(index))
        self.update_buttons()

    def update_buttons(self):
        index = self.tree.indexOfTopLevelItem(self.tree.currentItem())
        for label in ('Edit', 'Delete', 'Move Up', 'Move Down'):
            self.buttons[label].setEnabled(not self.invalid and index >= 0 and
                (label != 'Move Up' or index > 0) and (label != 'Move Down' or index < len(self.rules) - 1))
        self.buttons['Add'].setEnabled(not self.invalid)

    def save(self, index):
        self.settings.setValue('Associations/' + self.kind, json.dumps(self.rules, ensure_ascii=False))
        self.refresh(index)

    def edit(self, editing):
        index = self.tree.indexOfTopLevelItem(self.tree.currentItem())
        if self.invalid or (editing and index < 0):
            return
        editor = AssociationEditor(self, self.rules[index] if editing else None)
        if editor.exec():
            if editing:
                self.rules[index] = editor.rule
            else:
                self.rules.append(editor.rule)
                index = len(self.rules) - 1
            self.save(index)

    def delete(self):
        index = self.tree.indexOfTopLevelItem(self.tree.currentItem())
        if not self.invalid and index >= 0:
            del self.rules[index]
            self.save(min(index, len(self.rules) - 1))

    def move(self, delta):
        index = self.tree.indexOfTopLevelItem(self.tree.currentItem())
        target = index + delta
        if not self.invalid and index >= 0 and 0 <= target < len(self.rules):
            self.rules[index], self.rules[target] = self.rules[target], self.rules[index]
            self.save(target)
