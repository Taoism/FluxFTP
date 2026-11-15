"""Independent controls for toolbar movement and dock panel movement."""
from PySide6.QtCore import QObject
from PySide6.QtGui import QAction


class LayoutLock(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        preferences = window.preferences
        legacy_locked = preferences.value('Layout/locked', False, type=bool)
        if preferences.value('Layout/toolbar_locked', None) is None:
            preferences.setValue('Layout/toolbar_locked', legacy_locked)
        if preferences.value('Layout/dock_locked', None) is None:
            preferences.setValue('Layout/dock_locked', legacy_locked)
        preferences.setValue('Layout/locked', False)

        self.toolbar_action = QAction('Lock Toolbar', self)
        self.toolbar_action.setCheckable(True)
        self.toolbar_action.setChecked(preferences.value('Layout/toolbar_locked', False, type=bool))
        self.toolbar_action.toggled.connect(lambda value: self.set_locked('toolbar_locked', value))
        window.layout_commands.menu.addAction(self.toolbar_action)

        self.dock_action = QAction('Lock Dock', self)
        self.dock_action.setCheckable(True)
        self.dock_action.setChecked(preferences.value('Layout/dock_locked', False, type=bool))
        self.dock_action.toggled.connect(lambda value: self.set_locked('dock_locked', value))
        window.layout_commands.menu.addAction(self.dock_action)
        self.apply()

    def set_locked(self, key, value):
        self.window.preferences.setValue('Layout/' + key, bool(value))
        self.apply()

    def apply(self):
        toolbar_locked = self.toolbar_action.isChecked()
        dock_locked = self.dock_action.isChecked()
        docking = getattr(self.window, 'docking', None)
        if docking:
            docking.lock(dock_locked)
        for toolbar in (getattr(self.window, 'main_toolbar', None),):
            if toolbar:
                toolbar.setMovable(not toolbar_locked)
                toolbar.setFloatable(not toolbar_locked)
        for index in range(self.window.tabs.count()):
            split = self.window.tabs.widget(index)
            for side in range(split.count()):
                pane = split.widget(side)
                for toolbar in (getattr(pane, 'file_toolbar', None),
                                getattr(pane, 'address_toolbar', None),
                                getattr(pane, 'filter_toolbar', None)):
                    if toolbar:
                        toolbar.setMovable(not toolbar_locked)
                        toolbar.setFloatable(not toolbar_locked)
        for row in self.window.header_rows.rows.values():
            row.grip.setEnabled(not toolbar_locked)
            row.setAcceptDrops(not toolbar_locked)
            if hasattr(row, 'height_grip'):
                row.height_grip.setEnabled(not toolbar_locked)
