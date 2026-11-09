"""Prompt for verified FluxFTP releases and hand installation off safely."""
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMessageBox

from .worker import Worker


class ApplicationUpdates:
    def __init__(self, window):
        self.window = window
        self.install_root = None
        self.worker = None
        from .app_updates import supported_install_root
        self.install_root = supported_install_root()
        if self.install_root is not None and window.preferences.value('General/update_check', True, type=bool):
            QTimer.singleShot(2500, self.check)

    def check(self):
        from .app_updates import check_latest
        from . import __version__
        self._run(lambda _worker: check_latest(__version__), self._offer, self._check_failed)

    def _run(self, function, done, failed):
        self.worker = Worker(function, self.window)
        self.worker.result.connect(done)
        self.worker.failed.connect(failed)
        self.worker.start()

    def _offer(self, offer):
        if offer is None:
            return
        prefs = self.window.preferences
        if prefs.value('General/ignored_update', '', type=str) == offer.version:
            return
        prompt = QMessageBox(self.window)
        prompt.setIcon(QMessageBox.Icon.Information)
        prompt.setWindowTitle('FluxFTP update available')
        prompt.setText(f'FluxFTP {offer.version} is available. Would you like to download and install it now?')
        if offer.notes:
            prompt.setInformativeText(offer.notes[:2500])
        update = prompt.addButton('Update now', QMessageBox.ButtonRole.AcceptRole)
        later = prompt.addButton('Later', QMessageBox.ButtonRole.RejectRole)
        prompt.setDefaultButton(update)
        prompt.exec()
        if prompt.clickedButton() is update:
            self._download(offer)
        elif prompt.clickedButton() is later:
            prefs.setValue('General/ignored_update', offer.version)

    def _download(self, offer):
        from .app_updates import download_update
        self.window.statusBar().showMessage(f'Downloading FluxFTP {offer.version}…')
        self._run(lambda _worker: download_update(offer),
                  lambda path: self._install(path, offer), self._install_failed)

    def _install(self, path, offer):
        from .app_updates import start_debian_installer, start_ubuntu_installer, start_windows_installer
        import os
        try:
            if self.window_thread_platform() == 'win32':
                start_windows_installer(path, offer, self.install_root, os.getpid())
            elif self.window_thread_platform().startswith('linux'):
                try:
                    release = __import__('platform').freedesktop_os_release()
                except (AttributeError, OSError):
                    release = {}
                if release.get('ID', '').lower() == 'debian':
                    installer = Worker(lambda _worker: start_debian_installer(
                        path, offer, os.getpid()), self.window)
                    installer.result.connect(lambda _: self._install_started())
                    installer.failed.connect(self._install_failed)
                    self.worker = installer
                    installer.start()
                    return
                start_ubuntu_installer(path, offer, self.install_root, os.getpid())
            self._install_started()
        except Exception as error:
            self._install_failed(error)

    @staticmethod
    def window_thread_platform():
        import sys
        return sys.platform

    def _install_started(self):
        self.window.statusBar().showMessage('Update prepared. FluxFTP will restart to finish installing.', 10000)
        QMessageBox.information(self.window, 'FluxFTP update',
            'The verified update is ready. FluxFTP will close and restart to finish installation.')
        self.window.close()

    def _check_failed(self, error):
        # Startup update checks are best effort and should not interrupt normal use.
        self.window.statusBar().showMessage(f'Could not check for FluxFTP updates: {error}', 8000)

    def _install_failed(self, error):
        self.window.statusBar().showMessage('FluxFTP update failed.', 10000)
        QMessageBox.warning(self.window, 'FluxFTP update failed', str(error))
