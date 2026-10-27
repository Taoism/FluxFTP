"""View-menu navigation and access to existing remote editing sessions."""
from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (QApplication, QDialog, QVBoxLayout, QLabel,
    QTreeWidget, QTreeWidgetItem, QHBoxLayout, QPushButton, QStyle)
from shiboken6 import isValid
from .view_options import ViewOptions


class EditFilesDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.owner = window
        self.setWindowTitle('Current edit files')
        self.resize(760, 340)
        rows = QVBoxLayout(self)
        self.summary = QLabel()
        rows.addWidget(self.summary)
        self.files = QTreeWidget()
        self.files.setRootIsDecorated(False)
        self.files.setHeaderLabels(['Site', 'Remote file', 'Editor', 'Status'])
        rows.addWidget(self.files)
        buttons = QHBoxLayout()
        self.open_button = QPushButton('Show editor')
        self.open_button.clicked.connect(self.open_selected)
        buttons.addWidget(self.open_button)
        close = QPushButton('Close')
        close.clicked.connect(self.close)
        buttons.addWidget(close)
        rows.addLayout(buttons)
        self.files.itemSelectionChanged.connect(self.update_button)
        self.files.itemDoubleClicked.connect(lambda *_: self.open_selected())
        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self.refresh)

    def sessions(self):
        return [session for session in self.owner.mouse_windows
                if isValid(session) and getattr(session, 'editable', False)]

    def refresh(self):
        selected = self.files.currentItem()
        previous = selected.data(0, Qt.ItemDataRole.UserRole) if selected else None
        self.files.clear()
        for session in self.sessions():
            external = hasattr(session, 'remote')
            state = 'Saving…' if session.saving else ('Working copy' if external else
                    'Modified' if session.editor.document().isModified() else 'Saved')
            row = QTreeWidgetItem([session.site.name, session.remote if external else session.path,
                                  'External application' if external else 'Built-in editor', state])
            row.setData(0, Qt.ItemDataRole.UserRole, id(session))
            self.files.addTopLevelItem(row)
            if id(session) == previous:
                self.files.setCurrentItem(row)
        self.summary.setText(f'{self.files.topLevelItemCount()} remote edit sessions')
        for column in (0, 1, 2):
            self.files.resizeColumnToContents(column)
        self.update_button()

    def update_button(self):
        self.open_button.setEnabled(bool(self.files.selectedItems()))

    def open_selected(self):
        selected = self.files.selectedItems()
        identity = selected[0].data(0, Qt.ItemDataRole.UserRole) if selected else None
        session = next((session for session in self.sessions() if id(session) == identity), None)
        if session is None:
            self.refresh()
            return
        session.showNormal() if session.isMinimized() else session.show()
        session.raise_()
        session.activateWindow()

    def showEvent(self, event):
        self.refresh()
        self.timer.start()
        super().showEvent(event)

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)


class ViewMenu(QObject):
    def __init__(self, window, menus):
        super().__init__(window)
        self.window = window
        self.menu = menus.addMenu('View')
        self.menu.setStyleSheet('QMenu::item:disabled { color: #9ba2aa; }')
        self.actions = {}
        self.edit_dialog = None
        self.add('Refresh', 'F5', window.refresh_focused_pane, QStyle.StandardPixmap.SP_BrowserReload)
        self.menu.addSeparator()
        for label, shortcut, key in (
            ('Window list', 'Shift+F1', 'workspace'), ('Queue Window', 'Shift+F3', 'queue'),
            ('System log', 'Shift+F5', 'system'), ('Transfer Log', 'Shift+F4', 'transfer'),
            ('FTP Log', 'Shift+F2', 'ftp'), ('Task Window', 'Shift+F7', 'tasks')):
            self.add(label, shortcut, lambda key=key: self.show_panel(key),
                     QStyle.StandardPixmap.SP_FileDialogDetailedView)
        self.menu.addSeparator()
        self.options = ViewOptions(self)
        self.menu.addSeparator()
        self.add('Current edit files', '', self.show_edit_files)
        self.menu.addSeparator()
        self.add('Reset layout…', '', window.reset_layout)

    def add(self, label, shortcut, callback, icon=None):
        action = QAction(label, self)
        if shortcut:
            action.setShortcut(shortcut)
        if icon is not None:
            action.setIcon(self.window.style().standardIcon(icon))
        action.triggered.connect(lambda checked=False: callback())
        self.menu.addAction(action)
        self.actions[label] = action

    def initialize(self):
        QApplication.instance().focusChanged.connect(self.remember_pane)
        self.options.initialize()

    def remember_pane(self, old, current):
        if not isValid(self.window) or not isValid(self.window.tabs):
            return
        for pane in self.window.current_panes():
            if current is pane or (current is not None and pane.isAncestorOf(current)):
                pane.parentWidget().mark_pane = pane
                self.window.ftp_commands.update()
                self.window.queue_commands.update()
                break

    def show_panel(self, key):
        window = self.window
        if getattr(window, 'docking', None) and window.docking.active:
            window.docking.show_panel(key)
            return
        if key == 'workspace':
            panel = window.workspace_panel
            focus = window.mark_commands.pane().tree
        elif key == 'queue':
            panel, focus = window.queue_panel, window.queue
        else:
            panel = window.log_tabs
            log_panel = window.log_panels[key]
            panel.setCurrentWidget(log_panel)
            focus = {'system': window.log, 'transfer': window.transfer_log,
                     'ftp': window.ftp_log, 'tasks': window.tasks_log}[key]
        panel.show()
        focus.show()
        sizes = window.work_area.sizes()
        index = window.work_area.indexOf(panel)
        if sizes[index] == 0:
            sizes[index] = max(120, window.work_area.height() // 3)
            window.work_area.setSizes(sizes)
        focus.setFocus()

    def show_edit_files(self):
        if self.edit_dialog is None:
            self.edit_dialog = EditFilesDialog(self.window)
        self.edit_dialog.refresh()
        self.edit_dialog.showNormal() if self.edit_dialog.isMinimized() else self.edit_dialog.show()
        self.edit_dialog.raise_()
        self.edit_dialog.activateWindow()
