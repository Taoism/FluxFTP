"""Selectable, reorderable whole rows above the file panes."""
import json

from PySide6.QtCore import Qt, QMimeData, QSize
from PySide6.QtGui import QDrag, QPainter, QPalette
from PySide6.QtWidgets import (QWidget, QFrame, QVBoxLayout, QHBoxLayout, QToolButton,
    QApplication, QMenu, QSplitter, QSizePolicy, QScrollArea)

MIME = 'application/x-fluxftp-header-row'
DEFAULT_ORDER = ('tools', 'connection', 'tabs', 'overview')


class ConnectionStrip(QScrollArea):
    """Keep every connection control reachable even in a narrow shared row."""
    def __init__(self, content):
        super().__init__()
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setWidgetResizable(True)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setWidget(content)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        height = self.widget().sizeHint().height()
        if self.width() < self.widget().minimumSizeHint().width():
            height += self.horizontalScrollBar().sizeHint().height()
        if self.height() != height:
            self.setFixedHeight(height)


class HeightGrip(QFrame):
    def __init__(self, row):
        super().__init__(row)
        self.row = row
        self.origin = None
        self.setFixedHeight(7)
        self.setCursor(Qt.CursorShape.SizeVerCursor)
        self.setToolTip('Drag or Ctrl + mouse wheel to resize the Workspace list; double-click for one workspace row')
        from .font_zoom import ResizeWheel
        self._resize_wheel = ResizeWheel(row.host.window, self, 'wheel_panels',
            lambda steps: row.set_height(row.height() + steps * 16))
        self.setAccessibleName('Resize Workspace list vertically')
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleDescription('Up and Down resize by 16 pixels. Home fits one workspace row. Unavailable when layout is locked.')
        self.setStyleSheet('QFrame { border-top:1px solid palette(mid); } QFrame:focus { border:2px solid palette(highlight); }')

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.origin = (event.globalPosition().y(), self.row.height())
            event.accept()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            self.row.set_height(self.row.height() + (-16 if event.key() == Qt.Key.Key_Up else 16))
            event.accept()
        elif event.key() == Qt.Key.Key_Home:
            self.row.compact()
            event.accept()
        else:
            super().keyPressEvent(event)

    def mouseMoveEvent(self, event):
        if self.origin is not None:
            self.row.set_height(self.origin[1] + event.globalPosition().y() - self.origin[0])
            event.accept()

    def mouseReleaseEvent(self, event):
        self.origin = None
        event.accept()

    def mouseDoubleClickEvent(self, event):
        self.row.compact()
        event.accept()


class Grip(QToolButton):
    def __init__(self, row):
        super().__init__(row)
        self.row = row
        self.setFixedSize(18, 28)
        self.setStyleSheet('QToolButton { padding:0; }')
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip('Drag to reorder or share a row: ' + row.title + '; right-click for move options')
        self.setAccessibleName('Move ' + row.title)
        self.origin = None

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.palette().color(QPalette.ColorRole.ButtonText))
        for x in (6, 10):
            for y in (9, 13, 17):
                painter.drawEllipse(x, y, 2, 2)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.origin = event.position().toPoint()
            self.row.host.select(self.row.key)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (self.origin is not None and event.buttons() & Qt.MouseButton.LeftButton
                and (event.position().toPoint() - self.origin).manhattanLength() >= QApplication.startDragDistance()):
            self.origin = None
            self.setDown(False)
            drag = QDrag(self)
            data = QMimeData()
            data.setData(MIME, self.row.key.encode('ascii'))
            drag.setMimeData(data)
            drag.exec(Qt.DropAction.MoveAction)
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self.origin = None
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.AltModifier and event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            self.row.host.move(self.row.key, -1 if event.key() == Qt.Key.Key_Up else 1)
            event.accept()
            return
        super().keyPressEvent(event)

    def contextMenuEvent(self, event):
        host = self.row.host
        host.select(self.row.key)
        menu = QMenu(self)
        if hasattr(host.window, 'toolbar_visibility'):
            visibility = host.window.toolbar_visibility
            menu.addAction('Hide ' + self.row.title, lambda: visibility.set_visible(self.row.key, False))
            edit = menu.addMenu('Edit Toolbars')
            visibility.apply()
            for action in visibility.actions.values(): edit.addAction(action)
            menu.addSeparator()
        for label, delta in (('Move up', -1), ('Move down', 1)):
            action = menu.addAction(label)
            index = host.order().index(self.row.key)
            action.setEnabled(0 <= index + delta < len(host.rows))
            action.triggered.connect(lambda checked=False, delta=delta: host.move(self.row.key, delta))
        menu.addSeparator()
        join = menu.addMenu('Share a row with')
        for key, row in host.rows.items():
            if key != self.row.key:
                join.addAction(row.title, lambda _=False, key=key: host.join(self.row.key, key))
        menu.addAction('Move to its own row', lambda: host.detach(self.row.key))
        if self.row.key == 'overview':
            menu.addAction('Fit one workspace row', self.row.compact)
        if self.row.key == 'tabs':
            menu.addAction('Keep workspace tabs on one line', host.single_line_tabs)
        sizes = menu.addMenu('Toolbar icon size')
        for size in (16, 20, 24, 32, 40, 48):
            sizes.addAction(str(size) + ' px', lambda _=False, size=size: host.window.set_toolbar_icon_size(size))
        menu.addSeparator()
        menu.addAction('Restore default row order', host.reset)
        menu.exec(event.globalPos())
        menu.deleteLater()


class HeaderRow(QFrame):
    def __init__(self, host, key, title, content):
        super().__init__(host)
        self.host, self.key, self.title = host, key, title
        self.content = content
        self.setObjectName('movableHeaderRow')
        self.setAcceptDrops(True)
        self.setStyleSheet('QFrame#movableHeaderRow { border:1px solid transparent; }'
                           'QFrame#movableHeaderRow[selected="true"] { border:1px solid palette(highlight); }'
                           'QFrame#movableHeaderRow[dropEdge="top"] { border-top:2px solid palette(highlight); }'
                           'QFrame#movableHeaderRow[dropEdge="left"] { border-left:2px solid palette(highlight); }'
                           'QFrame#movableHeaderRow[dropEdge="right"] { border-right:2px solid palette(highlight); }'
                           'QFrame#movableHeaderRow[dropEdge="bottom"] { border-bottom:2px solid palette(highlight); }')
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        layout = QHBoxLayout()
        outer.addLayout(layout)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        self.grip = Grip(self)
        layout.addWidget(self.grip, 0, Qt.AlignmentFlag.AlignTop)
        self.display = ConnectionStrip(content) if key == 'connection' else content
        layout.addWidget(self.display, 1)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        if key == 'overview':
            content.setMinimumHeight(0)
            content.setMaximumHeight(16777215)
            self.height_grip = HeightGrip(self)
            outer.addWidget(self.height_grip)
            self.set_height(host.window.preferences.value('Layout/workspace_list_height', 100, type=int), save=False)

    def minimumSizeHint(self):
        size = super().minimumSizeHint()
        return QSize(min(size.width(), 100), size.height())

    def set_height(self, height, save=True):
        item_height = max(self.content.sizeHintForRow(0), self.content.fontMetrics().height() + 8)
        minimum = self.content.header().height() + item_height + 2 * self.content.frameWidth() + 11
        height = max(minimum, min(600, int(height)))
        self.setFixedHeight(height)
        if save:
            self.host.window.preferences.setValue('Layout/workspace_list_height', height)

    def compact(self):
        self.set_height(0)

    def valid_drag(self, event):
        source = event.source()
        return isinstance(source, Grip) and source.row.host is self.host and event.mimeData().hasFormat(MIME)

    def dragEnterEvent(self, event):
        if self.valid_drag(event):
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()

    def dragMoveEvent(self, event):
        self.dragEnterEvent(event)
        if self.valid_drag(event):
            self.set_drop_edge(self.drop_edge(event.position()))

    def drop_edge(self, point):
        if point.y() <= 8: return 'top'
        if point.y() >= self.height() - 8: return 'bottom'
        return 'left' if point.x() < self.width() / 2 else 'right'

    def set_drop_edge(self, edge):
        self.setProperty('dropEdge', edge)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def dragLeaveEvent(self, event):
        self.set_drop_edge('')
        event.accept()

    def dropEvent(self, event):
        self.set_drop_edge('')
        if self.valid_drag(event):
            edge = self.drop_edge(event.position())
            if edge in ('left', 'right'):
                self.host.join(event.source().row.key, self.key, after=edge == 'right')
            else:
                self.host.drop(event.source().row.key, self.key, edge == 'bottom')
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()


class HeaderRows(QWidget):
    def __init__(self, window, contents):
        super().__init__(window)
        self.window = window
        self.layout_rows = QVBoxLayout(self)
        self.layout_rows.setContentsMargins(0, 0, 0, 0)
        self.layout_rows.setSpacing(2)
        self.rows = {key: HeaderRow(self, key, title, content) for key, title, content in contents}
        try:
            saved = json.loads(window.preferences.value('Layout/header_row_order', '[]', type=str))
            valid = isinstance(saved, list) and len(saved) == len(DEFAULT_ORDER) and set(saved) == set(DEFAULT_ORDER)
        except (ValueError, TypeError):
            valid = False
        order = saved if valid else list(DEFAULT_ORDER)
        try:
            groups = json.loads(window.preferences.value('Layout/header_row_groups', '[]', type=str))
            valid_groups = (isinstance(groups, list) and 0 < len(groups) <= 4 and
                all(isinstance(group, list) and group and all(isinstance(k, str) for k in group) for group in groups) and
                [key for group in groups for key in group] == order)
        except (ValueError, TypeError):
            valid_groups = False
        self.groups = groups if valid_groups else [[key] for key in order]
        self.bands = []
        self.rebuild()

    def order(self):
        return [key for group in self.groups for key in group]

    def rebuild(self):
        for row in self.rows.values():
            row.setParent(self)
        while self.layout_rows.count():
            widget = self.layout_rows.takeAt(0).widget()
            if widget: widget.deleteLater()
        self.bands = []
        for group in self.groups:
            band = QSplitter(Qt.Orientation.Horizontal, self)
            band.setChildrenCollapsible(False)
            band.setHandleWidth(5)
            band.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
            for key in group:
                band.addWidget(self.rows[key])
                self.rows[key].show()
            self.layout_rows.addWidget(band)
            key = 'Layout/header_band_widths/' + '+'.join(group)
            try:
                widths = json.loads(self.window.preferences.value(key, '[]', type=str))
                if len(widths) == len(group) and all(type(n) is int and 0 < n < 20000 for n in widths):
                    band.setSizes(widths)
            except (ValueError, TypeError):
                pass
            band.splitterMoved.connect(lambda *_, band=band, key=key:
                self.window.preferences.setValue(key, json.dumps(band.sizes())))
            self.bands.append(band)
        self.updateGeometry()
        if hasattr(self.window, 'layout_lock'): self.window.layout_lock.apply()
        if hasattr(self.window, 'toolbar_visibility'): self.window.toolbar_visibility.apply()

    def save(self):
        self.window.preferences.setValue('Layout/header_row_order', json.dumps(self.order()))
        self.window.preferences.setValue('Layout/header_row_groups', json.dumps(self.groups))
        self.window.preferences.sync()

    def single_line_tabs(self):
        self.window.preferences.setValue('Layout/multiple_lines', False)
        self.window.apply_layout_settings()

    def join(self, key, target, after=True):
        if key == target: return
        self.groups = [[k for k in group if k != key] for group in self.groups]
        self.groups = [group for group in self.groups if group]
        group = next(group for group in self.groups if target in group)
        group.insert(group.index(target) + int(after), key)
        if 'tabs' in group: self.single_line_tabs()
        self.rebuild()
        self.save()

    def detach(self, key):
        group = next(group for group in self.groups if key in group)
        if len(group) == 1: return
        index = self.groups.index(group)
        group.remove(key)
        self.groups.insert(index + 1, [key])
        self.rebuild()
        self.save()

    def select(self, key):
        for name, row in self.rows.items():
            row.setProperty('selected', name == key)
            row.style().unpolish(row)
            row.style().polish(row)
            row.update()

    def set_order(self, order):
        if len(order) != len(DEFAULT_ORDER) or set(order) != set(DEFAULT_ORDER):
            return
        self.groups = [[key] for key in order]
        self.rebuild()
        self.save()

    def move(self, key, delta):
        order = self.order()
        old = order.index(key)
        new = old + delta
        if 0 <= new < len(order):
            self.drop(key, order[new], delta > 0)
            self.select(key)
            self.rows[key].grip.setFocus()

    def drop(self, key, target, after):
        if key == target:
            return
        self.groups = [[k for k in group if k != key] for group in self.groups]
        self.groups = [group for group in self.groups if group]
        index = next(i for i, group in enumerate(self.groups) if target in group)
        self.groups.insert(index + int(after), [key])
        self.rebuild()
        self.save()
        self.select(key)

    def reset(self):
        self.set_order(list(DEFAULT_ORDER))
