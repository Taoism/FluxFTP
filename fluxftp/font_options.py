"""Independent application, file/queue and log fonts."""
from PySide6.QtGui import QFont, QFontDatabase

SCOPES = ('application', 'files', 'logs')


def default_font(scope):
    families = QFontDatabase.families()
    if scope == 'logs':
        font = QFont('Terminal', 11) if 'Terminal' in families else QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        font.setPointSize(11)
        return font
    return QFont('Tahoma', 11) if 'Tahoma' in families else QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont)


def display_font(settings, scope):
    raw = settings.value('Display/font_' + scope, '', type=str)
    if not raw and scope == 'application': raw = settings.value('Display/font', '', type=str)
    font = QFont()
    return font if raw and font.fromString(raw) else default_font(scope)


def font_description(font):
    size = f'{font.pointSizeF():g} pt' if font.pointSizeF() > 0 else f'{font.pixelSize()} px'
    style = ', '.join(value for enabled, value in ((font.bold(), 'Bold'), (font.italic(), 'Italic'),
                                                  (font.underline(), 'Underline'), (font.strikeOut(), 'Strikeout')) if enabled)
    return f'{font.family()}, {size}' + (f', {style}' if style else '')
