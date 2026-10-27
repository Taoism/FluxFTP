"""Comparison of the two current directory listings."""
from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QAction, QActionGroup, QBrush, QColor

RESULT_ROLE = int(Qt.ItemDataRole.UserRole) + 31


class CompareMenu(QObject):
    def __init__(self, window, tools_menu):
        super().__init__(window)
        self.window = window
        self.menu = tools_menu.addMenu('Compare folder contents')
        self.menu.menuAction().setText('Compare folder contents\tCtrl+D')
        shortcut = QAction(self)
        shortcut.setShortcut('Ctrl+D')
        shortcut.triggered.connect(self.compare)
        window.addAction(shortcut)
        self.mode_group = QActionGroup(self)
        self.actions = {}
        for label, key, default in (
            ('Compare name only', 'size', False),
            ('Compare name and size', 'size', False),
            ('Select compare result', None, False),
            ('Hide same files/folders', 'hide', True),
            ('Partial Match', 'partial', False),
        ):
            if label in ('Select compare result', 'Hide same files/folders', 'Partial Match'):
                self.menu.addSeparator()
            action = self.menu.addAction(label)
            self.actions[label] = action
            if key is None:
                action.triggered.connect(lambda: self.compare(select=True))
                continue
            action.setCheckable(True)
            value = window.preferences.value('Compare/' + key, default, type=bool)
            action.setChecked(not value if label == 'Compare name only' else value)
            if key == 'size':
                self.mode_group.addAction(action)
            action.triggered.connect(lambda checked, key=key, label=label: self.change(
                key, not checked if label == 'Compare name only' else checked))

    def change(self, key, value):
        self.window.preferences.setValue('Compare/' + key, value)
        self.compare()

    def clear(self, pane):
        workspace = pane.parentWidget()
        if not hasattr(workspace, 'count'):
            return
        if not getattr(workspace, '_comparison_active', False): return
        workspace._comparison_active = False
        for index in range(workspace.count()):
            sibling = workspace.widget(index)
            for i in range(sibling.tree.topLevelItemCount()):
                row = sibling.tree.topLevelItem(i)
                if row.data(0, RESULT_ROLE) is not None:
                    row.setData(0, RESULT_ROLE, None)
                    for column in range(sibling.tree.columnCount()):
                        row.setBackground(column, QBrush())
            sibling.filter_rows()

    def compare(self, checked=False, *, select=False):
        panes = self.window.current_panes()
        if len(panes) != 2:
            return
        if any(pane.busy for pane in panes):
            self.window.statusBar().showMessage('Wait for both panes to finish loading before comparing', 5000)
            return
        if any(pane.mode == 'Remote' and pane.site is None for pane in panes):
            self.window.error('Connect each remote pane before comparing folder contents.')
            return
        size = self.actions['Compare name and size'].isChecked()
        partial = self.actions['Partial Match'].isChecked()
        rows = [[pane.tree.topLevelItem(i) for i in range(pane.tree.topLevelItemCount())]
                for pane in panes]
        panes[0].parentWidget()._comparison_active = True
        counts = [0, 0, 0]
        for side, own in enumerate(rows):
            opposite = {row.data(0, Qt.ItemDataRole.UserRole).name: row for row in rows[1-side]}
            for row in own:
                entry = row.data(0, Qt.ItemDataRole.UserRole)
                candidates = [opposite[entry.name]] if entry.name in opposite else []
                if partial and not candidates:
                    candidates = [other for name, other in opposite.items()
                                  if entry.name in name or name in entry.name]
                same = any(entry.is_dir == other.is_dir and entry.is_link == other.is_link
                           and (not size or entry.is_dir or entry.size == other.size)
                           for other in (r.data(0, Qt.ItemDataRole.UserRole) for r in candidates))
                result = 'same' if same else 'different' if candidates else 'only'
                row.setData(0, RESULT_ROLE, result)
                from .themes import comparison_color
                brush = QBrush() if same else QBrush(QColor(comparison_color(self.window.preferences, bool(candidates))))
                for column in range(panes[side].tree.columnCount()):
                    row.setBackground(column, brush)
                if not candidates:
                    counts[side] += 1
                elif not same and side == 0:
                    counts[2] += 1
            panes[side].filter_rows()
            if select:
                panes[side].tree.clearSelection()
                for row in own:
                    row.setSelected(not row.isHidden() and row.data(0, RESULT_ROLE) != 'same')
        self.window.statusBar().showMessage(
            f'Compare complete · left only: {counts[0]} · right only: {counts[1]} · different: {counts[2]}', 10000)
