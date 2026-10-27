"""Workspace names and layout persistence; never serialize live connections."""
import json
from pathlib import Path
from PySide6.QtCore import Qt, QByteArray, QSignalBlocker, QTimer
from PySide6.QtWidgets import QInputDialog, QMenu, QMessageBox
from shiboken6 import isValid


class Workspaces:
    def initialize_workspaces(self):
        self._restoring_workspaces = True
        try:
            raw = self.preferences.value('Workspaces/session', '', type=str)
            saved = json.loads(raw) if raw and len(raw) < 1000000 else {}
            items = saved.get('items', []) if isinstance(saved, dict) else []
            if not isinstance(items, list) or not 1 <= len(items) <= 100:
                items = []
            for index, item in enumerate(items):
                if not isinstance(item, dict):
                    continue
                while self.tabs.count() <= index:
                    self.add_workspace()
                split = self.tabs.widget(index)
                name = item.get('name')
                if isinstance(name, str) and name.strip() and len(name) <= 80 and not any(ord(c) < 32 for c in name):
                    self.tabs.setTabText(index, name.strip())
                modes = item.get('modes', [])
                for side, mode in enumerate(modes[:2] if isinstance(modes, list) else []):
                    if mode in ('Local', 'Remote'):
                        split.widget(side).change_mode(mode)
                state = item.get('splitter', '')
                if isinstance(state, str) and len(state) < 16384:
                    try:
                        split._workspace_saved_state = QByteArray(bytes.fromhex(state))
                        split.restoreState(split._workspace_saved_state)
                    except ValueError:
                        pass
            current = saved.get('current', 0) if isinstance(saved, dict) else 0
            if type(current) is int:
                self.tabs.setCurrentIndex(max(0, min(self.tabs.count() - 1, current)))
        except (ValueError, TypeError):
            pass
        finally:
            self._restoring_workspaces = False
        self._workspaces_ready = True
        self.tabs.currentChanged.connect(self.save_workspaces)
        self.tabs.currentChanged.connect(self.restore_workspace_layout)
        self.window_list.itemDoubleClicked.connect(
            lambda row, column: self.rename_workspace(row.data(0, Qt.ItemDataRole.UserRole)) if column == 0 else None)
        self.window_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.window_list.customContextMenuRequested.connect(self.workspace_list_menu)
        self.refresh_workspace_names()

    def showEvent(self, event):
        super().showEvent(event)
        self.restore_workspace_layout()

    def restore_workspace_layout(self, *_):
        split = self.tabs.currentWidget()
        def restore():
            if (isValid(self) and isValid(split) and self.isVisible() and self.tabs.currentWidget() is split
                    and hasattr(split, '_workspace_saved_state')):
                split.restoreState(split._workspace_saved_state)
                del split._workspace_saved_state
        QTimer.singleShot(0, restore)

    def save_workspaces(self, *_):
        if (not getattr(self, '_workspaces_ready', False) or getattr(self, '_restoring_workspaces', False)
                or getattr(self, '_smoke_test', False) or getattr(self, '_configuration_restored', False)):
            return
        items = []
        for index in range(self.tabs.count()):
            split = self.tabs.widget(index)
            items.append(dict(name=self.tabs.tabText(index),
                              modes=[split.widget(side).mode for side in range(2)],
                              splitter=bytes(getattr(split, '_workspace_saved_state', split.saveState())).hex()))
        self.preferences.setValue('Workspaces/session', json.dumps(dict(items=items, current=self.tabs.currentIndex())))
        self.preferences.sync()

    def refresh_workspace_names(self):
        if hasattr(self, 'workspace_navigation'):
            self.workspace_navigation.signature = None
            self.workspace_navigation.rebuild()
        if hasattr(self, 'window_list'):
            self.view_commands.options.refresh_windows()

    def workspace_name(self, base='Workspace'):
        names = {self.tabs.tabText(i).casefold() for i in range(self.tabs.count())}
        if base != 'Workspace' and base.casefold() not in names:
            return base
        index = 1
        while f'{base} {index}'.casefold() in names:
            index += 1
        return f'{base} {index}'

    def update_connected_workspace_name(self, pane):
        for index in range(self.tabs.count()):
            split = self.tabs.widget(index)
            panes = [split.widget(side) for side in range(2)]
            if pane not in panes:
                continue
            names = []
            for current in panes:
                site = current.site
                if (current.mode == 'Remote' and site is not None
                        and getattr(current, '_workspace_connected_site', None) is site):
                    name = ' '.join((site.name or site.host).split())
                    if name:
                        names.append(name)
            title = ' - '.join(names)[:80] if names else self.workspace_name()
            self.tabs.setTabText(index, title)
            self.refresh_workspace_names()
            self.save_workspaces()
            return

    def rename_workspace(self, index=None):
        index = self.tabs.currentIndex() if index is None else index
        workspace = self.tabs.widget(index)
        if workspace is None:
            return
        name, accepted = QInputDialog.getText(self, 'Rename workspace', 'Workspace name (up to 80 characters):',
                                             text=self.tabs.tabText(index))
        if not accepted:
            return
        name = name.strip()
        index = self.tabs.indexOf(workspace)
        if index < 0:
            return
        if not name or len(name) > 80 or any(ord(c) < 32 for c in name):
            self.error('Enter a workspace name containing 1–80 characters and no control characters.')
            return
        if any(i != index and self.tabs.tabText(i).casefold() == name.casefold() for i in range(self.tabs.count())):
            self.error('Another workspace already uses that name.')
            return
        self.tabs.setTabText(index, name)
        self.refresh_workspace_names()
        self.save_workspaces()

    def workspace_busy(self, workspace):
        return (getattr(workspace, '_manual_download_pending', False)
                or any(workspace.widget(i).busy for i in range(2))
                or any(record['workspace'] is workspace for record in self.active.values())
                or any(job['workspace'] is workspace and (job.get('editing') or job.get('state') == 'active')
                       for job in self.queue_jobs))

    def workspace_menu(self, workspace, position):
        if self.tabs.indexOf(workspace) < 0:
            return
        menu = QMenu(self)
        menu.addAction('Rename', lambda: self.rename_workspace(self.tabs.indexOf(workspace)))
        duplicate = menu.addAction('Duplicate', lambda: self.duplicate_workspace(workspace))
        duplicate.setEnabled(not any(workspace.widget(i).busy for i in range(2)) and self.tabs.count() < 100)
        menu.addSeparator()
        close = menu.addAction('Close', lambda: self.close_tab(self.tabs.indexOf(workspace)))
        close.setEnabled(self.tabs.count() > 1 and not self.workspace_busy(workspace))
        others = menu.addAction('Close Others', lambda: self.close_other_workspaces(workspace))
        others.setEnabled(self.tabs.count() > 1)
        menu.exec(position)
        menu.deleteLater()

    def workspace_list_menu(self, point):
        row = self.window_list.itemAt(point)
        if row:
            workspace = self.tabs.widget(row.data(0, Qt.ItemDataRole.UserRole))
            self.workspace_menu(workspace, self.window_list.viewport().mapToGlobal(point))

    def duplicate_workspace(self, workspace):
        index = self.tabs.indexOf(workspace)
        if index >= 0 and self.tabs.count() < 100:
            self.tabs.setCurrentIndex(index)
            self.new_window_of_current()

    def close_other_workspaces(self, keep):
        if self.tabs.indexOf(keep) < 0:
            return
        self.tabs.setCurrentWidget(keep)
        for index in reversed(range(self.tabs.count())):
            workspace = self.tabs.widget(index)
            if workspace is keep or self.workspace_busy(workspace):
                continue
            if not self.close_tab(index):
                break  # A declined confirmation cancels the remaining closes.
        if self.tabs.count() > 1:
            self.statusBar().showMessage('Some workspaces remain open because they are busy or closing was cancelled.', 7000)

    def reset_layout(self):
        if QMessageBox.question(self, 'Reset layout',
                'Restore default panel sizes, column widths and toolbar arrangement?',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        from .layout_options import DEFAULTS
        if hasattr(self, 'docking'):
            self.docking.action.setChecked(False)
            self.preferences.remove('Layout/dock_state')
        keys = ('Layout/panel_order', 'Layout/panel_positions', 'Layout/manual_panel_positions',
                'Layout/pane_positions', 'Layout/header_row_order', 'Layout/header_row_groups',
                'Layout/header_band_widths', 'Layout/workspace_list_height', 'Layout/toolbar_icon_size',
                'Layout/active_log', 'Layout/toolbars', 'Display/column_width', 'Display/auto_size')
        for key in keys:
            self.preferences.remove(key)
        for key in ('navigate_tabs', 'multiple_lines', 'tabs_bottom', 'limit_tab_width', 'large_icons'):
            self.preferences.setValue('Layout/' + key, DEFAULTS[key])
        self.preferences.setValue('Display/pane_layout', 'Side by side')
        self._manual_panel_layout = False
        for index, panel in enumerate((self.workspace_panel, self.log_tabs, self.queue_panel)):
            self.work_area.insertWidget(index, panel)
        self.work_area.setSizes([350, 320, 300])
        self.log_tabs.setCurrentWidget(self.log_panels['ftp'])
        trees = [self.queue, self.window_list]
        for index in range(self.tabs.count()):
            split = self.tabs.widget(index)
            if hasattr(split, '_workspace_saved_state'):
                del split._workspace_saved_state
            split.setOrientation(Qt.Orientation.Horizontal)
            split.setSizes([1, 1])
            trees.extend(split.widget(side).tree for side in range(2))
        self.header_rows.reset()
        self.header_rows.rows['overview'].set_height(100)
        for tree in trees:
            tree._view_signature = None
        for key, action in self.view_commands.options.actions.items():
            if key.startswith('auto_size/'):
                with QSignalBlocker(action):
                    action.setChecked(True)
        self.apply_layout_settings()
        self.view_commands.options.apply()
        self.save_panel_layout()
        self.save_workspaces()
        self.statusBar().showMessage('Default layout restored.', 5000)
