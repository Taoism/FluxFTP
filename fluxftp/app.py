from __future__ import annotations

import json
import argparse
import os
import sys
import ssl
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QStandardPaths, QTimer, QSettings
from PySide6.QtGui import QBrush, QColor, QGuiApplication
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QLineEdit, QComboBox, QSpinBox, QCheckBox, QSplitter,
    QHeaderView, QAbstractItemView, QTabWidget, QPlainTextEdit, QMessageBox,
    QInputDialog, QToolBar, QStyle)

from .site_manager import SitesDialog
from .preferences import PreferencesDialog
from . import diagnostics
from . import credentials
from .ident import IdentService
from .transfer_options import TransferOptions, RateLimiter
from .site_options import SiteSettings, decode as site_options
from .queue_manager import QueueManager
from .display_options import DisplayOptions, AddressBar
from .sort_options import sorted_entries
from .color_options import ColorOptions
from .layout_options import LayoutOptions, CaptionPanel, WorkspaceTabBar
from .mouse_actions import MouseActions
from .confirm_options import ConfirmOptions
from .file_menu import FileMenu
from .workspaces import Workspaces
from .directory_cache import DirectoryCache
from .browse_session import BrowseSession
from .queue_tree import QueueTree, selected_rows
from .queue_menu import QueueMenu
from .compare_menu import CompareMenu, RESULT_ROLE
from .command_menu import CommandMenu
from .site_dialog import SiteDialog
from .transfer_queue import TransferQueue
from .connection_factory import ConnectionFactory
from .worker import Worker
from .configuration_persistence import ConfigurationPersistence
from .pane import Pane, TransferTree
from .window_chrome import WindowChrome
from .configuration_backup_ui import ConfigurationBackupActions
from .window_workers import WindowWorkers
from .window_connections import WindowConnections
from . import __version__


STYLE = """
QWidget { background:#f2f3f5; color:#17202a; }
QMainWindow { background:#e8edf5; }
QMenuBar { background:#f7f7f7; border-bottom:1px solid #c7cdd6; padding:2px 4px; }
QMenuBar::item { padding:4px 9px; background:transparent; }
QMenuBar::item:selected { background:#dce9fb; }
QMenu { background:#fff; border:1px solid #aeb8c5; }
QMenu::item { padding:5px 26px 5px 24px; }
QMenu::item:selected { background:#dce9fb; }
QLabel#brand { font-size:19px; font-weight:600; color:#164e91; }
QLabel#muted { color:#526477; }
QPushButton { background:#f5f6f8; border:1px solid #aeb7c2; padding:4px 9px; border-radius:2px; }
QPushButton:hover { background:#e4eefb; border-color:#6e9ed3; }
QPushButton:pressed { background:#d4e4f7; }
QPushButton:disabled { color:#9aa3ad; background:#eceef1; }
QPushButton#primary { background:#dceafa; border:1px solid #78a6d5; color:#123e70; font-weight:600; }
QLineEdit,QSpinBox,QComboBox { background:#fff; border:1px solid #aeb7c2; border-radius:1px; padding:4px 6px; selection-background-color:#2c70bb; }
QTreeWidget,QPlainTextEdit { background:#fff; border:1px solid #abb5c1; alternate-background-color:#f5f8fc; }
QTreeWidget::item { padding:3px 5px; }
QTreeWidget::item:selected { background:#cfe2f8; color:#10263d; }
QHeaderView::section { background:#eef1f5; color:#243447; border:0; border-right:1px solid #c8cdd4; border-bottom:1px solid #b7bec8; padding:5px 7px; }
QTabWidget::pane { border:1px solid #aab4c1; background:#fff; }
QTabBar::tab { background:#e5e9ef; border:1px solid #bdc6d1; padding:5px 12px; margin-right:2px; }
QTabBar::tab:selected { background:#fff; color:#164e91; border-bottom-color:#fff; }
QSplitter::handle { background:#b9c9df; }
QToolTip { color:#17202a; background:#fffde8; border:1px solid #aeb7c2; }
QToolBar { background:#edf0f4; border:0; border-bottom:1px solid #c2c9d2; spacing:3px; padding:3px 6px; }
QToolBar QToolButton { background:transparent; border:1px solid transparent; border-radius:2px; padding:4px 7px; }
QToolBar QToolButton:hover { background:#deebfa; border-color:#9bb9dc; }
QStatusBar { background:#e9edf3; border-top:1px solid #c3cad3; }
"""


def settings_store():
    """Use Qt's per-user settings backend (registry on Windows, native settings elsewhere)."""
    return QSettings("FluxFTP", "FluxFTP")


def human(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


class Window(DisplayOptions, ColorOptions, LayoutOptions, MouseActions, ConfirmOptions, FileMenu, Workspaces, TransferQueue, ConfigurationPersistence, ConfigurationBackupActions, WindowChrome, WindowWorkers, WindowConnections, QMainWindow, QueueManager):
    # Compatibility properties keep existing menus/storage on engine-owned state.
    @property
    def queue_jobs(self):
        return self.queue_engine.jobs

    @queue_jobs.setter
    def queue_jobs(self, value):
        self.queue_engine.jobs = value

    @property
    def pending(self):
        return self.queue_engine.pending

    @pending.setter
    def pending(self, value):
        self.queue_engine.pending = value

    @property
    def active(self):
        return self.queue_engine.active

    @active.setter
    def active(self, value):
        self.queue_engine.active = value

    @property
    def paused(self):
        return self.queue_engine.paused

    @paused.setter
    def paused(self, value):
        self.queue_engine.paused = value

    @property
    def queue_batch_failed(self):
        return self.queue_engine.batch_failed

    @queue_batch_failed.setter
    def queue_batch_failed(self, value):
        self.queue_engine.batch_failed = value

    ftp_message = Signal(str, int)
    def __init__(self, *, smoke=False):
        super().__init__()
        from .window_initializer import initialize_window
        initialize_window(self, smoke, STYLE, settings_store, QStandardPaths)

    def maintain_browse_sessions(self):
        for index in range(self.tabs.count()):
            split = self.tabs.widget(index)
            for side in range(split.count()):
                pane = split.widget(side)
                if pane.mode != 'Remote' or not pane.site:
                    continue
                settings = SiteSettings(self.preferences, pane.site)
                persistent = settings.value('Connection/persistent_browsing', True, type=bool)
                if not persistent or pane.site.single_connection or settings.value('Transfer/single_connection', False, type=bool):
                    if not pane.busy:
                        pane.browse_session.close()
                    continue
                if pane.busy or getattr(pane, '_keepalive_pending', False):
                    continue
                interval = max(5, min(3600, settings.value('Connection/ftp_keepalive_interval', 60, type=int)))
                if settings.value('Connection/ftp_keepalive', True, type=bool) and pane.browse_session.due(interval):
                    pane._keepalive_pending = True
                    worker = self.run(lambda worker, session=pane.browse_session, seconds=interval: session.keepalive(seconds),
                                      lambda _: None, failed=lambda _: None, raw_failure=True, autostart=False)
                    worker.finished.connect(lambda pane=pane: setattr(pane, '_keepalive_pending', False))
                    worker.start()

    def apply_ident_settings(self):
        try:
            self.ident.configure(
                enabled=self.preferences.value("Ident/enabled", False, type=bool),
                only_connecting=self.preferences.value("Ident/only_connecting", True, type=bool),
                name=self.preferences.value("Ident/name", "FluxFTP", type=str),
                system=self.preferences.value("Ident/system", "OTHER", type=str),
                port=self.preferences.value("Ident/port", 113, type=int),
            )
        except (OSError, ValueError) as error:
            for index in range(self.tabs.count()):
                for side in range(2):
                    self.tabs.widget(index).widget(side).browse_session.close()
            self.ident.close()
            self.error(str(error))

    def refresh_skip_colors(self):
        if not hasattr(self, "tabs"):
            return
        options = TransferOptions.from_settings(self.preferences)
        from .highlight_rules import highlight_rules, highlight_color
        try:
            rules = () if self.preferences.value('Display/highlights_disabled', False, type=bool) else highlight_rules(self.preferences.value('Display/highlight_rules', '[]', type=str))
        except ValueError:
            rules = ()
        for tab in range(self.tabs.count()):
            split = self.tabs.widget(tab)
            for index in range(split.count()):
                pane = split.widget(index)
                for row_index in range(pane.tree.topLevelItemCount()):
                    row = pane.tree.topLevelItem(row_index)
                    entry = row.data(0, Qt.ItemDataRole.UserRole)
                    reason = options.skip_reason(entry.name, entry.is_dir, entry.size)
                    color = highlight_color(entry.name, rules)
                    for column in range(row.columnCount()):
                        foreground = options.skip_color if reason else color
                        row.setForeground(column, QBrush(QColor(foreground)) if foreground else QBrush())
                    row.setToolTip(0, f"Skip List: {reason}" if reason else "")

    def new_connection(self, site, password, direction=None):
        return self.connection_factory.create(site, password, direction)

    def _configured_local_port_range(self):
        return self.connection_factory.configured_local_port_range()

    def proxy_bypassed(self, host):
        return self.connection_factory.proxy_bypassed(host)

    def ensure_proxy_credentials(self, parent, host="", site=None):
        if site is not None and site_options(site.site_options).get('Connection/proxy_profile'):
            from .site_connections import custom_proxy
            try:
                custom_proxy(self, site)
                return True
            except ValueError as error:
                self.error(str(error))
                return False
        if site is not None and site_options(site.site_options).get('Connection/proxy_enabled') is False:
            return True
        if host.startswith('cloud-') and len(host) == 38 and all(c in '0123456789abcdef' for c in host[6:]):
            return True
        if host and self.proxy_bypassed(host):
            return True
        if not (self.preferences.value("Connection/proxy_enabled", False, type=bool)
                and self.preferences.value("Connection/proxy_auth", False, type=bool)):
            return True
        if self._proxy_password_entered:
            return True
        password, accepted = QInputDialog.getText(
            parent, "Proxy authentication", "Proxy password (kept in memory until FluxFTP closes):",
            QLineEdit.EchoMode.Password)
        if not accepted:
            return False
        self.proxy_password = password
        self._proxy_password_entered = True
        try:
            self.credential_vault.set(getattr(self, "proxy_credential_id", "proxy"), password)
        except RuntimeError as error:
            QMessageBox.warning(parent, "Proxy password not saved", str(error))
        return True

    def set_activity_logging(self, enabled):
        self._activity_logging = bool(enabled)
        self.preferences.setValue("General/log_to_disk", self._activity_logging)
        if enabled:
            self.log.appendPlainText("Activity log to disk enabled")

    def _write_activity_log(self):
        current = self.log.toPlainText()
        previous = getattr(self, "_activity_log_snapshot", "")
        self._activity_log_snapshot = current
        if not getattr(self, "_activity_logging", False):
            return
        previous_lines = previous.splitlines()
        current_lines = current.splitlines()
        overlap = min(len(previous_lines), len(current_lines))
        while overlap and previous_lines[-overlap:] != current_lines[:overlap]:
            overlap -= 1
        added = current_lines[overlap:]
        if not added:
            return
        try:
            path = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)) / "logs" / "activity.log"
            path.parent.mkdir(parents=True, exist_ok=True)
            from .diagnostics import redact
            with path.open("a", encoding="utf-8") as stream:
                stream.write(redact("\n".join(added)) + "\n")
        except OSError:
            pass

    def apply_selection_color(self, color):
        from .themes import stylesheet, palette_for, apply_application
        apply_application(self.app_instance, self.preferences, self.STYLE)
        if palette_for(self.preferences):
            return
        base = stylesheet(self.preferences, self.STYLE)
        self.app_instance.setStyleSheet(base + f"\nQTreeWidget::item:selected {{ background:{color}; color:white; }}")

    def open_cloud_accounts(self, parent=None):
        from .cloud_accounts import CloudAccounts
        CloudAccounts(self, parent).exec()

    def open_key_manager(self, parent=None):
        from .key_manager import KeyManager
        KeyManager(self, parent).exec()

    def open_preferences(self):
        PreferencesDialog(self).exec()

    def add_workspace(self):
        if self.tabs.count() >= 100:
            self.statusBar().showMessage('Close a workspace before opening another (100-workspace limit).', 5000)
            return
        modes = [pane.mode for pane in self.current_panes()] if hasattr(self, 'tabs') else []
        if len(modes) != 2 or self.preferences.value('Misc/new_tab_remote', False, type=bool):
            modes = ['Remote', 'Remote']
        orientation = Qt.Orientation.Horizontal if self.preferences.value("Display/pane_layout", "Side by side", type=str) == "Side by side" else Qt.Orientation.Vertical
        splitter = QSplitter(orientation)
        splitter.addWidget(Pane(self, "1", modes[0]))
        splitter.addWidget(Pane(self, "2", modes[1]))
        if hasattr(self, 'mouse_filter'):
            for index in range(splitter.count()):
                self.install_pane_mouse(splitter.widget(index))
        self.tabs.setCurrentIndex(self.tabs.addTab(splitter, self.workspace_name()))
        if getattr(self, '_panel_positions_ready', False):
            self.initialize_pane_positions(splitter)
        self.apply_display_settings()
        if hasattr(self, "tray"): self.apply_fonts()
        if hasattr(self, 'layout_lock'): self.layout_lock.apply()
        self.save_workspaces()

    def close_tab(self, index):
        pane = self.tabs.widget(index)
        if pane is None or self.tabs.count() <= 1:
            return
        jobs = [job for job in self.queue_jobs if job['workspace'] is pane and job.get('state') in ('queued', 'active', 'failed')]
        if self.workspace_busy(pane):
            self.statusBar().showMessage('Wait for active work and close queue editors before closing this workspace.')
            return
        if jobs and not self.confirm_action('close_queue', 'Close workspace with queue files',
                f'Close this workspace and discard its {len(jobs)} unfinished queue item(s)? Transferred files will be kept.'):
            return
        if any(pane.widget(i).site or pane.widget(i).remote_site for i in range(2)) and not self.confirm_action(
                'close_connected', 'Close connected workspace', 'Close this workspace and disconnect its sites?'):
            return
        if any(pane.widget(i).busy for i in range(2)) or any(record['workspace'] is pane for record in self.active.values()):
            self.statusBar().showMessage('New work started; workspace close cancelled.')
            return
        self.discard_queue_jobs(jobs)
        for i in range(2):
            pane.widget(i).browse_session.close()
        self.tabs.removeTab(index)
        pane.deleteLater()
        self.save_queue_state()
        self.apply_layout_settings()
        self.refresh_workspace_names()
        self.save_panel_layout()
        self.save_workspaces()
        return True

    def manage_sites(self):
        dialog = SitesDialog(self)
        if dialog.exec() and dialog.connection:
            index, name = dialog.connection
            self.tabs.currentWidget().widget(index).connect_saved_site(name)

    def closeEvent(self, event):
        if getattr(self, '_queue_operation_depth', 0) or getattr(self, '_queue_commit', False):
            event.ignore()
            return
        saved = self.save_queue_state()
        if self.workers or (self.pending and not ((self.paused or all(j.get('held') for j in self.pending)) and saved)):
            QMessageBox.information(self, "Work in progress", "Cancel queued transfers and wait for active operations to finish before closing.")
            event.ignore()
        else:
            if not (getattr(self, '_smoke_test', False) or getattr(self, '_configuration_restored', False)) and not self.confirm_action('exit', 'Exit FluxFTP', 'Exit FluxFTP?'):
                event.ignore()
                return
            if self.workers or self.active:
                event.ignore()
                return
            for dialog in list(getattr(self, 'mouse_windows', [])):
                if not dialog.close():
                    event.ignore()
                    return
            self.save_panel_layout()
            self.save_workspaces()
            self.docking.shutdown()
            for index in range(self.tabs.count()):
                for side in range(2):
                    self.tabs.widget(index).widget(side).browse_session.close()
            self.ident.close()
            self.cloud_service.close()
            self.automation_runtime.shutdown()
            for mount in list(getattr(getattr(self, 'sshfs_mounts', None), 'mounts', [])):
                self.sshfs_mounts.stop(mount)
            if getattr(self, 'control_api', None):
                self.control_api.close()
            self.bandwidth_control.timer.stop()
            self.browse_timer.stop()
            self.system_appearance.close()
            self.tray_timer.stop()
            self.download_display_timer.stop()
            self.tray.hide()
            self.window_positions.close()
            event.accept()


def main():
    parser = argparse.ArgumentParser(description="FluxFTP desktop file transfer client")
    parser.add_argument("--debug", action="store_true", help="Write connection diagnostics to a text log")
    parser.add_argument("--log-file", type=Path, help="Debug log destination (requires --debug)")
    parser.add_argument("--smoke-test", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--runtime-smoke-test", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--screenshot", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument('--automation-once', action='store_true', help='Run due saved automation without opening the desktop')
    args, qt_args = parser.parse_known_args()
    args.smoke_test = args.smoke_test or args.runtime_smoke_test
    if args.log_file and not args.debug:
        parser.error("--log-file requires --debug")
    # Preserve fractional Windows display scaling (for example 125% and 150%).
    # Qt requires the policy to be set before constructing the application.
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication([sys.argv[0], *qt_args])
    app.setApplicationName("FluxFTP")
    app.setOrganizationName("FluxFTP")
    if args.automation_once:
        from .background_scheduler import run_once
        from .openssl_runtime import ready
        ready()
        try:
            result = run_once(app, smoke=args.smoke_test)
        except Exception as error:
            diagnostics.worker_failure(error)
            result = 1
        sys.exit(result)
    debug_path = None
    if args.debug:
        from datetime import datetime
        default_dir = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)) / "logs"
        path = args.log_file or default_dir / f"fluxftp-debug-{datetime.now():%Y%m%d-%H%M%S}-{os.getpid()}.log"
        try:
            debug_path = diagnostics.configure_debug(path)
        except OSError as error:
            QMessageBox.critical(None, "Cannot create debug log", str(error))
            sys.exit(1)
        previous_hook = sys.excepthook
        def report_unhandled(exc_type, value, tb):
            diagnostics.worker_failure(value)
            previous_hook(exc_type, value, tb)
        sys.excepthook = report_unhandled
    from .themes import apply_application
    if not args.smoke_test:
        apply_application(app, settings_store(), STYLE)
    window = Window(smoke=args.smoke_test)
    window.ftp_log.appendPlainText(f"FTP/FTPS SSL library: {ssl.OPENSSL_VERSION}")
    window._smoke_test = args.smoke_test
    if debug_path:
        window.log.appendPlainText(f"Debug logging enabled: {debug_path}")
        copy_log = QPushButton("Copy debug log path")
        copy_log.setToolTip(str(debug_path))
        copy_log.clicked.connect(lambda: app.clipboard().setText(str(debug_path)))
        window.statusBar().addPermanentWidget(copy_log)
        diagnostics.logger.info("Desktop ready; reproduce the connection problem to capture diagnostics.")
    if window.preferences.value('Layout/windows/main', '', type=str):
        window.show()
    else:
        window.showMaximized()
    from .openssl_runtime import ready
    ready()
    if not args.smoke_test and sys.platform == 'win32' and getattr(sys, 'frozen', False):
        from .openssl_update_ui import RuntimeUpdater
        window.runtime_updater = RuntimeUpdater(window)
    if not args.smoke_test:
        from .app_updates_ui import ApplicationUpdates
        window.application_updates = ApplicationUpdates(window)
    if not args.smoke_test:
        def recover_queue():
            window.initialize_queue_storage(defer=True)
            window.show_saved_queue()
        QTimer.singleShot(0, recover_queue)
    if args.smoke_test:
        def finish():
            if args.screenshot:
                window.grab().save("preview.png")
            window.close()
        QTimer.singleShot(500, finish)
    exit_code = app.exec()
    if debug_path:
        diagnostics.logger.info("Desktop exited: code=%s", exit_code)
        sys.excepthook = previous_hook
        diagnostics.close_debug()
    sys.exit(exit_code)


