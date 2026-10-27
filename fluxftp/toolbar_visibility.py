"""Named, persistent visibility controls for header rows and pane toolbars."""
from PySide6.QtCore import QObject, Qt, QSignalBlocker
from PySide6.QtWidgets import QMenu


NAMES = {
    'tools': 'Main toolbar', 'connection': 'Quick Connect',
    'tabs': 'Workspace tabs', 'overview': 'Window list',
    'left': 'Left pane toolbar', 'right': 'Right pane toolbar',
}


class ToolbarVisibility(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.menu = window.view_commands.menu.addMenu('Edit Toolbars')
        self.actions = {}
        for key, name in NAMES.items():
            action = self.menu.addAction(name)
            action.setCheckable(True)
            action.toggled.connect(lambda checked, key=key: self.set_visible(key, checked))
            self.actions[key] = action
        self.menu.aboutToShow.connect(self.apply)
        window.edit_commands.menu.addSeparator()
        self.edit_menu = window.edit_commands.menu.addMenu('Toolbars')
        self.edit_menu.addActions(list(self.actions.values()))
        self.edit_menu.aboutToShow.connect(self.apply)
        self.apply()

    def visible(self, key):
        return self.window.preferences.value('Layout/toolbars/' + key, True, type=bool)

    def set_visible(self, key, value):
        self.window.preferences.setValue('Layout/toolbars/' + key, bool(value))
        self.apply()

    def context_menu(self, key, parent):
        menu = QMenu(parent)
        menu.addAction('Hide ' + NAMES[key], lambda: self.set_visible(key, False))
        edit = menu.addMenu('Edit Toolbars')
        self.apply()
        for action in self.actions.values():
            edit.addAction(action)
        return menu

    def install(self, widget, key):
        if widget.property('toolbarVisibilityInstalled'):
            return
        widget.setProperty('toolbarVisibilityInstalled', True)
        widget.setAccessibleName(NAMES[key])
        widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        def show(position):
            menu = self.context_menu(key, widget)
            menu.exec(widget.mapToGlobal(position))
            menu.deleteLater()
        widget.customContextMenuRequested.connect(show)

    def apply(self):
        window = self.window
        for key, action in self.actions.items():
            with QSignalBlocker(action):
                action.setChecked(self.visible(key))
        for key, row in window.header_rows.rows.items():
            row.title = NAMES[key]
            self.install(row, key)
            if key in ('tools', 'connection'):
                self.install(row.content, key)
            row.setVisible(self.visible(key))
        for group, band in zip(window.header_rows.groups, window.header_rows.bands):
            band.setVisible(any(self.visible(key) for key in group))
        for index in range(window.tabs.count()):
            split = window.tabs.widget(index)
            for side, key in enumerate(('left', 'right')):
                toolbar = split.widget(side).file_toolbar
                self.install(toolbar, key)
                toolbar.setVisible(self.visible(key))
