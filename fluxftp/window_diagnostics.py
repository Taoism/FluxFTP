"""Main-window controls for opt-in application diagnostics."""
import os
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QMessageBox, QPushButton

from . import diagnostics


class WindowDiagnostics:
    """Keep debug-log setup and its controls outside the window composition root."""
    def start_debug_logging(self):
        """Start opt-in application and Qt runtime logging for this session."""
        if diagnostics.log_path is None:
            path = (Path(self.standard_paths.writableLocation(
                self.standard_paths.StandardLocation.AppLocalDataLocation)) / "logs" /
                f"fluxftp-debug-{datetime.now():%Y%m%d-%H%M%S}-{os.getpid()}.log")
            try:
                diagnostics.configure_debug(path, self.app_instance)
            except OSError as error:
                QMessageBox.critical(self, "Cannot create debug log", str(error))
                return None
            diagnostics.install_exception_hook()
            diagnostics.logger.info("Debug logging enabled from the Tools menu.")
        else:
            path = diagnostics.log_path
        self.show_debug_log_controls(path)
        if hasattr(self, "tools_commands"):
            self.tools_commands.update()
        return path

    def show_debug_log_controls(self, path):
        self._debug_log_path = Path(path)
        if getattr(self, "_debug_copy_button", None):
            self._debug_copy_button.setToolTip(str(path))
            return
        self.log.appendPlainText(f"Debug logging enabled: {path}")
        self.log.appendPlainText(
            "Passwords are omitted; host names and file paths may appear in this log. Review it before sharing.")
        self._debug_copy_button = QPushButton("Copy debug log path")
        self._debug_copy_button.setToolTip(str(path))
        self._debug_copy_button.clicked.connect(
            lambda: self.app_instance.clipboard().setText(str(self._debug_log_path)))
        self.statusBar().addPermanentWidget(self._debug_copy_button)
        self._debug_folder_button = QPushButton("Open debug logs folder")
        self._debug_folder_button.clicked.connect(lambda: QDesktopServices.openUrl(
            QUrl.fromLocalFile(str(self._debug_log_path.parent))))
        self.statusBar().addPermanentWidget(self._debug_folder_button)
