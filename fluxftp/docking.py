"""Optional native dock layout; existing widgets and transfer workers stay alive."""
from PySide6.QtCore import QObject, Qt, QTimer, QEvent, QByteArray
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (QMainWindow, QDockWidget, QWidget, QHBoxLayout, QLabel,
    QToolButton, QMenu, QStyle, QTabBar)


class DockTitleBar(QWidget):
    def __init__(self, docking, key, dock):
        super().__init__(dock)
        self.docking, self.key = docking, key
        row = QHBoxLayout(self)
        row.setContentsMargins(6, 2, 4, 2)
        row.setSpacing(4)
        title = QLabel(docking.panel_titles[key], self)
        title.setStyleSheet('font-weight:600;')
        row.addWidget(title)
        if key == 'workspace':
            window = docking.window
            add = window.workspace_panel.add_button
            add.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            add.customContextMenuRequested.connect(
                lambda point: docking.show_panel_context_menu(key, add.mapToGlobal(point)))
            row.addWidget(add)
            row.addWidget(window.workspace_navigation)
        row.addStretch(1)
        close = QToolButton(self)
        close.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_TitleBarCloseButton))
        close.setToolTip('Hide panel')
        close.setAccessibleName(f'Hide {docking.panel_titles[key]}')
        close.clicked.connect(dock.hide)
        row.addWidget(close)
        close.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        close.customContextMenuRequested.connect(
            lambda point: docking.show_panel_context_menu(key, close.mapToGlobal(point)))
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(
            lambda point: docking.show_panel_context_menu(key, self.mapToGlobal(point)))
        title.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        title.customContextMenuRequested.connect(
            lambda point: docking.show_panel_context_menu(key, title.mapToGlobal(point)))


class DockLayout(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        window.docking = self
        self.active = False
        self.changing = False
        self.closing = False
        self.host = QMainWindow(window.centralWidget())
        self.host.setWindowFlags(Qt.WindowType.Widget)
        self.host.setObjectName('dockLayoutHost')
        self.host.setDockOptions(QMainWindow.DockOption.AllowNestedDocks |
                                 QMainWindow.DockOption.AllowTabbedDocks |
                                 QMainWindow.DockOption.AnimatedDocks)
        self.host.installEventFilter(self)
        window.centralWidget().layout().addWidget(self.host, 1)
        self.host.hide()
        self.docks = {}
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(300)
        self.timer.timeout.connect(self.save)
        self.action = QAction('Customizable docking layout', self)
        self.action.setCheckable(True)
        self.action.toggled.connect(self.enable)
        self.rush_layout_action = QAction('FTP Rush layout', self)
        self.rush_layout_action.setToolTip(
            'Put the two-pane workspace above the transfer queue. Drag panels to customize; FluxFTP remembers their positions.')
        self.rush_layout_action.triggered.connect(self.apply_ftp_rush_layout)
        window.view_commands.menu.addAction(self.rush_layout_action)
        self.menu = window.view_commands.menu.addMenu('Dock panels')
        self.move_bottom_menu = self.menu.addMenu('Move to Bottom')
        self.panel_titles = dict(workspace='Workspaces', tools='Tools', queue='Transfer Queue', ftp='FTP Log',
            transfer='Transfer Log', system='System Log', tasks='Tasks')
        for key, title in self.panel_titles.items():
            action = self.move_bottom_menu.addAction(title)
            action.triggered.connect(lambda checked=False, key=key: self.move_to_bottom(key))
        self.menu.setEnabled(False)
        # Docking is the normal layout; the user can drag and nest panels directly.
        # Keep the action internal for reset/lock integration, without a mode toggle.
        window.preferences.setValue('Layout/docking_enabled', True)
        self.action.setChecked(True)

    def panels(self):
        return dict(workspace=self.window.workspace_panel, tools=self.window.main_toolbar,
            queue=self.window.queue_panel, **self.window.log_panels)

    def enable(self, enabled):
        if enabled == self.active: return
        self.changing = True
        window = self.window
        try:
            if enabled:
                window.save_panel_layout()
                self.active = True
                for key, panel in self.panels().items():
                    dock = self.docks.get(key)
                    if dock is None:
                        dock = QDockWidget(self.panel_titles[key], self.host)
                        dock.setObjectName('fluxftp-dock-' + key)
                        dock.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
                        titlebar = DockTitleBar(self, key, dock)
                        dock.setTitleBarWidget(titlebar)
                        dock.installEventFilter(self)
                        dock.dockLocationChanged.connect(self.schedule)
                        dock.topLevelChanged.connect(self.schedule)
                        dock.visibilityChanged.connect(self.schedule)
                        self.docks[key] = dock
                        self.menu.addAction(dock.toggleViewAction())
                    dock.setWidget(panel)
                    if not getattr(panel, '_dock_context_connected', False):
                        panel.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
                        panel.customContextMenuRequested.connect(
                            lambda point, key=key, panel=panel:
                                self.show_panel_context_menu(key, panel.mapToGlobal(point)))
                        panel._dock_context_connected = True
                    if key == 'workspace':
                        panel.show_caption(False, False)
                    area = (Qt.DockWidgetArea.TopDockWidgetArea if key in ('workspace', 'tools')
                            else Qt.DockWidgetArea.BottomDockWidgetArea)
                    self.host.addDockWidget(area, dock)
                    dock.show()
                    panel.show()
                self.window.header_rows.rows['tools'].hide()
                logs = [self.docks[key] for key in window.log_panels]
                for dock in logs:
                    self.host.tabifyDockWidget(self.docks['queue'], dock)
                for tab_bar in self.host.findChildren(QTabBar):
                    tab_bar.installEventFilter(self)
                self.docks['queue'].raise_()
                self.host.splitDockWidget(self.docks['tools'], self.docks['workspace'], Qt.Orientation.Vertical)
                self.host.splitDockWidget(self.docks['workspace'], self.docks['queue'], Qt.Orientation.Vertical)
                state = window.preferences.value('Layout/dock_state', '', type=str)
                if state:
                    try: self.host.restoreState(QByteArray.fromHex(state.encode('ascii')), 1)
                    except (ValueError, UnicodeError): pass
                window.work_area.hide()
                self.host.show()
                self.lock(window.preferences.value('Layout/locked', False, type=bool))
                QTimer.singleShot(0, self.restore_floating_geometries)
            else:
                self.save()
                self.active = False
                # Reparent before hiding docks: closing a panel never destroys it.
                workspace_dock = self.docks.get('workspace')
                if workspace_dock:
                    titlebar = workspace_dock.titleBarWidget()
                    if titlebar and titlebar.layout():
                        titlebar.layout().removeWidget(window.workspace_navigation)
                        titlebar.layout().removeWidget(window.workspace_panel.add_button)
                        window.workspace_panel.caption_row.layout().insertWidget(1, window.workspace_panel.add_button)
                        window.workspace_panel.caption_row.layout().insertWidget(2, window.workspace_navigation)
                    workspace_dock.setTitleBarWidget(None)
                for panel in self.panels().values(): panel.setParent(window.work_area)
                for index, panel in enumerate((window.workspace_panel, window.log_tabs, window.queue_panel)):
                    window.work_area.insertWidget(index, panel)
                    panel.show()
                window.workspace_panel.show_caption(True, False)
                for key, panel in window.log_panels.items():
                    window.log_tabs.addTab(panel, dict(ftp='FTP Log', transfer='Transfer Log', system='System Log', tasks='Tasks')[key])
                tools_row = window.header_rows.rows['tools']
                tools_row.layout().itemAt(0).layout().addWidget(window.main_toolbar, 1)
                tools_row.show()
                for dock in self.docks.values(): dock.hide()
                self.host.hide()
                window.work_area.show()
                window.work_area.setSizes([350, 320, 300])
            self.menu.setEnabled(enabled)
            window.preferences.setValue('Layout/docking_enabled', enabled)
        finally:
            self.changing = False

    def schedule(self, *_):
        if self.active and not self.changing and not self.closing: self.timer.start()

    def save(self):
        if self.active and not self.closing and not getattr(self.window, '_configuration_restored', False):
            self.window.preferences.setValue('Layout/dock_state', bytes(self.host.saveState(1)).hex())
            for key, dock in self.docks.items():
                if dock.isFloating():
                    self.window.preferences.setValue(f'Layout/dock_geometry/{key}',
                        bytes(dock.saveGeometry()).hex())
            self.window.preferences.sync()

    def restore_floating_geometries(self):
        if not self.active:
            return
        for key, dock in self.docks.items():
            if not dock.isFloating():
                continue
            value = self.window.preferences.value(f'Layout/dock_geometry/{key}', '', type=str)
            if not value or len(value) > 16384:
                continue
            try:
                dock.restoreGeometry(QByteArray.fromHex(value.encode('ascii')))
            except (ValueError, UnicodeError):
                continue

    def shutdown(self):
        self.closing = True
        self.timer.stop()
        for dock in self.docks.values(): dock.hide()

    def show_panel(self, key):
        dock = self.docks[key]
        dock.show()
        dock.raise_()
        focus = {'queue': self.window.queue, 'ftp': self.window.ftp_log, 'transfer': self.window.transfer_log,
                 'system': self.window.log, 'tasks': self.window.tasks_log}
        if key in focus: focus[key].setFocus()

    def show_panel_context_menu(self, key, position):
        dock = self.docks.get(key)
        if dock is None:
            return
        menu = QMenu(self.window)
        menu.addAction('Hide panel', dock.hide)
        menu.exec(position)
        menu.deleteLater()

    def move_to_bottom(self, key):
        """Move and show one panel in the bottom dock area."""
        if not self.active or key not in self.docks:
            return
        dock = self.docks[key]
        self.host.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, dock)
        dock.show()
        dock.raise_()
        self.save()

    def apply_ftp_rush_layout(self):
        """Place the tools above the two-pane workspace and logs with the queue below."""
        if not self.active:
            self.action.setChecked(True)
        if not self.active:
            return
        if getattr(self.window, 'layout_lock', None):
            self.window.layout_lock.action.setChecked(False)

        self.changing = True
        self.host.setUpdatesEnabled(False)
        try:
            workspace = self.docks['workspace']
            tools = self.docks['tools']
            queue = self.docks['queue']
            self.host.addDockWidget(Qt.DockWidgetArea.TopDockWidgetArea, tools)
            self.host.addDockWidget(Qt.DockWidgetArea.TopDockWidgetArea, workspace)
            self.host.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, queue)
            tools.show()
            workspace.show()
            queue.show()
            for key in self.window.log_panels:
                dock = self.docks[key]
                self.host.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, dock)
                self.host.tabifyDockWidget(queue, dock)
                dock.show()
            queue.raise_()
            self.host.splitDockWidget(tools, workspace, Qt.Orientation.Vertical)
            self.host.splitDockWidget(workspace, queue, Qt.Orientation.Vertical)

            # FTP Rush's main work area is two panes side by side.
            self.window.tools_commands.layout('Side by side')
            self.host.setUpdatesEnabled(True)
            self.host.resizeDocks([tools, workspace, queue],
                [48, max(200, int(self.host.height() * 0.55)), max(160, int(self.host.height() * 0.40))],
                Qt.Orientation.Vertical)
        finally:
            self.host.setUpdatesEnabled(True)
            self.changing = False
        self.save()

    def lock(self, locked):
        self.action.setEnabled(not locked)
        for dock in self.docks.values():
            dock.setFeatures(QDockWidget.DockWidgetFeature.NoDockWidgetFeatures if locked else
                QDockWidget.DockWidgetFeature.DockWidgetClosable | QDockWidget.DockWidgetFeature.DockWidgetMovable | QDockWidget.DockWidgetFeature.DockWidgetFloatable)
            if dock.isFloating():
                if locked: dock.setFixedSize(dock.size())
                else:
                    dock.setMinimumSize(0, 0)
                    dock.setMaximumSize(16777215, 16777215)

    def eventFilter(self, obj, event):
        if self.active:
            if isinstance(obj, QTabBar) and event.type() == QEvent.Type.ContextMenu:
                index = obj.tabAt(event.pos())
                if index >= 0:
                    title = obj.tabText(index)
                    key = next((name for name, value in self.panel_titles.items() if value == title), None)
                    if key:
                        self.show_panel_context_menu(key, event.globalPos())
                        event.accept()
                        return True
            if obj is self.host and self.window.preferences.value('Layout/locked', False, type=bool) and event.type() in (
                    QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick, QEvent.Type.MouseMove):
                return True  # The host handles separators; child controls remain usable.
            if event.type() in (QEvent.Type.Resize, QEvent.Type.Move): self.schedule()
        return False
