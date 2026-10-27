"""File-menu workspace, tray and configuration commands."""
from dataclasses import replace
from pathlib import Path
from PySide6.QtWidgets import QFileDialog, QMessageBox, QSystemTrayIcon
from .config_backup import configuration, write_json, read_configuration, restore_configuration


class FileMenu:
    def update_file_menu(self):
        panes = self.current_panes()
        busy = not panes or any(pane.busy for pane in panes)
        workspace = self.tabs.currentWidget()
        active = any(record['workspace'] is workspace for record in self.active.values())
        editing = any(job['workspace'] is workspace and job.get('editing') for job in self.queue_jobs)
        self.close_window_action.setEnabled(self.tabs.count() > 1 and not busy and not active and not editing)
        self.clone_window_action.setEnabled(not busy)

    def new_window_of_current(self):
        panes = self.current_panes()
        if not panes or any(pane.busy for pane in panes) or self.tabs.count() >= 100:
            return
        title = self.workspace_name(self.tabs.tabText(self.tabs.currentIndex())[:68] + ' (copy)')
        original = self.tabs.currentWidget()
        splitter_state = getattr(original, '_workspace_saved_state', original.saveState())
        states = [dict(mode=pane.mode, path=pane.path, local_path=pane.local_path,
                       site=replace(pane.site) if pane.site else None, password=pane.password,
                       remote_site=replace(pane.remote_site) if pane.remote_site else None,
                       remote_password=pane.remote_password, remote_path=pane.remote_path,
                       filter=pane.filter.text()) for pane in panes]
        for source, state in zip(panes, states):
            if source.site is source.remote_site:
                state['remote_site'] = state['site']
        self.add_workspace()
        self.tabs.setTabText(self.tabs.currentIndex(), title)
        self.tabs.currentWidget().restoreState(splitter_state)
        for pane, state in zip(self.current_panes(), states):
            pane.local_path = state['local_path']
            if state['mode'] == 'Remote':
                pane.set_remote(state['site'], state['password'], state['path'])
                pane.refresh()
            else:
                pane.mode = 'Local'
                pane.site, pane.password = None, ''
                pane._set_mode_button('Local')
                pane.title_label.setText(f'({pane.number}) Local')
                pane.navigate(state['path'])
            pane.remote_site, pane.remote_password, pane.remote_path = state['remote_site'], state['remote_password'], state['remote_path']
            pane.filter.setText(state['filter'])
        self.apply_display_settings()
        self.refresh_workspace_names()
        self.save_workspaces()

    def file_minimize_to_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self.showMinimized()
            self.statusBar().showMessage('System tray unavailable; minimized to the taskbar.', 5000)
            return
        self.tray.show()
        self.showMinimized()
        self.hide()
        self.update_tray()

    def backup_configuration(self):
        path, _ = QFileDialog.getSaveFileName(self, 'Backup configuration now',
            'fluxftp-configuration.json', 'FluxFTP configuration (*.json)')
        if not path:
            return
        try:
            if Path(path).resolve() == self.sites_file.resolve() or (hasattr(self, 'queue_file') and Path(path).resolve() == self.queue_file.resolve()):
                raise ValueError('Choose a backup file separate from the live site and queue files.')
            write_json(path, configuration(self.preferences, self.sites))
            self.statusBar().showMessage('Configuration backed up. Passwords and key material are not included.', 10000)
        except (OSError, ValueError, TypeError) as error:
            self.error(str(error))

    def can_restore_configuration(self):
        if self.workers or self.pending or self.active or self.mouse_windows:
            return False
        for index in range(self.tabs.count()):
            split = self.tabs.widget(index)
            if any(split.widget(side).busy or split.widget(side).site or split.widget(side).remote_site for side in range(2)):
                return False
        return True

    def restore_configuration_backup(self):
        if not self.can_restore_configuration():
            self.error('Finish or cancel work, disconnect sites and close file-view/edit sessions before restoring configuration.')
            return
        path, _ = QFileDialog.getOpenFileName(self, 'Restore configuration from backup file', '', 'FluxFTP configuration (*.json)')
        if not path:
            return
        try:
            data = read_configuration(path)
            if QMessageBox.question(self, 'Restore configuration',
                    f"Replace configuration with {len(data['sites'])} saved site(s) and {len(data['settings'])} preferences?\n"
                    'Passwords are not included; re-enter them after restore. The current data-folder location is retained.\n'
                    'A recovery backup will be saved. FluxFTP will exit; reopen it to use the restored configuration.',
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
            if not self.can_restore_configuration():
                raise ValueError('Work started while the prompt was open. Restore cancelled.')
            recovery = restore_configuration(self, data)
            self._configuration_restored = True
            self.statusBar().showMessage(f'Configuration restored. Recovery backup: {recovery}')
            self.close()
        except (OSError, ValueError, TypeError) as error:
            self.error(str(error))
