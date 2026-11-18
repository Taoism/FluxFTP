"""Explicit names for composite Qt fields and their editable child controls."""
from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import (QWidget, QFormLayout, QLabel, QLineEdit, QComboBox,
    QAbstractSpinBox, QAbstractButton, QAbstractItemView, QPlainTextEdit, QTextEdit,
    QApplication, QMenu)


def name_control(control, name, description=''):
    control.setAccessibleName(name.replace('&', ''))
    if description:
        control.setAccessibleDescription(description)
    if isinstance(control, (QComboBox, QAbstractSpinBox)):
        for editor in control.findChildren(QLineEdit):
            editor.setAccessibleName(name.replace('&', ''))


def label_forms(root):
    """QFormLayout cannot associate labels with editors inside composite widgets."""
    inputs = (QLineEdit, QComboBox, QAbstractSpinBox, QAbstractItemView, QPlainTextEdit, QTextEdit)
    for form in root.findChildren(QFormLayout):
        for row in range(form.rowCount()):
            label_item = form.itemAt(row, QFormLayout.ItemRole.LabelRole)
            field_item = form.itemAt(row, QFormLayout.ItemRole.FieldRole)
            if not label_item or not field_item or not isinstance(label_item.widget(), QLabel):
                continue
            label = label_item.widget()
            field = field_item.widget()
            if field is None:
                continue
            controls = [field] if isinstance(field, inputs) else field.findChildren(QWidget)
            for control in controls:
                if isinstance(control, inputs) and not control.accessibleName():
                    name_control(control, label.text())
                    if label.buddy() is None:
                        label.setBuddy(control)
                elif isinstance(control, QAbstractButton) and not control.accessibleName():
                    text = control.text().strip('.… ')
                    name_control(control, (text or 'Choose') + ' ' + label.text())
    enable_copyable_text(root)


def enable_copyable_text(root):
    """Make static labels selectable and give selected label text a Copy menu."""
    for label in root.findChildren(QLabel):
        _make_label_copyable(label)


def _make_label_copyable(label):
    label.setTextInteractionFlags(label.textInteractionFlags() |
        Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard)
    if not label.property('_copy_menu_installed'):
        label.setProperty('_copy_menu_installed', True)
        label.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        label.customContextMenuRequested.connect(lambda point, current=label: _label_copy_menu(current, point))


class _CopyableLabelFilter(QObject):
    def eventFilter(self, watched, event):
        if isinstance(watched, QLabel) and event.type() in (QEvent.Type.Polish, QEvent.Type.Show):
            _make_label_copyable(watched)
        return False


def install_copyable_text_support(application):
    """Keep labels in later dialogs copyable too."""
    if not getattr(application, '_copyable_label_filter', None):
        application._copyable_label_filter = _CopyableLabelFilter(application)
        application.installEventFilter(application._copyable_label_filter)


def _label_copy_menu(label, point):
    text = label.selectedText()
    if not text:
        return
    menu = QMenu(label)
    copy = menu.addAction('Copy')
    copy.triggered.connect(lambda: QApplication.clipboard().setText(text))
    menu.exec(label.mapToGlobal(point))


def fit_dialog(dialog):
    """Keep dialog actions on screen at high display scaling."""
    screen = dialog.screen()
    if screen is None:
        return
    area = screen.availableGeometry()
    width, height = max(320, area.width() - 24), max(240, area.height() - 48)
    dialog.setMinimumSize(min(dialog.minimumWidth(), width), min(dialog.minimumHeight(), height))
    dialog.resize(min(dialog.width(), width), min(dialog.height(), height))
