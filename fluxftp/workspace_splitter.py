"""Two-pane splitter with a double-click reset to equal pane sizes."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QSplitter, QSplitterHandle


class WorkspaceSplitterHandle(QSplitterHandle):
    def mouseDoubleClickEvent(self, event):
        splitter = self.splitter()
        if event.button() == Qt.MouseButton.LeftButton and splitter.count() == 2:
            splitter.center_panes()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class WorkspaceSplitter(QSplitter):
    centered = Signal()

    def createHandle(self):
        return WorkspaceSplitterHandle(self.orientation(), self)

    def center_panes(self):
        if self.count() != 2:
            return
        bounds = self.contentsRect()
        extent = bounds.width() if self.orientation() == Qt.Orientation.Horizontal else bounds.height()
        usable = max(2, extent - self.handleWidth())
        first = usable // 2
        self.setSizes([first, usable - first])
        self.centered.emit()
