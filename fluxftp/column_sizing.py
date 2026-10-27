"""Resizable headers with optional fitting to the available viewport."""
from PySide6.QtCore import QObject, QEvent, Qt, QTimer
from PySide6.QtWidgets import QMenu
from shiboken6 import isValid


class ColumnSizing(QObject):
    def __init__(self, tree, options):
        super().__init__(tree)
        self.tree, self.options = tree, options
        self.pending = False
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.automatic_fit)
        tree.viewport().installEventFilter(self)
        header = tree.header()
        header.setMinimumSectionSize(32)
        header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        header.customContextMenuRequested.connect(self.menu)
        header.setToolTip('Drag any column divider to resize. Double-click to fit its contents. Right-click to fit all columns to the window.')

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            self.schedule()
        return False

    def schedule(self):
        if not self.pending:
            self.pending = True
            self.timer.start(0)

    def automatic_fit(self):
        self.pending = False
        if not isValid(self.tree): return
        if self.options.value('auto_size/' + self.tree._view_group):
            self.fit()

    def fit(self):
        tree, header = self.tree, self.tree.header()
        visible = [i for i in range(tree.columnCount()) if not tree.isColumnHidden(i)]
        if not visible or tree.viewport().width() <= 0:
            return
        available = max(tree.viewport().width(), len(visible) * header.minimumSectionSize())
        minimum = header.minimumSectionSize()
        weights = [max(1, header.sectionSize(i) - minimum) for i in visible]
        remaining = available - len(visible) * minimum
        total = sum(weights)
        widths = [minimum + int(remaining * weight / total) for weight in weights]
        widths[-1] += available - sum(widths)
        tree._fitting_columns = True
        try:
            for column, width in zip(visible, widths):
                header.resizeSection(column, width)
        finally:
            tree._fitting_columns = False
        if not self.options.value('auto_size/' + tree._view_group):
            for column in visible:
                self.options.save_width(tree, column, header.sectionSize(column))

    def menu(self, position):
        header = self.tree.header()
        menu = QMenu(self.tree)
        menu.addAction('Fit columns to window', self.fit)
        column = header.logicalIndexAt(position)
        if column >= 0:
            menu.addAction('Size this column to contents', lambda: self.tree.resizeColumnToContents(column))
        menu.exec(header.mapToGlobal(position))
        menu.deleteLater()
