"""Download verified releases in the background and install them on exit."""
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
        self._download(offer)

    def _download(self, offer):
        from .app_updates import download_update
        self.window.statusBar().showMessage(f'Downloading FluxFTP {offer.version}…')
        self._run(lambda _worker: download_update(offer),
                  lambda path: self._downloaded(path, offer), self._install_failed)

    def _downloaded(self, path, offer):
        self.pending_update = (path, offer)
        self.window.statusBar().showMessage(
            f'FluxFTP {offer.version} is downloaded and verified. It will install when you exit.', 15000)
        self.window.log.appendPlainText(
            f'FluxFTP {offer.version} is ready. The update will install when you exit; Windows may ask for administrator approval.')

    def install_pending(self):
        """Begin installation after a confirmed close; return whether the window may close."""
        pending = getattr(self, 'pending_update', None)
        if pending is None:
            return True
        if getattr(self, '_installing', False):
            return False
        path, offer = pending
        if self.window_thread_platform().startswith('linux'):
            try:
                release = __import__('platform').freedesktop_os_release()
            except (AttributeError, OSError):
                release = {}
            if release.get('ID', '').lower() == 'debian':
                from .app_updates import start_debian_installer
                import os
                self._installing = True
                self._run(lambda _worker: start_debian_installer(
                    path.path, offer, os.getpid(), path.sha256),
                    self._debian_installer_started, self._debian_installer_failed)
                return False
        try:
            self._start_platform_installer(path, offer)
            self.pending_update = None
            return True
        except Exception as error:
            self._install_failed(error)
            return False

    def _start_platform_installer(self, path, offer):
        from .app_updates import start_ubuntu_installer, start_windows_installer
        import os
        archive = path.path
        if self.window_thread_platform() == 'win32':
            start_windows_installer(archive, offer, self.install_root, os.getpid(), path.sha256)
        elif self.window_thread_platform().startswith('linux'):
            start_ubuntu_installer(archive, offer, self.install_root, os.getpid())

    def _debian_installer_started(self, _result):
        self.pending_update = None
        self._installing = False
        self.window.close()

    def _debian_installer_failed(self, error):
        self._installing = False
        self._install_failed(error)

    @staticmethod
    def window_thread_platform():
        import sys
        return sys.platform

    def _check_failed(self, error):
        # Startup update checks are best effort and should not interrupt normal use.
        self.window.statusBar().showMessage(f'Could not check for FluxFTP updates: {error}', 8000)

    def _install_failed(self, error):
        self.window.statusBar().showMessage('FluxFTP update failed.', 10000)
        QMessageBox.warning(self.window, 'FluxFTP update failed', str(error))
