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
            saved_site_names = set()
            for site_key, profile in self.sites.items():
                if isinstance(profile, dict):
                    for candidate in (site_key, profile.get('name'), profile.get('host')):
                        if isinstance(candidate, str) and candidate.strip():
                            saved_site_names.add(candidate.strip().casefold())
            for index, item in enumerate(items):
                if not isinstance(item, dict):
                    continue
                while self.tabs.count() <= index:
                    self.add_workspace()
                split = self.tabs.widget(index)
                name = item.get('name')
                if isinstance(name, str) and name.strip() and len(name) <= 80 and not any(ord(c) < 32 for c in name):
                    name = name.strip()
                    parts = [part.strip().casefold() for part in name.split(' - ')]
                    if parts and all(part in saved_site_names for part in parts):
                        name = f'Workspace {index + 1}'
                    self.tabs.setTabText(index, name)
                split.setProperty('auto_workspace_name', bool(item.get('auto_name', name == f'Workspace {index + 1}')))
                group = item.get('duplicate_group')
                if isinstance(group, str) and group:
                    split.setProperty('duplicate_group', group[:80])
                modes = item.get('modes', [])
                for side, mode in enumerate(modes[:2] if isinstance(modes, list) else []):
                    if mode in ('Local', 'Remote'):
                        split.widget(side).change_mode(mode)
                self.apply_pane_order(split)
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
            items.append(dict(name=self.tabs.tabText(index), auto_name=bool(split.property('auto_workspace_name')),
                              duplicate_group=split.property('duplicate_group'),
                              modes=[split.widget(side).mode for side in range(2)],
                              splitter=bytes(getattr(split, '_workspace_saved_state', split.saveState())).hex()))
        self.preferences.setValue('Workspaces/session', json.dumps(dict(items=items, current=self.tabs.currentIndex())))
        self.preferences.sync()

    def workspace_preset_data(self, name):
        """Capture tabs, site references, pane folders and global layout without credentials."""
        items = []
        for index in range(self.tabs.count()):
            split = self.tabs.widget(index)
            panes = []
            for side in range(2):
                pane = split.widget(side)
                site = pane.site if pane.site is not None else getattr(pane, 'remote_site', None)
                bookmark = ''
                if site is not None:
                    for key, profile in self.sites.items():
                        if not isinstance(profile, dict):
                            continue
                        stored = self.site_from_profile(profile)
                        if (stored.host.casefold(), stored.port, stored.username.casefold(), stored.protocol) == (
                                site.host.casefold(), site.port, site.username.casefold(), site.protocol):
                            bookmark = key
                            break
                panes.append(dict(mode=pane.mode, bookmark=bookmark,
                    local_path=str(getattr(pane, 'local_path', '') or ''),
                    remote_path=str(getattr(pane, 'remote_path', '') or (site.root if site else '/'))))
            items.append(dict(name=self.tabs.tabText(index), auto_name=bool(split.property('auto_workspace_name')),
                modes=[pane['mode'] for pane in panes], panes=panes,
                splitter=bytes(split.saveState()).hex()))
        layout = {}
        for key in self.preferences.allKeys():
            if key.startswith(('Layout/', 'Display/')) and not key.startswith('Layout/saved_layouts'):
                layout[key] = self.preferences.value(key)
        return dict(format=1, name=name, items=items, current=self.tabs.currentIndex(), layout=layout)

    @staticmethod
    def validate_workspace_preset(preset):
        """Validate a persisted preset before applying any part of it."""
        if (not isinstance(preset, dict) or preset.get('format') != 1 or
                not isinstance(preset.get('name'), str) or not 1 <= len(preset['name']) <= 80 or
                not isinstance(preset.get('items'), list) or not 1 <= len(preset['items']) <= 100 or
                not isinstance(preset.get('layout', {}), dict)):
            raise ValueError('Invalid workspace preset.')
        names = set()
        for item in preset['items']:
            if (not isinstance(item, dict) or not isinstance(item.get('name'), str) or
                    not 1 <= len(item['name']) <= 80 or any(ord(char) < 32 for char in item['name']) or
                    item['name'].casefold() in names or not isinstance(item.get('panes'), list) or
                    len(item['panes']) != 2 or not isinstance(item.get('splitter', ''), str) or
                    len(item.get('splitter', '')) > 32768):
                raise ValueError('Invalid workspace preset tab.')
            names.add(item['name'].casefold())
            for pane in item['panes']:
                if (not isinstance(pane, dict) or pane.get('mode') not in ('Local', 'Remote') or
                        not isinstance(pane.get('bookmark', ''), str) or
                        not isinstance(pane.get('local_path', ''), str) or
                        not isinstance(pane.get('remote_path', ''), str) or
                        len(pane.get('local_path', '')) > 32768 or len(pane.get('remote_path', '')) > 32768):
                    raise ValueError('Invalid workspace preset pane.')
        for key in preset.get('layout', {}):
            if (not isinstance(key, str) or not key.startswith(('Layout/', 'Display/')) or
                    key.startswith('Layout/saved_layouts')):
                raise ValueError('Workspace preset contains an unsupported layout setting.')
        return preset

    def apply_workspace_preset(self, preset):
        """Replace current tabs after explicit approval; restore saved site references and folders."""
        preset = self.validate_workspace_preset(preset)
        workspaces = [self.tabs.widget(index) for index in range(self.tabs.count())]
        if any(self.workspace_busy(split) for split in workspaces):
            self.statusBar().showMessage('Wait for active transfers or editors before switching workspace presets.', 7000)
            return False
        jobs = [job for job in self.queue_jobs if job.get('workspace') in workspaces and
                job.get('state') in ('queued', 'failed')]
        connected = any(pane.site or pane.remote_site for split in workspaces for pane in
                        (split.widget(0), split.widget(1)))
        warning = 'Switching replaces the open workspace tabs.'
        if jobs:
            warning += f' It removes {len(jobs)} queued or failed queue item(s).'
        if connected:
            warning += ' Saved-site connections will be closed; the preset can reconnect its saved sites.'
        if QMessageBox.question(self, 'Apply workspace preset', warning + ' Continue?') != QMessageBox.StandardButton.Yes:
            return False
        if jobs:
            self.discard_queue_jobs(jobs)
        self._restoring_workspaces = True
        missing_sites = set()
        try:
            for split in workspaces:
                for side in range(2):
                    split.widget(side).set_remote(None)
            for index in reversed(range(1, self.tabs.count())):
                split = self.tabs.widget(index)
                self.tabs.removeTab(index)
                self.panel_manager.remove(split)
                split.deleteLater()
            first = self.tabs.widget(0)
            for side in range(2):
                pane = first.widget(side)
                pane.change_mode('Local')
                pane.navigate(pane.local_path)
            for key, value in preset.get('layout', {}).items():
                self.preferences.setValue(key, value)
            if hasattr(self, 'layout_commands'):
                self.layout_commands.apply_layout_values(preset.get('layout', {}), report=False)
            else:
                self.apply_layout_settings()
            for index, item in enumerate(preset['items']):
                if index:
                    self.add_workspace()
                split = self.tabs.widget(index)
                split.setProperty('auto_workspace_name', bool(item.get('auto_name', False)))
                self.tabs.setTabText(index, item['name'])
                for side, pane_spec in enumerate(item['panes']):
                    pane = split.widget(side)
                    pane.change_mode('Local')
                    pane.local_path = pane_spec['local_path'] or pane.local_path
                    bookmark = pane_spec['bookmark']
                    profile = self.sites.get(bookmark) if bookmark else None
                    if profile and profile.get('_disabled'):
                        missing_sites.add(bookmark + ' (disabled)')
                        profile = None
                    if profile:
                        remote_profile = dict(profile)
                        remote_profile['root'] = pane_spec['remote_path'] or '/'
                        if pane_spec['mode'] == 'Remote':
                            pane.connect_profile(remote_profile, bookmark)
                            if pane.mode != 'Remote':
                                pane.remote_site = self.site_from_profile(remote_profile)
                                pane.remote_path = remote_profile['root']
                                pane.remote_password = ''
                                pane.change_mode('Remote')
                                pane.path = remote_profile['root']
                                pane.refresh()
                        else:
                            pane.remote_site = self.site_from_profile(remote_profile)
                            pane.remote_path = remote_profile['root']
                            try:
                                pane.remote_password = self.get_site_password(profile)
                            except RuntimeError:
                                pane.remote_password = ''
                    elif bookmark:
                        missing_sites.add(bookmark)
                    if pane_spec['mode'] == 'Remote' and not profile:
                        if not bookmark:
                            missing_sites.add(f'{item["name"]} pane {side + 1} (unsaved site)')
                        pane.remote_site = None
                        pane.remote_password = ''
                        pane.change_mode('Remote')
                        pane.path = pane_spec['remote_path'] or '/'
                        pane.refresh()
                    if pane_spec['mode'] == 'Local':
                        pane.navigate(pane.local_path)
                state = item.get('splitter', '')
                if state:
                    from PySide6.QtCore import QByteArray
                    try:
                        split._workspace_saved_state = QByteArray(bytes.fromhex(state))
                    except ValueError:
                        pass
            current = preset.get('current', 0)
            self.tabs.setCurrentIndex(current if type(current) is int and 0 <= current < self.tabs.count() else 0)
            if hasattr(self, 'layout_commands'):
                self.layout_commands.apply_layout_values(preset.get('layout', {}), report=False)
            else:
                self.apply_layout_settings()
            self.restore_workspace_layout()
        finally:
            self._restoring_workspaces = False
        self.refresh_workspace_names()
        self.save_workspaces()
        detail = (' Missing saved sites: ' + ', '.join(sorted(missing_sites)) + '.' if missing_sites else '')
        self.statusBar().showMessage(f"Workspace preset ‘{preset['name']}’ applied.{detail}", 9000)
        return True

    def apply_pane_order(self, split=None):
        order = self.preferences.value('Display/pane_order', 'Site then Local', type=str)
        splitters = [split] if split is not None else [self.tabs.widget(i) for i in range(self.tabs.count())]
        for workspace in splitters:
            if workspace is None or workspace.count() != 2:
                continue
            first, second = workspace.widget(0), workspace.widget(1)
            if first.mode == second.mode:
                if first.number > second.number:
                    workspace.insertWidget(0, second)
                continue
            local_first = order == 'Local then Site'
            should_swap = (local_first and first.mode != 'Local' and second.mode == 'Local'
                           or not local_first and first.mode == 'Local' and second.mode != 'Local')
            if should_swap:
                workspace.insertWidget(0, second)

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
        workspace = next((self.tabs.widget(i) for i in range(self.tabs.count())
                          if any(self.tabs.widget(i).widget(side) is pane for side in range(2))), None)
        if workspace is None or not workspace.property('auto_workspace_name'):
            return
        index = self.tabs.indexOf(workspace)
        connected = []
        for side in range(2):
            side_pane = workspace.widget(side)
            site = getattr(side_pane, '_workspace_connected_site', None)
            if site is not None:
                connected.append(site.name.strip() or site.host.strip())
        if connected:
            name = ' | '.join(connected)
            if len(name) > 80:
                name = name[:77].rstrip() + '…'
        else:
            name = f'Workspace {index + 1}'
        if self.tabs.tabText(index) != name:
            self.tabs.setTabText(index, name)
            self.refresh_workspace_names()
            self.save_workspaces()

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
        workspace.setProperty('auto_workspace_name', False)
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
        self.layout_commands.apply_popup_menu(menu, 'window')
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
            group = workspace.property('duplicate_group') or f'duplicate-{id(workspace)}'
            workspace.setProperty('duplicate_group', group)
            self.tabs.setCurrentIndex(index)
            self.new_window_of_current()
            duplicate = self.tabs.currentWidget()
            if duplicate is not workspace:
                new_index = self.tabs.indexOf(duplicate)
                if new_index > index + 1:
                    self.tabs.tabBar().moveTab(new_index, index + 1)
                duplicate.setProperty('duplicate_group', group)
                self.save_workspaces()
                self.refresh_workspace_names()

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
            for key in self.preferences.allKeys():
                if key.startswith('Layout/dock_geometry/'):
                    self.preferences.remove(key)
        keys = ('Layout/panel_order', 'Layout/panel_positions', 'Layout/manual_panel_positions',
                'Layout/pane_positions', 'Layout/header_row_order', 'Layout/header_row_groups',
                'Layout/header_band_widths', 'Layout/workspace_list_height', 'Layout/toolbar_icon_size',
                'Layout/active_log', 'Layout/toolbars', 'Display/column_width', 'Display/auto_size')
        for key in keys:
            self.preferences.remove(key)
        for key in ('navigate_tabs', 'multiple_lines', 'tabs_bottom', 'limit_tab_width', 'large_icons'):
            self.preferences.setValue('Layout/' + key, DEFAULTS[key])
        self.preferences.setValue('Display/pane_layout', 'Side by side')
        self.preferences.setValue('Display/pane_order', 'Site then Local')
        self._manual_panel_layout = False
        for index, panel in enumerate((self.workspace_panel, self.log_tabs, self.queue_panel)):
            self.work_area.insertWidget(index, panel)
        self.work_area.setSizes([350, 320, 300])
        self.panel_manager.sets[self.tabs.currentWidget()]['active_log'] = 'ftp'
        self.log_tabs.setCurrentWidget(self.log_stacks['ftp'])
        trees = [*self.panel_manager.queue_trees(), self.window_list]
        for index in range(self.tabs.count()):
            split = self.tabs.widget(index)
            if hasattr(split, '_workspace_saved_state'):
                del split._workspace_saved_state
            split.setOrientation(Qt.Orientation.Horizontal)
            self.apply_pane_order(split)
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
        if hasattr(self, 'docking'):
            self.docking.action.setChecked(True)
        self.save_panel_layout()
        self.save_workspaces()
        self.statusBar().showMessage('Default layout restored.', 5000)
