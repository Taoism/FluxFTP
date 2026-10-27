"""Persistent View submenu settings shared by existing and future workspaces."""
from PySide6.QtCore import Qt, QSignalBlocker
from PySide6.QtGui import QActionGroup
from PySide6.QtWidgets import QStyledItemDelegate, QHeaderView, QTreeWidget, QTreeWidgetItem


GRID_MODES = ('None', 'Horizontal', 'Vertical', 'Horizontal and Vertical')


class GridDelegate(QStyledItemDelegate):
    def __init__(self, tree, preferences):
        super().__init__(tree)
        self.preferences = preferences

    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        mode = self.preferences.value('Display/grid_lines', 'None', type=str)
        painter.save()
        painter.setPen(option.palette.mid().color())
        if mode in ('Horizontal', 'Horizontal and Vertical'):
            painter.drawLine(option.rect.bottomLeft(), option.rect.bottomRight())
        if mode in ('Vertical', 'Horizontal and Vertical'):
            painter.drawLine(option.rect.topRight(), option.rect.bottomRight())
        painter.restore()


class ViewOptions:
    def __init__(self, owner):
        self.owner, self.window = owner, owner.window
        self.settings = self.window.preferences
        self.actions = {}
        self.menus = {}
        self.applying = False
        for label, group, columns in (
            ('Remote columns', 'remote', ('Size', 'Date', 'Attrib', 'User', 'Group')),
            ('Local columns', 'local', ('Size', 'Date')),
            ('Window list columns', 'windows', ('FTP', 'Action'))):
            menu = self.menus[label] = owner.menu.addMenu(label)
            for column in columns:
                self.toggle(menu, column, f'{group}_columns/{column.lower()}', column not in ('User', 'Group'))
        grid = self.menus['Grid Lines'] = owner.menu.addMenu('Grid Lines')
        self.grid_group = QActionGroup(grid)
        self.grid_group.setExclusive(True)
        current = self.settings.value('Display/grid_lines', 'None', type=str)
        if current not in GRID_MODES:
            current = 'None'
        for mode in GRID_MODES:
            action = grid.addAction(mode)
            action.setCheckable(True)
            action.setChecked(mode == current)
            self.grid_group.addAction(action)
            action.triggered.connect(lambda checked, mode=mode: self.set_value('grid_lines', mode))
        auto = self.menus['Column Auto Size'] = owner.menu.addMenu('Column Auto Size')
        for group in ('Remote', 'Local', 'Queue'):
            self.toggle(auto, group, 'auto_size/' + group.lower(), True)

    def toggle(self, menu, label, key, default):
        action = menu.addAction(label)
        action.setCheckable(True)
        action.setChecked(self.value(key, default))
        action.toggled.connect(lambda checked: self.set_value(key, checked))
        self.actions[key] = action

    def value(self, key, default=True):
        return self.settings.value('Display/' + key, default, type=bool)

    def set_value(self, key, value):
        self.settings.setValue('Display/' + key, value)
        self.apply()

    def initialize(self):
        w = self.window
        # The workspace overview supplies the reference's FTP and Action columns.
        w.window_list = QTreeWidget()
        w.window_list.setAccessibleName('Workspaces')
        w.window_list.setRootIsDecorated(False)
        w.window_list.setHeaderLabels(['Window', 'FTP', 'Action'])
        w.window_list.setMaximumHeight(115)
        w.window_list.setMinimumHeight(65)
        w.workspace_panel.rows.insertWidget(2, w.window_list)
        w.window_list.itemSelectionChanged.connect(self.select_workspace)
        w.tabs.currentChanged.connect(lambda _: self.refresh_windows())
        w.tray_timer.timeout.connect(self.refresh_windows)
        self.apply()

    def select_workspace(self):
        selected = self.window.window_list.selectedItems()
        if selected:
            self.window.tabs.setCurrentIndex(selected[0].data(0, Qt.ItemDataRole.UserRole))

    def refresh_windows(self):
        w = self.window
        if not hasattr(w, 'window_list'):
            return
        tree = w.window_list
        with QSignalBlocker(tree):
            while tree.topLevelItemCount() > w.tabs.count():
                tree.takeTopLevelItem(tree.topLevelItemCount() - 1)
            for index in range(w.tabs.count()):
                workspace = w.tabs.widget(index)
                panes = [workspace.widget(i) for i in range(workspace.count())]
                for pane in panes:
                    pane.disconnect_button.setEnabled(pane.mode == 'Remote' and pane.site is not None and not pane.busy)
                endpoints = [p.site.name or p.site.host if p.mode == 'Remote' and p.site else p.mode for p in panes]
                jobs = [job for job in w.queue_jobs if job['workspace'] is workspace]
                active = sum(job.get('state') == 'active' for job in jobs)
                queued = sum(job.get('state') == 'queued' for job in jobs)
                failed = sum(job.get('state') == 'failed' for job in jobs)
                state = (f'Transferring ({active})' if active else 'Browsing…' if any(p.busy for p in panes) else
                         f'Queued ({queued})' if queued else f'Failed ({failed})' if failed else 'Idle')
                row = tree.topLevelItem(index)
                if row is None:
                    row = QTreeWidgetItem()
                    tree.addTopLevelItem(row)
                for column, text in enumerate((w.tabs.tabText(index), ' ↔ '.join(endpoints), state)):
                    if row.text(column) != text:
                        row.setText(column, text)
                row.setData(0, Qt.ItemDataRole.UserRole, index)
                row.setSelected(index == w.tabs.currentIndex())

    def save_width(self, tree, column, width):
        group = tree._view_group
        if not self.applying and not getattr(tree, '_fitting_columns', False) and not tree.isColumnHidden(column):
            # A user's drag takes precedence over automatic fitting. Keep the
            # existing group preference, but never lock the header handles.
            if self.value('auto_size/' + group):
                self.settings.setValue('Display/auto_size/' + group, False)
                action = self.actions.get('auto_size/' + group)
                if action:
                    with QSignalBlocker(action): action.setChecked(False)
                for i in range(tree.columnCount()):
                    if not tree.isColumnHidden(i):
                        self.settings.setValue(f'Display/column_width/{group}/{i}', tree.header().sectionSize(i))
            self.settings.setValue(f'Display/column_width/{group}/{column}', width)

    def apply_tree(self, tree, group):
        if not hasattr(tree, '_view_group'):
            from .column_sizing import ColumnSizing
            tree.setItemDelegate(GridDelegate(tree, self.settings))
            tree.header().sectionResized.connect(lambda column, old, width: self.save_width(tree, column, width))
            tree._column_sizing = ColumnSizing(tree, self)
        tree._view_group = group
        auto = self.value('auto_size/' + group)
        if group == 'remote':
            visible = [True] + [self.value('remote_columns/' + key, key not in ('user', 'group'))
                                for key in ('size', 'date', 'attrib', 'user', 'group')]
        elif group == 'local':
            visible = [True, self.value('local_columns/size'), self.value('local_columns/date'), False, False, False]
        elif group == 'windows':
            visible = [True, self.value('windows_columns/ftp'), self.value('windows_columns/action')]
        else:
            visible = [True] * tree.columnCount()
        signature = (group, auto, tuple(visible))
        if getattr(tree, '_view_signature', None) != signature:
            old_signature = getattr(tree, '_view_signature', None)
            tree._view_signature = signature
            header = tree.header()
            header.setStretchLastSection(False)
            for column, show in enumerate(visible):
                tree.setColumnHidden(column, not show)
                header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
                if old_signature is None or old_signature[0] != group:
                    key = f'Display/column_width/{group}/{column}'
                    defaults = ((200, 80, 90, 110, 90, 240, 240, 220) if group == 'queue' else
                                (280, 90, 160, 90, 90, 90))
                    width = self.settings.value(key, defaults[column], type=int)
                    header.resizeSection(column, max(30, min(4000, width)))
        if auto:
            tree._column_sizing.schedule()
        tree.viewport().update()

    def apply(self):
        w = self.window
        if not hasattr(w, 'tabs'):
            return
        self.applying = True
        try:
            for index in range(w.tabs.count()):
                workspace = w.tabs.widget(index)
                for pane in (workspace.widget(0), workspace.widget(1)):
                    self.apply_tree(pane.tree, pane.mode.lower())
            if hasattr(w, 'queue'):
                self.apply_tree(w.queue, 'queue')
            if hasattr(w, 'window_list'):
                self.apply_tree(w.window_list, 'windows')
                self.refresh_windows()
        finally:
            self.applying = False
