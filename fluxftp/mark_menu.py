"""Pane-local marks, separate from the selection used for file operations."""
from fnmatch import fnmatchcase

from PySide6.QtCore import QObject, QEvent, Qt, QSignalBlocker
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QInputDialog, QStyle

MARK_ROLE = Qt.ItemDataRole.UserRole + 32


def set_marked(row, marked):
    if marked and row.treeWidget(): row.treeWidget().setProperty('marksEmpty', False)
    row.setData(0, MARK_ROLE, marked)
    font = row.font(0)
    font.setBold(marked)
    row.setFont(0, font)


class MarkMenu(QObject):
    def __init__(self, window, menus):
        super().__init__(window)
        self.window = window
        self.menu = menus.addMenu('Mark')
        self.menu.setStyleSheet('QMenu::item:disabled { color: #9ba2aa; }')
        self.actions = []
        for label, shortcut, callback in (
            ('Mark/Unmark', 'Space', self.toggle),
            ('Unmark All', 'Ctrl+Shift+C', self.clear),
            ('Select all Marked items', 'Ctrl+Shift+S', self.select_marked),
            ('Export Mark List to Clipboard', '', self.export),
            (None, '', None),
            ('Select', 'Ctrl+S', self.select_mask),
            ('Select All', 'Ctrl+A', self.select_all),
        ):
            if label is None:
                self.menu.addSeparator()
                continue
            action = QAction(label, self)
            action.setShortcut(shortcut)
            action.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
            action.triggered.connect(callback)
            self.menu.addAction(action)
            self.actions.append(action)
        for action in self.actions[-2:]:
            action.setIcon(window.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogDetailedView))
        self.menu.aboutToShow.connect(self.update)

    def install(self, pane):
        pane.tree.installEventFilter(self)
        for action in self.actions:
            pane.tree.addAction(action)
        pane.tree.currentItemChanged.connect(self.update)
        pane.tree.itemSelectionChanged.connect(self.update)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.FocusIn:
            watched.pane.parentWidget().mark_pane = watched.pane
            self.update()
            self.window.edit_commands.update()
        return False

    def pane(self):
        if not hasattr(self.window, 'tabs'):
            return None
        workspace = self.window.tabs.currentWidget()
        if workspace is None:
            return None
        return getattr(workspace, 'mark_pane', workspace.widget(0))

    def rows(self, visible=True):
        pane = self.pane()
        if pane is None or pane.busy:
            return []
        return [row for i in range(pane.tree.topLevelItemCount())
                if (row := pane.tree.topLevelItem(i)).data(0, Qt.ItemDataRole.UserRole) is not None
                and (not visible or not row.isHidden())]

    def marked(self, visible=True):
        return [row for row in self.rows(visible) if row.data(0, MARK_ROLE)]

    def update(self, *_):
        pane = self.pane()
        available = bool(pane and not pane.busy)
        tree = pane.tree if available else None
        visible = bool(tree and any(not tree.topLevelItem(i).isHidden() for i in range(tree.topLevelItemCount())))
        current = tree.currentItem() if tree else None
        target = bool(tree and (tree.selectedItems() or current is not None and not current.isHidden()))
        marked = visible_marked = False
        if tree and not tree.property('marksEmpty'):
            for i in range(tree.topLevelItemCount()):
                row = tree.topLevelItem(i)
                if row.data(0, MARK_ROLE):
                    marked = True
                    if not row.isHidden():
                        visible_marked = True
                        break
        for action, enabled in zip(self.actions, (target, marked, visible_marked, marked, visible, visible)):
            action.setEnabled(enabled)

    def toggle(self):
        rows = self.rows()
        pane = self.pane()
        if pane is None:
            return
        targets = [row for row in rows if row.isSelected()]
        if not targets and pane.tree.currentItem() in rows:
            targets = [pane.tree.currentItem()]
        with QSignalBlocker(pane.tree):
            for row in targets:
                set_marked(row, not row.data(0, MARK_ROLE))
        self.update()

    def clear(self):
        pane = self.pane()
        if pane is None:
            return
        with QSignalBlocker(pane.tree):
            for row in self.marked(False):
                set_marked(row, False)
        self.update()

    def select_rows(self, matches):
        pane = self.pane()
        if pane is None or pane.busy:
            return
        with QSignalBlocker(pane.tree):
            pane.tree.clearSelection()
            for row in matches:
                row.setSelected(True)
        self.update()

    def select_marked(self):
        self.select_rows(self.marked())

    def select_all(self):
        self.select_rows(self.rows())

    def select_mask(self):
        pane = self.pane()
        if not self.rows():
            return
        generation, path = pane.generation, pane.path
        mask, accepted = QInputDialog.getText(self.window, 'Select', 'Filename masks (separate with semicolons):', text='*.*')
        if not accepted or self.pane() is not pane or pane.generation != generation or pane.path != path or pane.busy:
            return
        masks = [part.strip().casefold() for part in mask.split(';') if part.strip()]
        if masks:
            self.select_rows([row for row in self.rows() if any(
                pattern in ('*', '*.*') or fnmatchcase(row.data(0, Qt.ItemDataRole.UserRole).name.casefold(), pattern)
                for pattern in masks)])

    def export(self):
        pane = self.pane()
        rows = self.marked(False)
        if pane is not None and rows:
            QApplication.clipboard().setText('\n'.join(pane.join(row.data(0, Qt.ItemDataRole.UserRole).name) for row in rows))
            self.window.statusBar().showMessage(f'Copied {len(rows)} marked paths', 5000)
