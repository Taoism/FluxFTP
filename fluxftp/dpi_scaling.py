"""Refresh app-owned font and icon sizing when the window's DPI changes."""
from PySide6.QtCore import QObject, QEvent, QTimer


class DpiScaling(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.handle = None
        self.screen = None
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setSingleShot(True)
        self.refresh_timer.setInterval(0)
        self.refresh_timer.timeout.connect(self.refresh)
        window.installEventFilter(self)

    def eventFilter(self, watched, event):
        if watched is self.window and event.type() in (QEvent.Type.Show, QEvent.Type.WinIdChange):
            QTimer.singleShot(0, self.attach)
        return False

    def attach(self):
        handle = self.window.windowHandle()
        if handle is None:
            return
        if handle is not self.handle:
            if self.handle is not None:
                self.handle.screenChanged.disconnect(self.screen_changed)
            self.handle = handle
            handle.screenChanged.connect(self.screen_changed)
        self.screen_changed(handle.screen())

    def screen_changed(self, screen):
        if screen is self.screen:
            self.refresh_timer.start()
            return
        if self.screen is not None:
            try:
                self.screen.logicalDotsPerInchChanged.disconnect(self.on_dpi_changed)
            except (RuntimeError, TypeError):
                pass
        self.screen = screen
        if screen is not None:
            screen.logicalDotsPerInchChanged.connect(self.on_dpi_changed)
        self.refresh_timer.start()

    def on_dpi_changed(self, *_):
        self.refresh_timer.start()

    def refresh(self):
        if self.window.isVisible():
            self.window.apply_fonts()
            self.window.apply_layout_settings()
