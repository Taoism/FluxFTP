"""Optional native dock layout; existing widgets and transfer workers stay alive."""
from PySide6.QtCore import QObject, Qt, QTimer, QEvent, QByteArray
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMainWindow, QDockWidget


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
        window.view_commands.menu.addAction(self.action)
        self.menu = window.view_commands.menu.addMenu('Dock panels')
        self.menu.setEnabled(False)
        self.action.setChecked(window.preferences.value('Layout/docking_enabled', False, type=bool))

    def panels(self):
        return dict(workspace=self.window.workspace_panel, queue=self.window.queue_panel, **self.window.log_panels)

    def enable(self, enabled):
        if enabled == self.active: return
        self.changing = True
        window = self.window
        try:
            if enabled:
                window.save_panel_layout()
                self.active = True
                titles = dict(workspace='Workspaces', queue='Transfer Queue', ftp='FTP Log', transfer='Transfer Log', system='System Log', tasks='Tasks')
                for key, panel in self.panels().items():
                    dock = self.docks.get(key)
                    if dock is None:
                        dock = QDockWidget(titles[key], self.host)
                        dock.setObjectName('fluxftp-dock-' + key)
                        dock.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
                        dock.installEventFilter(self)
                        dock.dockLocationChanged.connect(self.schedule)
                        dock.topLevelChanged.connect(self.schedule)
                        dock.visibilityChanged.connect(self.schedule)
                        self.docks[key] = dock
                        self.menu.addAction(dock.toggleViewAction())
                    dock.setWidget(panel)
                    self.host.addDockWidget(Qt.DockWidgetArea.TopDockWidgetArea if key == 'workspace' else Qt.DockWidgetArea.BottomDockWidgetArea, dock)
                    dock.show()
                    panel.show()
                logs = [self.docks[key] for key in window.log_panels]
                for dock in logs[1:]: self.host.tabifyDockWidget(logs[0], dock)
                self.host.splitDockWidget(self.docks['workspace'], self.docks['queue'], Qt.Orientation.Vertical)
                logs[0].raise_()
                state = window.preferences.value('Layout/dock_state', '', type=str)
                if state:
                    try: self.host.restoreState(QByteArray.fromHex(state.encode('ascii')), 1)
                    except (ValueError, UnicodeError): pass
                window.work_area.hide()
                self.host.show()
                self.lock(window.preferences.value('Layout/locked', False, type=bool))
            else:
                self.save()
                self.active = False
                # Reparent before hiding docks: closing a panel never destroys it.
                for panel in self.panels().values(): panel.setParent(window.work_area)
                for index, panel in enumerate((window.workspace_panel, window.log_tabs, window.queue_panel)):
                    window.work_area.insertWidget(index, panel)
                    panel.show()
                for key, panel in window.log_panels.items():
                    window.log_tabs.addTab(panel, dict(ftp='FTP Log', transfer='Transfer Log', system='System Log', tasks='Tasks')[key])
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
            self.window.preferences.sync()

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
            if obj is self.host and self.window.preferences.value('Layout/locked', False, type=bool) and event.type() in (
                    QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick, QEvent.Type.MouseMove):
                return True  # The host handles separators; child controls remain usable.
            if event.type() in (QEvent.Type.Resize, QEvent.Type.Move): self.schedule()
        return False
