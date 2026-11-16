"""Workspace-owned queue and log widgets with shared panel docking shells."""
from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QHeaderView, QHBoxLayout,
    QPlainTextEdit, QPushButton, QStackedWidget, QTabWidget, QVBoxLayout, QWidget)

from .layout_options import CaptionPanel
from .queue_tree import QueueTree


LOGS = (('ftp', 'FTP Log'), ('transfer', 'Transfer Log'),
        ('system', 'System Log'), ('tasks', 'Tasks'))


def create_panel_set(window):
    system_log = QPlainTextEdit()
    system_log.setAccessibleName('System log')
    system_log.setTabChangesFocus(True)
    system_log.setReadOnly(True)
    system_log.setMaximumBlockCount(window.preferences.value('General/log_lines', 300, type=int))
    system_log.textChanged.connect(lambda log=system_log: window._write_activity_log(log))

    ftp_log = QPlainTextEdit()
    ftp_log.setAccessibleName('FTP log')
    ftp_log.setTabChangesFocus(True)
    ftp_log.setReadOnly(True)
    ftp_log.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
    ftp_content = QWidget()
    ftp_layout = QVBoxLayout(ftp_content)
    ftp_layout.setContentsMargins(0, 0, 0, 0)
    ftp_layout.setSpacing(2)
    command_row = QHBoxLayout()
    command_row.setContentsMargins(0, 0, 0, 0)
    command_row.setSpacing(3)
    command_input = QComboBox()
    command_input.setEditable(True)
    command_input.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
    command_input.addItems(['HELP', 'FEAT', 'SYST', 'PWD', 'NOOP', 'SITE HELP', 'SITE WHO'])
    command_input.setCurrentIndex(-1)
    command_input.setCompleter(None)
    command_input.lineEdit().setPlaceholderText('FTP Command')
    command_input.setAccessibleName('FTP Command')
    command_input.lineEdit().setAccessibleName('FTP Command')
    command_input.lineEdit().returnPressed.connect(window.command_commands.inline_raw)
    command_send = QPushButton('Send')
    command_send.clicked.connect(window.command_commands.inline_raw)
    command_row.addWidget(command_input, 1)
    command_row.addWidget(command_send)
    ftp_layout.addLayout(command_row)
    ftp_layout.addWidget(ftp_log, 1)

    transfer_log = QPlainTextEdit()
    transfer_log.setAccessibleName('Transfer log')
    transfer_log.setTabChangesFocus(True)
    transfer_log.setReadOnly(True)
    transfer_log.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
    tasks_log = QPlainTextEdit()
    tasks_log.setAccessibleName('Tasks log')
    tasks_log.setTabChangesFocus(True)
    tasks_log.setReadOnly(True)

    panels = {}
    widgets = dict(ftp=ftp_content, transfer=transfer_log, system=system_log, tasks=tasks_log)
    for key, title in LOGS:
        panels[key] = CaptionPanel(widgets[key], title)

    queue = QueueTree()
    queue.setAccessibleName('Transfer queue')
    queue.setAlternatingRowColors(True)
    queue.setHeaderLabels(['Name', 'Progress', 'Speed', 'Transferred', 'Files', 'Size',
                           'Source path', 'Target path', 'Remark'])
    queue.setIconSize(QSize(32, 32))
    queue.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
    for column in (6, 7):
        queue.header().setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)
    queue.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)

    return dict(queue=queue, log=system_log, ftp_log=ftp_log, ftp_log_content=ftp_content,
                ftp_command_input=command_input, ftp_command_send=command_send,
                transfer_log=transfer_log, tasks_log=tasks_log, log_panels=panels,
                active_log='ftp')


class WorkspacePanelManager:
    def __init__(self, window, first_workspace, first_set):
        self.window = window
        self.sets = {first_workspace: first_set}
        self.queue_stack = QStackedWidget(window)
        self.log_stacks = {key: QStackedWidget(window) for key, _ in LOGS}
        self.log_tabs = QTabWidget()
        self.log_tabs.setTabPosition(QTabWidget.TabPosition.South)
        self.queue_stack.addWidget(first_set['queue'])
        for key, title in LOGS:
            self.log_stacks[key].addWidget(first_set['log_panels'][key])
            self.log_tabs.addTab(self.log_stacks[key], title)
        self.queue_panel = CaptionPanel(self.queue_stack, 'Queue')
        self.log_tabs.currentChanged.connect(self._remember_active_log)
        window.panel_manager = self
        window.workspace_panels = self.sets
        window.queue_stack = self.queue_stack
        window.queue_panel = self.queue_panel
        window.log_tabs = self.log_tabs
        window.log_stacks = self.log_stacks
        self.activate(first_workspace)
        window.tabs.currentChanged.connect(self._activate_index)
        window.queue_commands.install()
        window.queue_commands.attach_tree(first_set['queue'])

    def queue_for_workspace(self, workspace=None):
        panel_set = self.sets.get(workspace) if workspace is not None else None
        return panel_set['queue'] if panel_set is not None else self.window.queue

    def log_for_workspace(self, workspace=None, kind='system'):
        panel_set = self.sets.get(workspace) if workspace is not None else None
        if panel_set is None:
            panel_set = self.sets.get(self.window.tabs.currentWidget())
        if panel_set is None:
            return self.window.log
        names = dict(system='log', ftp='ftp_log', transfer='transfer_log', tasks='tasks_log')
        return panel_set[names.get(kind, 'log')]

    def _activate_index(self, index):
        workspace = self.window.tabs.widget(index)
        if workspace is not None:
            self.activate(workspace)

    def _remember_active_log(self, index):
        workspace = self.window.tabs.currentWidget()
        panel_set = self.sets.get(workspace)
        if panel_set is not None and 0 <= index < len(LOGS):
            panel_set['active_log'] = LOGS[index][0]

    def add(self, workspace):
        panel_set = create_panel_set(self.window)
        self.sets[workspace] = panel_set
        self.queue_stack.addWidget(panel_set['queue'])
        for key, _ in LOGS:
            self.log_stacks[key].addWidget(panel_set['log_panels'][key])
        self.window.queue_commands.attach_tree(panel_set['queue'])
        if hasattr(self.window, 'configure_ftp_log'):
            self.window.configure_ftp_log(panel_set['ftp_log'])
        if hasattr(self.window, 'layout_value'):
            style = self.window.layout_value('caption_tab_style')
            for key, panel in panel_set['log_panels'].items():
                panel.show_caption(self.window.layout_value('caption_' + key), style)
        if hasattr(self.window, 'queue_filter'):
            self.window.queue_filter.apply_to(panel_set['queue'])
        if hasattr(self.window, 'mouse_filter'):
            panel_set['queue'].itemDoubleClicked.connect(self.window.mouse_queue)
            for widget in (panel_set['log'], panel_set['ftp_log'],
                           panel_set['transfer_log'], panel_set['tasks_log']):
                widget.viewport().installEventFilter(self.window.mouse_filter)
        return panel_set

    def activate(self, workspace):
        panel_set = self.sets.get(workspace)
        if panel_set is None:
            return
        self.queue_stack.setCurrentWidget(panel_set['queue'])
        for key, _ in LOGS:
            self.log_stacks[key].setCurrentWidget(panel_set['log_panels'][key])
        window = self.window
        for name in ('queue', 'log', 'ftp_log', 'ftp_log_content', 'ftp_command_input',
                     'ftp_command_send', 'transfer_log', 'tasks_log', 'log_panels'):
            setattr(window, name, panel_set[name])
        selected_log = panel_set.get('active_log', 'ftp')
        for index, (key, _) in enumerate(LOGS):
            if key == selected_log:
                self.log_tabs.setCurrentIndex(index)
                break
        if hasattr(window, 'queue_filter'):
            window.queue_filter.bind_queue(panel_set['queue'])
            window.queue_filter.apply_to(panel_set['queue'])
        if hasattr(window, 'view_commands'):
            window.view_commands.options.apply_tree(panel_set['queue'], 'queue')
        if hasattr(window, 'queue_commands'):
            window.queue_commands.update()

    def remove(self, workspace):
        panel_set = self.sets.pop(workspace, None)
        if panel_set is None:
            return
        self.window.queue_commands.detach_tree(panel_set['queue'])
        if hasattr(self.window, 'ftp_highlighters'):
            self.window.ftp_highlighters.pop(panel_set['ftp_log'], None)
        self.queue_stack.removeWidget(panel_set['queue'])
        panel_set['queue'].deleteLater()
        for key, _ in LOGS:
            panel = panel_set['log_panels'][key]
            self.log_stacks[key].removeWidget(panel)
            panel.deleteLater()

    def queue_trees(self):
        return [panel_set['queue'] for panel_set in self.sets.values()]
