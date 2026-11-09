"""Persistently disable layout grips without disabling panel contents."""
from PySide6.QtCore import QObject
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QSplitter


class LayoutLock(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.action = QAction('Lock layout', self)
        self.action.setCheckable(True)
        self.action.setChecked(window.preferences.value('Layout/locked', False, type=bool))
        self.action.toggled.connect(self.set_locked)
        window.view_commands.menu.addAction(self.action)
        self.apply()

    def set_locked(self, value):
        self.window.preferences.setValue('Layout/locked', value)
        self.apply()

    def apply(self):
        enabled = not self.action.isChecked()
        if hasattr(self.window, 'docking'):
            self.window.docking.lock(not enabled)
        for splitter in self.window.findChildren(QSplitter):
            for index in range(1, splitter.count()):
                splitter.handle(index).setEnabled(enabled)
        for index in range(self.window.tabs.count()):
            split = self.window.tabs.widget(index)
            for side in range(split.count()):
                pane = split.widget(side)
                for toolbar in (getattr(pane, 'file_toolbar', None),
                                getattr(pane, 'address_toolbar', None),
                                getattr(pane, 'filter_toolbar', None)):
                    if toolbar:
                        toolbar.setMovable(enabled)
                        toolbar.setFloatable(enabled)
        for row in self.window.header_rows.rows.values():
            row.grip.setEnabled(enabled)
            row.setAcceptDrops(enabled)
            if hasattr(row, 'height_grip'): row.height_grip.setEnabled(enabled)
