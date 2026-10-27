"""Structured script designer and schedule management."""
import copy
import time
from pathlib import Path

from PySide6.QtCore import Qt, QDateTime
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QComboBox, QPlainTextEdit, QPushButton, QDialogButtonBox,
    QTreeWidget, QTreeWidgetItem, QMessageBox, QCheckBox, QSpinBox, QDateTimeEdit,
    QFileDialog)

from .automation import ACTIONS, validate_script


def note(text):
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


class StepDialog(QDialog):
    def __init__(self, window, parent, step=None):
        super().__init__(parent)
        self.window = window
        self.setWindowTitle('Script step')
        self.resize(680, 470)
        form = QFormLayout(self)
        self.action = QComboBox()
        self.action.addItems(ACTIONS)
        self.site = QComboBox()
        self.site.addItems(sorted(n for n, s in window.sites.items() if not s.get('_disabled')))
        self.remote = QLineEdit('/')
        self.local = QLineEdit()
        self.exists = QComboBox()
        self.exists.addItems(('Fail', 'Skip', 'Overwrite'))
        self.template = QComboBox()
        self.template.addItem('Choose a Command Designer template…', None)
        commands = window.command_commands
        for key in [*commands.defaults, *('Custom/' + n for n in commands.custom)]:
            self.template.addItem(key, key)
        self.lines = QPlainTextEdit()
        self.lines.setPlaceholderText('FTP commands, one per line. No prompts or selection placeholders.')
        form.addRow('Action', self.action)
        form.addRow('Saved site', self.site)
        form.addRow('Remote path', self.remote)
        local_row = QHBoxLayout()
        local_row.addWidget(self.local)
        self.browse = QPushButton('Browse…')
        self.browse.clicked.connect(self.browse_local)
        local_row.addWidget(self.browse)
        form.addRow('Local path', local_row)
        form.addRow('If destination exists', self.exists)
        form.addRow('Command template', self.template)
        form.addRow('Commands', self.lines)
        form.addRow(note('File transfers use full paths including filenames. Recursive folder transfers copy the source contents into the destination folder. '
            'Commands run in the remote directory. Imported templates are copied into the script; later template edits do not change it.'))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        for widget, label in ((self.action, 'Step action'), (self.site, 'Step saved site'), (self.remote, 'Step remote path'),
                              (self.local, 'Step local file'), (self.lines, 'Step FTP commands')):
            widget.setAccessibleName(label)
        self.template.currentIndexChanged.connect(self.load_template)
        self.action.currentTextChanged.connect(self.update_fields)
        if step:
            self.action.setCurrentText(step['action'])
            if self.site.findText(step['site']) < 0:
                self.site.addItem(step['site'])
            self.site.setCurrentText(step['site'])
            self.remote.setText(step['remote'])
            self.local.setText(step.get('local', ''))
            self.exists.setCurrentText(step.get('exists', 'Fail'))
            self.lines.setPlainText('\n'.join(step.get('lines', [])))
        self.update_fields()

    def update_fields(self):
        transfer = self.action.currentText() in ('Download file', 'Upload file', 'Download folder', 'Upload folder')
        for widget in (self.local, self.browse, self.exists):
            widget.setEnabled(transfer)
        for widget in (self.template, self.lines):
            widget.setEnabled(self.action.currentText() == 'FTP commands')

    def load_template(self):
        key = self.template.currentData()
        if key:
            self.lines.setPlainText('\n'.join(self.window.command_commands.lines(key)))

    def browse_local(self):
        if self.action.currentText().endswith('folder'):
            path = QFileDialog.getExistingDirectory(self, 'Local folder', self.local.text() or str(Path.home()))
            if path:
                self.local.setText(path)
            return
        function = QFileDialog.getOpenFileName if self.action.currentText() == 'Upload file' else QFileDialog.getSaveFileName
        path, _ = function(self, 'Local file', self.local.text() or str(Path.home()))
        if path:
            self.local.setText(path)

    def value(self):
        step = dict(action=self.action.currentText(), site=self.site.currentText(), remote=self.remote.text().strip(),
                    local=self.local.text().strip(), exists=self.exists.currentText(), lines=self.lines.toPlainText().splitlines())
        return validate_script(dict(name='Step', steps=[step]))['steps'][0]

    def accept(self):
        try:
            self.value()
        except ValueError as error:
            QMessageBox.warning(self, 'Invalid step', str(error))
            return
        super().accept()


class ScriptDialog(QDialog):
    def __init__(self, controller, identity=None, parent=None):
        super().__init__(parent or controller.window)
        self.controller, self.identity = controller, identity
        self.setWindowTitle('Script Designer')
        self.resize(840, 650)
        script = controller.scripts.get(identity, {})
        self.steps = copy.deepcopy(script.get('steps', []))
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit(script.get('name', ''))
        self.name.setMaxLength(120)
        form.addRow('Script name', self.name)
        layout.addLayout(form)
        layout.addWidget(note('Steps run in order and stop on failure. Earlier changes are not rolled back. '
            'File transfers use saved connection settings; they run separately from the transfer queue.'))
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(('Action', 'Saved site', 'Remote path', 'Local file / commands'))
        self.tree.setAccessibleName('Ordered script steps')
        self.tree.itemDoubleClicked.connect(lambda *_: self.edit_step())
        layout.addWidget(self.tree, 1)
        row = QHBoxLayout()
        for label, callback in (('Add step…', self.add_step), ('Edit step…', self.edit_step),
                                ('Remove', self.remove_step), ('Move up', lambda: self.move(-1)),
                                ('Move down', lambda: self.move(1))):
            button = QPushButton(label)
            button.clicked.connect(callback)
            row.addWidget(button)
        layout.addLayout(row)
        schedule = QFormLayout()
        self.enabled = QCheckBox('Enable this schedule')
        self.enabled.setChecked(script.get('enabled', False))
        self.when = QDateTimeEdit(QDateTime.fromSecsSinceEpoch(int(script.get('next_run') or time.time() + 3600)))
        self.when.setCalendarPopup(True)
        self.when.setDisplayFormat('yyyy-MM-dd HH:mm:ss')
        self.interval = QSpinBox()
        self.interval.setRange(0, 525600)
        self.interval.setSpecialValueText('Once')
        self.interval.setSuffix(' minutes')
        self.interval.setValue(script.get('interval', 0))
        from .calendar_schedule import MODES
        self.schedule = QComboBox()
        self.schedule.addItems(MODES)
        self.schedule.setCurrentText(script.get('schedule', 'Interval'))
        self.schedule.currentTextChanged.connect(lambda mode: self.interval.setEnabled(mode == 'Interval'))
        self.interval.setEnabled(self.schedule.currentText() == 'Interval')
        schedule.addRow(self.enabled)
        schedule.addRow('First run (local time)', self.when)
        schedule.addRow('Repeat every', self.interval)
        schedule.addRow('Calendar recurrence', self.schedule)
        layout.addLayout(schedule)
        layout.addWidget(note('Enable the Windows background task in Scheduled Automation to run when the desktop is closed (while signed in). Due scripts wait for other work and dialogs to finish. '
            'Missed runs are combined into one run when available. Repeats use elapsed minutes (1440 = 24 hours). '
            'Failure or interruption disables the schedule until you review and enable it again. '
            'Credentials stay in the saved site vault; establish server trust before scheduling.'))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.refresh()
        from .accessibility import label_forms, fit_dialog
        label_forms(self)
        fit_dialog(self)

    def refresh(self, index=0):
        self.tree.clear()
        for step in self.steps:
            QTreeWidgetItem(self.tree, [step['action'], step['site'], step['remote'],
                                      step.get('local', f'{len(step.get("lines", []))} commands' if step['action'] == 'FTP commands' else '')])
        if self.steps:
            self.tree.setCurrentItem(self.tree.topLevelItem(min(index, len(self.steps) - 1)))
        for column in range(3):
            self.tree.resizeColumnToContents(column)

    def index(self):
        return self.tree.indexOfTopLevelItem(self.tree.currentItem())

    def add_step(self):
        dialog = StepDialog(self.controller.window, self)
        if dialog.exec():
            self.steps.append(dialog.value())
            self.refresh(len(self.steps) - 1)
        dialog.deleteLater()

    def edit_step(self):
        index = self.index()
        if index < 0:
            return
        dialog = StepDialog(self.controller.window, self, self.steps[index])
        if dialog.exec():
            self.steps[index] = dialog.value()
            self.refresh(index)
        dialog.deleteLater()

    def remove_step(self):
        index = self.index()
        if index >= 0:
            self.steps.pop(index)
            self.refresh(max(0, index - 1))

    def move(self, direction):
        index = self.index()
        target = index + direction
        if index >= 0 and 0 <= target < len(self.steps):
            self.steps[index], self.steps[target] = self.steps[target], self.steps[index]
            self.refresh(target)

    def accept(self):
        try:
            self.identity = self.controller.put(self.identity, dict(name=self.name.text(), steps=self.steps,
                enabled=self.enabled.isChecked(), next_run=self.when.dateTime().toSecsSinceEpoch(), interval=self.interval.value(),
                schedule=self.schedule.currentText(), month_day=self.when.date().day(), wall_time=self.when.time().toString('HH:mm:ss')))
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, 'Cannot save script', str(error))
            return
        super().accept()


class AutomationDialog(QDialog):
    def __init__(self, controller):
        super().__init__(controller.window)
        self.controller = controller
        self.setWindowTitle('Scripts and Scheduled Automation')
        self.resize(930, 620)
        layout = QVBoxLayout(self)
        layout.addWidget(note('Design scripts, run them now, or schedule them. Enable the Windows background task to run while the desktop is closed and you remain signed in. '
            'Run Now executes the saved steps, including writes and commands, without changing the next scheduled time.'))
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(('Script', 'Next run', 'Repeat', 'Last result'))
        self.tree.setAccessibleName('Saved automation scripts')
        self.tree.currentItemChanged.connect(self.details)
        self.tree.itemDoubleClicked.connect(lambda *_: self.edit())
        layout.addWidget(self.tree)
        row = QHBoxLayout()
        self.buttons = {}
        for label, callback in (('New script…', self.new), ('Edit…', self.edit), ('Delete', self.remove),
                                ('Run Now', self.run), ('Cancel Run', controller.cancel)):
            button = QPushButton(label)
            button.clicked.connect(callback)
            self.buttons[label] = button
            row.addWidget(button)
        layout.addLayout(row)
        self.status = note('')
        background = QHBoxLayout()
        for label, enabled in (('Enable Windows background task', True), ('Remove Windows background task', False)):
            button = QPushButton(label)
            button.clicked.connect(lambda _checked=False, value=enabled: self.background(value))
            background.addWidget(button)
        layout.addLayout(background)
        layout.addWidget(self.status)
        self.history = QPlainTextEdit()
        self.history.setReadOnly(True)
        self.history.setAccessibleName('Script run history')
        layout.addWidget(self.history)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.close)
        layout.addWidget(close)
        controller.changed.connect(self.refresh)
        self.refresh()

    def background(self, enabled):
        from .background_scheduler import configure_task
        try:
            configure_task(enabled)
            self.controller.window.preferences.setValue('Automation/background_enabled', enabled)
            self.refresh()
            QMessageBox.information(self, 'Background automation', 'Background task enabled. Runs due scripts and folder monitors every minute while you are signed in.' if enabled else 'Background task removed.')
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, 'Background automation', str(error))

    def identity(self):
        row = self.tree.currentItem()
        return row.data(0, Qt.ItemDataRole.UserRole) if row else None

    def refresh(self):
        identity = self.identity()
        self.tree.clear()
        for key, script in self.controller.scripts.items():
            when = time.strftime('%Y-%m-%d %H:%M', time.localtime(script['next_run'])) if script['enabled'] else 'Disabled'
            row = QTreeWidgetItem(self.tree, [script['name'], when,
                script['schedule'] if script.get('schedule', 'Interval') != 'Interval' else f'Every {script["interval"]} min' if script['interval'] else 'Once', script.get('last', 'Not run')])
            row.setData(0, Qt.ItemDataRole.UserRole, key)
            if key == identity:
                self.tree.setCurrentItem(row)
        if not self.tree.currentItem() and self.tree.topLevelItemCount():
            self.tree.setCurrentItem(self.tree.topLevelItem(0))
        for column in range(3):
            self.tree.resizeColumnToContents(column)
        background = self.controller.window.preferences.value('Automation/background_enabled', False, type=bool)
        self.status.setText(self.controller.problem or self.controller.message or ('Ready. Windows background task configured.' if background else 'Ready. Schedules run while FluxFTP is open; enable the background task for closed-desktop runs.'))
        self.details()

    def details(self, *_):
        identity = self.identity()
        script = self.controller.scripts.get(identity, {})
        ready = self.controller.owner and not self.controller.problem
        for label in ('Edit…', 'Delete', 'Run Now'):
            self.buttons[label].setEnabled(bool(identity and ready and not self.controller.running))
        self.buttons['New script…'].setEnabled(bool(ready))
        self.buttons['Cancel Run'].setEnabled(bool(self.controller.worker))
        self.history.setPlainText('\n'.join(script.get('history', [])) or 'No runs recorded.')

    def new(self):
        dialog = ScriptDialog(self.controller, parent=self)
        dialog.exec()
        dialog.deleteLater()

    def edit(self):
        if self.identity() and self.identity() != self.controller.running:
            dialog = ScriptDialog(self.controller, self.identity(), self)
            dialog.exec()
            dialog.deleteLater()

    def remove(self):
        identity = self.identity()
        if identity and QMessageBox.question(self, 'Delete script', 'Delete this script and its schedule?') == QMessageBox.StandardButton.Yes:
            try:
                self.controller.remove(identity)
            except (ValueError, OSError) as error:
                QMessageBox.warning(self, 'Cannot delete script', str(error))

    def run(self):
        if not self.identity():
            return
        script = self.controller.scripts[self.identity()]
        parts = []
        for i, step in enumerate(script['steps'], 1):
            parts.append(f'{i}. {step["action"]}: {step["site"]}\nRemote: {step["remote"]}')
            if 'local' in step:
                parts.append(f'Local: {step["local"]}\nExisting destination: {step["exists"]}')
            if 'lines' in step:
                parts.append('\n'.join(step['lines']))
        review = QDialog(self)
        review.setWindowTitle('Review script before running')
        review.resize(700, 480)
        layout = QVBoxLayout(review)
        layout.addWidget(note('These saved steps will run now. Earlier changes are not rolled back if a later step fails.'))
        preview = QPlainTextEdit('\n\n'.join(parts))
        preview.setReadOnly(True)
        layout.addWidget(preview)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('Run Now')
        buttons.accepted.connect(review.accept)
        buttons.rejected.connect(review.reject)
        layout.addWidget(buttons)
        accepted = review.exec()
        review.deleteLater()
        if not accepted:
            return
        try:
            self.controller.start(self.identity())
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, 'Cannot run script', str(error))
