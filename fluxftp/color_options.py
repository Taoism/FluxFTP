"""Independent list palettes and sanitized, categorized FTP log presentation."""
from PySide6.QtGui import QColor, QSyntaxHighlighter, QTextCharFormat, QBrush
from PySide6.QtWidgets import QWidget, QHBoxLayout, QComboBox, QColorDialog, QInputDialog
from PySide6.QtCore import Qt, Signal
import json

LIST_DEFAULTS = {
    'remote': dict(selection='#3399ff', even='#ffffff', odd='#ffffff'),
    'local': dict(selection='#0078d7', even='#ffffff', odd='#ffffff'),
    'queue': dict(selection='#0078d7', even='#ffffff', odd='#ffffff'),
}
LOG_DEFAULTS = dict(background='#ffffff', error='#c62828', reply1='#0000ff',
                    reply2='#008000', command='#000000', prompt='#800000')
DEFAULT_COLOR_CHOICES = (('White', '#ffffff'), ('Black', '#000000'), ('Red', '#ff0000'), ('Blue', '#0000ff'),
                         ('Green', '#008000'), ('Maroon', '#800000'), ('Gray', '#808080'), ('Orange', '#ff8000'))


def color_value(settings, scope, role):
    from .themes import scope_colors
    themed = scope_colors(settings, scope)
    if themed:
        return themed[role]
    default = LOG_DEFAULTS[role] if scope == 'log' else LIST_DEFAULTS[scope][role]
    if scope != 'log' and role == 'selection':
        default = settings.value('Display/selection_color', default, type=str)
        if not QColor(default).isValid(): default = LIST_DEFAULTS[scope][role]
    value = settings.value(f'Colors/{scope}/{role}', default, type=str)
    value = QColor(value).name() if QColor(value).isValid() else default
    if scope == 'log' and role != 'background':
        from .themes import readable_text
        value = readable_text(value, color_value(settings, 'log', 'background'))
    return value


def list_style(settings, scope):
    values = {key: color_value(settings, scope, key) for key in LIST_DEFAULTS[scope]}
    from .themes import scope_colors
    themed = scope_colors(settings, scope)
    text = themed['text'] if themed else settings.value(f'Colors/{scope}/text', '#17202a', type=str)
    if not QColor(text).isValid(): text = '#17202a'
    from .themes import readable_text
    text = readable_text(text, values['even'])
    alternate_text = readable_text(text, values['odd'])
    foreground = readable_text(text, values['selection'])
    return (f"QTreeWidget {{ background-color:{values['even']}; alternate-background-color:{values['odd']}; color:{QColor(text).name()}; }}"
            f"QTreeWidget::item:alternate {{ color:{alternate_text}; }}"
            f"QTreeWidget::item:selected {{ background-color:{values['selection']}; color:{foreground}; }}")


class ColorControl(QWidget):
    changed = Signal(str)

    def __init__(self, value, parent=None, *, settings=None, setting_key=None, choices=None):
        super().__init__(parent)
        self.settings = settings
        self.setting_key = setting_key
        self._custom_labels = {}
        if settings is not None and setting_key:
            try:
                loaded = json.loads(settings.value(f'Colors/custom_names/{setting_key}', '{}', type=str))
                if isinstance(loaded, dict):
                    self._custom_labels = {QColor(color).name(): str(name)
                        for color, name in loaded.items() if QColor(color).isValid() and str(name).strip()}
            except (TypeError, ValueError):
                pass
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self.combo = QComboBox()
        for name, color in (choices or DEFAULT_COLOR_CHOICES):
            self.combo.addItem(name, color)
            self._set_swatch(self.combo.count() - 1, color)
        self._preset_count = self.combo.count()
        for color, name in self._custom_labels.items():
            self.combo.addItem(name, color)
            self._set_swatch(self.combo.count() - 1, color)
        self._custom_index = self.combo.count()
        self.combo.addItem('Custom', '')
        self._delete_index = self.combo.count()
        self.combo.addItem('Delete custom color…', '__delete_custom_color__')
        row.addWidget(self.combo)
        self.set_color(value)
        self.combo.activated.connect(self._selected)
        self._refresh_delete_enabled()

    @staticmethod
    def _text_color(value):
        color = QColor(value)
        channels = [component / 255 for component in (color.red(), color.green(), color.blue())]
        linear = [channel / 12.92 if channel <= .04045 else ((channel + .055) / 1.055) ** 2.4
                  for channel in channels]
        luminance = .2126 * linear[0] + .7152 * linear[1] + .0722 * linear[2]
        contrast_black = (luminance + .05) / .05
        contrast_white = 1.05 / (luminance + .05)
        return '#000000' if contrast_black >= contrast_white else '#ffffff'

    def _set_swatch(self, index, value):
        foreground = self._text_color(value)
        self.combo.setItemData(index, QColor(value), Qt.ItemDataRole.BackgroundRole)
        self.combo.setItemData(index, QColor(foreground), Qt.ItemDataRole.ForegroundRole)

    def set_color(self, value):
        color = QColor(value)
        if not color.isValid():
            return
        value = color.name()
        self.combo.blockSignals(True)
        index = -1
        if value in self._custom_labels:
            for candidate in range(self._preset_count, self._custom_index):
                if self.combo.itemData(candidate, Qt.ItemDataRole.UserRole) == value:
                    index = candidate
                    break
        if index < 0:
            index = self.combo.findData(value)
        if index < 0:
            index = self._custom_index
            self.combo.setItemData(index, value, Qt.ItemDataRole.UserRole)
            self._set_swatch(index, value)
        self.combo.setCurrentIndex(index)
        foreground = self._text_color(value)
        self.combo.setStyleSheet(f'QComboBox {{ background:{value}; color:{foreground}; }}')
        self.combo.blockSignals(False)
        self.current_color = value

    def setCurrentIndex(self, index):
        self.combo.setCurrentIndex(index)
        self._selected(index)

    def currentData(self):
        return self.current_color

    def _selected(self, index):
        if index == self._custom_index:
            self._choose_custom()
            return
        if index == self._delete_index:
            self._delete_custom()
            return
        value = self.combo.itemData(index, Qt.ItemDataRole.UserRole)
        if value:
            self.set_color(value)
            self.changed.emit(value)

    def _choose_custom(self):
        color = QColorDialog.getColor(QColor(self.current_color), self, 'Choose custom color')
        if color.isValid():
            value = color.name()
            name = f'Custom {value.upper()}'
            if self.settings is not None and self.setting_key:
                entered, accepted = QInputDialog.getText(self, 'Name custom color', 'Color name:',
                    text=self._custom_labels.get(value, 'Custom'))
                if accepted and entered.strip():
                    name = entered.strip()
                elif accepted:
                    name = f'Custom {value.upper()}'
            for candidate in range(self._preset_count, self._custom_index):
                if self.combo.itemData(candidate, Qt.ItemDataRole.UserRole) == value:
                    self.combo.removeItem(candidate)
                    self._custom_index -= 1
                    self._delete_index -= 1
                    break
            self._custom_labels[value] = name
            if self.settings is not None and self.setting_key:
                self.settings.setValue(f'Colors/custom_names/{self.setting_key}',
                    json.dumps(self._custom_labels, ensure_ascii=False))
            self.combo.blockSignals(True)
            self.combo.insertItem(self._custom_index, name, value)
            self._set_swatch(self._custom_index, value)
            self.combo.setCurrentIndex(self._custom_index)
            self._custom_index += 1
            self._delete_index += 1
            self._refresh_delete_enabled()
            foreground = self._text_color(value)
            self.combo.setStyleSheet(f'QComboBox {{ background:{value}; color:{foreground}; }}')
            self.combo.blockSignals(False)
            self.current_color = value
            self.changed.emit(value)
        else:
            self.set_color(self.current_color)

    def _choose(self):
        self._choose_custom()

    def _refresh_delete_enabled(self):
        item = self.combo.model().item(self._delete_index)
        if item is not None:
            item.setEnabled(bool(self._custom_labels))

    def _delete_custom(self):
        custom_indexes = list(range(self._preset_count, self._custom_index))
        if not custom_indexes:
            self._refresh_delete_enabled()
            return
        options = [f'{self.combo.itemText(index)} ({self.combo.itemData(index, Qt.ItemDataRole.UserRole)})'
                   for index in custom_indexes]
        current = next((i for i, index in enumerate(custom_indexes)
                        if self.combo.itemData(index, Qt.ItemDataRole.UserRole) == self.current_color), 0)
        chosen, accepted = QInputDialog.getItem(self, 'Delete custom color',
            'Choose a saved color to remove:', options, current, False)
        if not accepted:
            self.set_color(self.current_color)
            return
        selected = options.index(chosen)
        index = custom_indexes[selected]
        value = self.combo.itemData(index, Qt.ItemDataRole.UserRole)
        self._custom_labels.pop(value, None)
        if self.settings is not None and self.setting_key:
            self.settings.setValue(f'Colors/custom_names/{self.setting_key}',
                json.dumps(self._custom_labels, ensure_ascii=False))
        self.combo.blockSignals(True)
        self.combo.removeItem(index)
        self._custom_index -= 1
        self._delete_index -= 1
        self.combo.blockSignals(False)
        self.set_color(self.current_color)
        self._refresh_delete_enabled()


class FTPLogHighlighter(QSyntaxHighlighter):
    def __init__(self, document, settings):
        super().__init__(document)
        self.settings = settings

    def highlightBlock(self, text):
        import re
        message = re.sub(r'^\[[12]\] ', '', text)
        if message.startswith('FTP > '): kind = 'command'
        elif message.startswith('FTP < '):
            kind = 'error' if re.match(r'FTP < [45]\d\d', message) else 'reply2' if text.startswith('[2] ') else 'reply1'
        elif message.startswith('ERROR: '): kind = 'error'
        else: kind = 'prompt'
        style = QTextCharFormat()
        style.setForeground(QColor(color_value(self.settings, 'log', kind)))
        self.setFormat(0, len(text), style)


class ColorOptions:
    def initialize_colors(self):
        self.ftp_log.setMaximumBlockCount(self.preferences.value('General/log_lines', 300, type=int))
        self.ftp_highlighter = FTPLogHighlighter(self.ftp_log.document(), self.preferences)
        from PySide6.QtCore import Qt
        self.ftp_message.connect(self.append_ftp_message, Qt.ConnectionType.QueuedConnection)

    def append_ftp_message(self, message, side):
        reply = message.startswith('FTP < ')
        lines = message.split('\n')
        self.ftp_log.appendPlainText('\n'.join(
            f'[{side}] ' + ('FTP < ' if reply and index else '') + line
            for index, line in enumerate(lines)))

    def apply_colors(self):
        from .themes import apply_icons
        apply_icons(self)
        self._log_site_sides = {}
        for index in range(self.tabs.count()):
            split = self.tabs.widget(index)
            for side, pane in enumerate((split.widget(0), split.widget(1)), 1):
                pane.tree.setProperty('colorScope', pane.mode.lower())
                self.update_selection_display(pane.tree)
                from .compare_menu import RESULT_ROLE
                from .themes import comparison_color
                for row_index in range(pane.tree.topLevelItemCount()):
                    row = pane.tree.topLevelItem(row_index)
                    result = row.data(0, RESULT_ROLE)
                    if result in ('different', 'only'):
                        brush = QBrush(QColor(comparison_color(self.preferences, result == 'different')))
                        for column in range(row.columnCount()):
                            row.setBackground(column, brush)
                if pane.site: self._log_site_sides[self._transfer_site_key(pane.site)] = side
        self.queue.setStyleSheet(list_style(self.preferences, 'queue'))
        self.ftp_log.setStyleSheet(f"QPlainTextEdit {{ color:{color_value(self.preferences, 'log', 'command')}; background-color:{color_value(self.preferences, 'log', 'background')}; }}")
        if hasattr(self, 'ftp_command_input'):
            from .themes import palette_for, readable_text
            p = palette_for(self.preferences)
            from PySide6.QtGui import QPalette
            native = getattr(self.app_instance, '_fluxftp_original_palette', self.app_instance.palette())
            background = p['base'] if p else native.color(QPalette.ColorRole.Base).name()
            foreground = p['text'] if p else readable_text(native.color(QPalette.ColorRole.Text).name(), background)
            selected = p['selection'] if p else native.color(QPalette.ColorRole.Highlight).name()
            css = (f'QComboBox,QLineEdit,QAbstractItemView {{ background:{background}; color:{foreground}; '
                   f'selection-background-color:{selected}; selection-color:{readable_text(foreground, selected)}; }}')
            self.ftp_command_input.setStyleSheet(css)
            self.ftp_command_input.lineEdit().setStyleSheet(
                f'QLineEdit {{ background:{background}; color:{foreground}; border:0; padding:0; '
                f'selection-background-color:{selected}; selection-color:{readable_text(foreground, selected)}; }}')
            self.ftp_command_input.view().setStyleSheet(css)
        self.transfer_log.setStyleSheet(f"QPlainTextEdit {{ color:{color_value(self.preferences, 'log', 'prompt')}; background-color:{color_value(self.preferences, 'log', 'background')}; }}")
        if hasattr(self, 'ftp_highlighter'): self.ftp_highlighter.rehighlight()

    def color_tree_style(self, tree, hidden=False):
        scope = tree.property('colorScope') or 'remote'
        style = list_style(self.preferences, scope)
        if hidden:
            from .themes import scope_colors
            themed = scope_colors(self.preferences, scope)
            text = themed['text'] if themed else self.preferences.value(f'Colors/{scope}/text', '#17202a', type=str)
            if not QColor(text).isValid(): text = '#17202a'
            from .themes import readable_text
            text = readable_text(text, color_value(self.preferences, scope, 'even'))
            style += f'QTreeWidget::item:selected {{ background:transparent; color:{QColor(text).name()}; }}'
        return style
