"""Independent list palettes and sanitized, categorized FTP log presentation."""
from PySide6.QtGui import QColor, QSyntaxHighlighter, QTextCharFormat, QBrush
from PySide6.QtWidgets import QWidget, QHBoxLayout, QComboBox, QPushButton, QColorDialog
from PySide6.QtCore import Signal

LIST_DEFAULTS = {
    'remote': dict(selection='#3399ff', even='#ffffff', odd='#ffffff'),
    'local': dict(selection='#0078d7', even='#ffffff', odd='#ffffff'),
    'queue': dict(selection='#0078d7', even='#ffffff', odd='#ffffff'),
}
LOG_DEFAULTS = dict(background='#ffffff', error='#c62828', reply1='#0000ff',
                    reply2='#008000', command='#000000', prompt='#800000')


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

    def __init__(self, value, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self.combo = QComboBox()
        for name, color in (('White', '#ffffff'), ('Black', '#000000'), ('Red', '#ff0000'), ('Blue', '#0000ff'),
                            ('Green', '#008000'), ('Maroon', '#800000'), ('Gray', '#808080')):
            self.combo.addItem(name, color)
        self.choose = QPushButton('…')
        self.choose.setMaximumWidth(32)
        row.addWidget(self.combo)
        row.addWidget(self.choose)
        self.set_color(value)
        self.combo.currentIndexChanged.connect(self._changed)
        self.choose.clicked.connect(self._choose)

    def set_color(self, value):
        self.combo.blockSignals(True)
        index = self.combo.findData(value)
        if index < 0:
            self.combo.addItem(value, value)
            index = self.combo.count() - 1
        self.combo.setCurrentIndex(index)
        color = QColor(value)
        foreground = '#000000' if color.lightnessF() > .55 else '#ffffff'
        self.combo.setStyleSheet(f'QComboBox {{ background:{value}; color:{foreground}; }}')
        self.combo.blockSignals(False)

    def _changed(self):
        value = self.combo.currentData()
        self.set_color(value)
        self.changed.emit(value)

    def _choose(self):
        color = QColorDialog.getColor(QColor(self.combo.currentData()), self, 'Choose color')
        if color.isValid():
            self.set_color(color.name())
            self.changed.emit(color.name())


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
