"""Persistent Ctrl+wheel sizing for text, toolbar icons and panel heights."""
import math
from PySide6.QtCore import QObject, QEvent, Qt
from PySide6.QtGui import QFont


def wheel_steps(owner, event):
    delta = event.angleDelta().y() or event.pixelDelta().y() * 3
    if owner.remainder * delta < 0:
        owner.remainder = 0
    owner.remainder += delta
    steps = math.trunc(owner.remainder / 120)
    owner.remainder -= steps * 120
    return steps


class ResizeWheel(QObject):
    def __init__(self, window, widget, option, resize):
        super().__init__(widget)
        self.window, self.option, self.resize = window, option, resize
        self.remainder = 0
        widget.installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() != QEvent.Type.Wheel:
            return False
        if event.modifiers() != Qt.KeyboardModifier.ControlModifier:
            self.remainder = 0
            return False
        if self.window.preferences.value('Mouse/' + self.option, True, type=bool) and watched.isEnabled():
            steps = wheel_steps(self, event)
            if steps:
                self.resize(steps)
        else:
            self.remainder = 0
        event.accept()
        return True


def install_icon_wheel(window, toolbar):
    from PySide6.QtWidgets import QToolButton
    for widget in [toolbar, *toolbar.findChildren(QToolButton)]:
        if not hasattr(widget, '_resize_wheel'):
            widget._resize_wheel = ResizeWheel(window, widget, 'wheel_icons',
                lambda steps: window.set_toolbar_icon_size(window.toolbar_icon_size() + 4 * steps))
    toolbar.setToolTip('Ctrl + mouse wheel over toolbar buttons: resize toolbar icons (16–48 px).')


class WheelZoom(QObject):
    def __init__(self, window, view, key):
        super().__init__(view)
        self.window, self.view, self.key = window, view, key
        self.remainder = 0
        view.installEventFilter(self)
        view.viewport().installEventFilter(self)
        if hasattr(view, 'header'): view.header().viewport().installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() != QEvent.Type.Wheel:
            return False
        if event.modifiers() != Qt.KeyboardModifier.ControlModifier:
            self.remainder = 0
            return False
        if not self.window.preferences.value('Mouse/wheel_text', True, type=bool):
            self.remainder = 0
            event.accept()
            return True
        steps = wheel_steps(self, event)
        if steps:
            font = self.view.font()
            size = font.pointSizeF()
            if size <= 0:
                size = max(1, font.pixelSize() * 72 / self.view.logicalDpiY())
            size = max(1, min(512, size + steps))
            self.window.preferences.setValue('Display/zoom_' + self.key, size)
            self.window.apply_fonts()
        event.accept()
        return True


def zoomed_font(window, view, key, base):
    if not hasattr(view, '_wheel_zoom'):
        view._wheel_zoom = WheelZoom(window, view, key)
    font = QFont(base)
    size = window.preferences.value('Display/zoom_' + key, 0.0, type=float)
    if math.isfinite(size) and 1 <= size <= 512:
        font.setPointSizeF(size)
    return font


def reset_zoom(settings, scope=None):
    for key in settings.allKeys():
        if not key.startswith('Display/zoom_'): continue
        files = key in ('Display/zoom_left', 'Display/zoom_right', 'Display/zoom_queue')
        logs = key in ('Display/zoom_log', 'Display/zoom_ftp_log', 'Display/zoom_transfer_log', 'Display/zoom_tasks_log')
        if scope is None or (scope == 'files' and files) or (scope == 'logs' and logs) or (scope == 'application' and key == 'Display/zoom_workspace'):
            settings.remove(key)
