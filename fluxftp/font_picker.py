"""Font picker with a public, keyboard-editable point-size control."""
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFontComboBox,
    QDoubleSpinBox, QCheckBox, QLabel, QFormLayout, QHBoxLayout)


class FontPicker(QDialog):
    def __init__(self, initial, parent=None, title='Change font'):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(470, 260)
        self.initial = QFont(initial)
        form = QFormLayout(self)
        self.family = QFontComboBox()
        self.family.setCurrentFont(initial)
        form.addRow('&Font', self.family)
        self.size = QDoubleSpinBox()
        self.size.setObjectName('fontPointSize')
        self.size.setRange(1, 512)
        self.size.setDecimals(1)
        self.size.setKeyboardTracking(False)
        self.size.setValue(initial.pointSizeF() if initial.pointSizeF() > 0 else 11)
        form.addRow('&Size (points)', self.size)
        styles = QHBoxLayout()
        self.styles = {}
        for name in ('bold', 'italic', 'underline', 'strikeOut'):
            control = QCheckBox('Strikeout' if name == 'strikeOut' else name.title())
            control.setChecked(getattr(initial, name)())
            control.toggled.connect(self.refresh)
            self.styles[name] = control
            styles.addWidget(control)
        form.addRow('Style', styles)
        self.preview = QLabel('AaBbYyZz 0123456789')
        self.preview.setMinimumHeight(70)
        form.addRow('Preview', self.preview)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        form.addRow(self.buttons)
        self.family.currentFontChanged.connect(self.refresh)
        self.size.valueChanged.connect(self.refresh)
        self.refresh()

    def selected_font(self):
        font = QFont(self.initial)
        font.setFamily(self.family.currentFont().family())
        font.setPointSizeF(self.size.value())
        for name, control in self.styles.items():
            getattr(font, 'set' + name[0].upper() + name[1:])(control.isChecked())
        return font

    def refresh(self, *_):
        self.preview.setFont(self.selected_font())

    def accept(self):
        # Commit typed text even when OK is reached via a default-button shortcut.
        self.size.interpretText()
        super().accept()

    @staticmethod
    def getFont(initial, parent=None, title='Change font'):
        dialog = FontPicker(initial, parent, title)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        result = (dialog.selected_font() if accepted else QFont(initial)), accepted
        dialog.deleteLater()
        return result
