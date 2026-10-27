"""Remember desktop and dialog geometry without recording transient popups."""
from PySide6.QtCore import QObject, QEvent, QByteArray, QTimer
from PySide6.QtWidgets import QApplication, QDialog


class WindowPositions(QObject):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.active = True
        QApplication.instance().installEventFilter(self)

    def key(self, widget):
        if widget is self.owner:
            return 'Layout/windows/main'
        if not isinstance(widget, QDialog) or not widget.isWindow():
            return None
        parent = widget.parent()
        while parent is not None and parent is not self.owner:
            parent = parent.parent()
        if parent is None:
            return None
        name = type(widget).__name__
        if name == 'QDialog':
            # Generic editors have stable titles; native prompts are excluded.
            name += '/' + widget.windowTitle()
        elif not type(widget).__module__.startswith('fluxftp.'):
            return None
        return 'Layout/windows/' + name

    def enabled(self):
        return self.active and not (getattr(self.owner, '_smoke_test', False) or
                    getattr(self.owner, '_configuration_restored', False))

    def close(self):
        self.save(self.owner)
        self.active = False
        QApplication.instance().removeEventFilter(self)

    def save(self, widget):
        if not self.enabled() or widget.isMinimized():
            return
        key = self.key(widget)
        if key and getattr(widget, '_geometry_loaded', False):
            self.owner.preferences.setValue(key, bytes(widget.saveGeometry()).hex())
            self.owner.preferences.sync()

    def eventFilter(self, widget, event):
        kind = event.type()
        if kind not in (QEvent.Type.Show, QEvent.Type.Move, QEvent.Type.Resize,
                        QEvent.Type.Hide, QEvent.Type.Close):
            return False
        if not self.enabled() or not self.key(widget):
            return False
        if kind == QEvent.Type.Show and not getattr(widget, '_geometry_loaded', False):
            widget._geometry_loaded = True
            widget._geometry_restoring = True
            value = self.owner.preferences.value(self.key(widget), '', type=str)
            try:
                if value and len(value) <= 16384:
                    # Qt also adjusts geometry when a monitor is no longer present.
                    widget.restoreGeometry(QByteArray(bytes.fromhex(value)))
            except (TypeError, ValueError):
                pass
            finally:
                widget._geometry_restoring = False
            timer = QTimer(widget)
            timer.setSingleShot(True)
            timer.setInterval(250)
            timer.timeout.connect(lambda: self.save(widget))
            widget._geometry_timer = timer
        elif getattr(widget, '_geometry_loaded', False) and not getattr(widget, '_geometry_restoring', False):
            if kind in (QEvent.Type.Hide, QEvent.Type.Close):
                widget._geometry_timer.stop()
                self.save(widget)
            elif widget.isVisible():
                widget._geometry_timer.start()
        return False
