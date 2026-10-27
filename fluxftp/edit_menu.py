"""Selection-aware Edit commands for the active file pane."""
from pathlib import Path

from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox, QStyle

from .core import safe_name


def rename_plan(names, find, replacement, existing):
    if not find:
        raise ValueError('Enter text to replace.')
    pairs = [(name, name.replace(find, replacement)) for name in names]
    pairs = [(old, new) for old, new in pairs if old != new]
    targets = set()
    occupied = {name.casefold() for name in existing}
    for old, new in pairs:
        safe_name(new)
        key = new.casefold()
        if key in occupied or key in targets:
            raise ValueError(f'Target already exists or is duplicated: {new}. Nothing was renamed.')
        targets.add(key)
    return pairs


class EditMenu(QObject):
    def __init__(self, window, menus):
        super().__init__(window)
        self.window = window
        self.menu = menus.addMenu('Edit')
        self.actions = {}
        for label, shortcut, callback, icon in (
            ('Search', 'Ctrl+F', self.search, QStyle.StandardPixmap.SP_FileDialogContentsView),
            (None, '', None, None),
            ('Rename', 'F2', lambda: self.dispatch('rename'), None),
            ('Delete', 'Del', lambda: self.dispatch('delete'), QStyle.StandardPixmap.SP_DialogCancelButton),
            (None, '', None, None),
            ('Make Directory', 'Ins', lambda: self.dispatch('mkdir'), None),
            (None, '', None, None),
            ('Rename Multiple Files…', 'Shift+Ctrl+M', self.rename_multiple, QStyle.StandardPixmap.SP_FileDialogDetailedView),
            (None, '', None, None),
            ('Copy Selected Names', '', self.copy_names, None),
        ):
            if label is None:
                self.menu.addSeparator()
                continue
            action = QAction(label, self)
            action.setShortcut(shortcut)
            action.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
            action.triggered.connect(callback)
            if icon is not None:
                action.setIcon(window.style().standardIcon(icon))
            self.menu.addAction(action)
            self.actions[label] = action
        self.menu.aboutToShow.connect(self.update)

    def install(self, pane):
        for action in self.actions.values():
            pane.tree.addAction(action)
        pane.tree.itemSelectionChanged.connect(self.update)

    def pane(self):
        return self.window.mark_commands.pane()

    def update(self, *_):
        pane = self.pane()
        ready = bool(pane and not pane.busy and (pane.mode == 'Local' or pane.site))
        selected = pane.selected() if ready else []
        for label, enabled in (
            ('Search', bool(pane)), ('Rename', len(selected) == 1),
            ('Delete', bool(selected)), ('Make Directory', ready),
            ('Rename Multiple Files…', bool(selected)), ('Copy Selected Names', bool(selected)),
        ):
            self.actions[label].setEnabled(enabled)

    def dispatch(self, method):
        self.update()
        label = {'rename': 'Rename', 'delete': 'Delete', 'mkdir': 'Make Directory'}[method]
        if self.actions[label].isEnabled():
            getattr(self.pane(), method)()

    def search(self):
        pane = self.pane()
        if pane:
            pane.filter.setFocus()
            pane.filter.selectAll()

    def copy_names(self):
        self.update()
        if self.actions['Copy Selected Names'].isEnabled():
            QApplication.clipboard().setText('\n'.join(entry.name for entry in self.pane().selected()))

    def rename_multiple(self):
        self.update()
        if not self.actions['Rename Multiple Files…'].isEnabled():
            return
        pane = self.pane()
        snapshot = (pane.mode, pane.site, pane.path, pane.generation)
        names = [entry.name for entry in pane.selected()]
        find, ok = QInputDialog.getText(self.window, 'Rename Multiple Files', 'Find text (case-sensitive)')
        if not ok or not find:
            return
        replacement, ok = QInputDialog.getText(self.window, 'Rename Multiple Files', 'Replace with')
        if not ok:
            return
        try:
            existing = [pane.tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole).name
                        for i in range(pane.tree.topLevelItemCount())]
            pairs = rename_plan(names, find, replacement, existing)
            if not pairs:
                self.window.statusBar().showMessage('No selected names match the search text.', 5000)
                return
            preview = '\n'.join(f'{old} → {new}' for old, new in pairs)
            if QMessageBox.question(self.window, 'Rename Multiple Files', preview,
                    QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Ok:
                return
            if pane.busy or snapshot != (pane.mode, pane.site, pane.path, pane.generation):
                return
            directory = pane.path
            paths = [(pane.join(old), pane.join(new)) for old, new in pairs]
            def remote(connection):
                rename_plan(names, find, replacement, [entry.name for entry in connection.list(directory)])
                for old, new in paths:
                    connection.rename(old, new)
            def local():
                rename_plan(names, find, replacement, [p.name for p in Path(directory).iterdir()])
                for old, new in paths:
                    Path(old).rename(new)
            pane.mutate(remote, local)
        except ValueError as error:
            self.window.error(str(error))
