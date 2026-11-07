"""UI actions for credential-free configuration export and restore."""
from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QMessageBox

from .config_backup import configuration, read_configuration, restore_configuration, write_json


class ConfigurationBackupActions:
    """Configuration backup operations implemented against the owning window."""
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
