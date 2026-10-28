"""Expandable queue groups without changing their worker jobs."""
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QPainter, QPainterPath, QLinearGradient, QPalette
from PySide6.QtWidgets import QTreeWidget, QStyledItemDelegate, QStyleOptionViewItem, QStyle

GROUP_ROLE = Qt.ItemDataRole.UserRole + 40
STATE_ROLE = Qt.ItemDataRole.UserRole + 41
RATE_ROLE = Qt.ItemDataRole.UserRole + 42
PROGRESS_ROLE = Qt.ItemDataRole.UserRole + 43
SUMMARY_ROLE = Qt.ItemDataRole.UserRole + 44
RETRY_ROLE = Qt.ItemDataRole.UserRole + 45
SPEED_ROLE = Qt.ItemDataRole.UserRole + 46
STREAM_ROLE = Qt.ItemDataRole.UserRole + 47
MARKER_PARENT_ROLE = Qt.ItemDataRole.UserRole + 48
MARKER_OWNER_ROLE = Qt.ItemDataRole.UserRole + 49
JOB_ID_ROLE = Qt.ItemDataRole.UserRole + 51


class ReleaseProgress(QStyledItemDelegate):
    def paint(self, painter, option, index):
        value = index.data(PROGRESS_ROLE)
        if value is None:
            return super().paint(painter, option, index)
        cell = QStyleOptionViewItem(option)
        self.initStyleOption(cell, index)
        cell.text = ''
        option.widget.style().drawControl(QStyle.ControlElement.CE_ItemViewItem, cell, painter, option.widget)
        rect = QRectF(option.rect.adjusted(4, 3, -4, -3))
        if rect.width() <= 0 or rect.height() <= 0: return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        outline = QPainterPath()
        outline.addRoundedRect(rect, 4, 4)
        painter.fillPath(outline, option.palette.brush(QPalette.ColorRole.AlternateBase))
        fill = option.palette.color(QPalette.ColorRole.Link)
        gradient = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        gradient.setColorAt(0, fill.lighter(120))
        gradient.setColorAt(1, fill)
        filled = QRectF(rect)
        filled.setWidth(rect.width() * max(0, min(100, value)) / 100)
        painter.setClipPath(outline)
        painter.fillRect(filled, gradient)
        painter.setFont(option.font)
        painter.setPen(option.palette.color(QPalette.ColorRole.Text))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, f'{value}%')
        painter.setClipRect(filled, Qt.ClipOperation.IntersectClip)
        painter.setPen(Qt.GlobalColor.white if fill.lightnessF() < .55 else Qt.GlobalColor.black)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, f'{value}%')
        painter.setClipping(False)
        painter.setPen(option.palette.color(QPalette.ColorRole.Mid))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(outline)
        painter.restore()

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        if index.data(PROGRESS_ROLE) is not None:
            size.setHeight(max(size.height(), option.fontMetrics.height() + 8, 24))
        return size


def rows(tree):
    stack = [(tree.invisibleRootItem(), 0)]
    while stack:
        parent, index = stack.pop()
        if index < parent.childCount():
            row = parent.child(index)
            stack.append((parent, index + 1))
            yield row
            if row.childCount(): stack.append((row, 0))


def selected_rows(tree):
    result = []
    for row in rows(tree):
        ancestor = row
        while ancestor is not None:
            if ancestor.isSelected():
                result.append(row)
                break
            ancestor = ancestor.parent()
    return result


def detach(tree, row):
    parent = row.parent() or tree.invisibleRootItem()
    index = parent.indexOfChild(row)
    if index >= 0:
        parent.takeChild(index)


class QueueTree(QTreeWidget):
    def __init__(self):
        super().__init__()
        self.setRootIsDecorated(True)
        self.setExpandsOnDoubleClick(False)
        self.setItemDelegateForColumn(1, ReleaseProgress(self))

    def mouseReleaseEvent(self, event):
        row = self.itemAt(event.position().toPoint())
        toggle = (event.button() == Qt.MouseButton.LeftButton and row is not None and row.childCount()
                  and self.columnAt(int(event.position().x())) == 0
                  and event.position().x() >= self.visualItemRect(row).left())
        super().mouseReleaseEvent(event)
        if toggle:
            row.setExpanded(not row.isExpanded())

    def mouseDoubleClickEvent(self, event):
        row = self.itemAt(event.position().toPoint())
        if row is not None and row.childCount():
            event.accept()
            return
        super().mouseDoubleClickEvent(event)
