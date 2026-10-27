"""Coordinated application palettes, without overwriting saved custom colors."""
from PySide6.QtCore import QObject, QTimer, Qt
from PySide6.QtGui import QColor, QPalette, QIcon, QPixmap, QAction, QGuiApplication
from PySide6.QtWidgets import QAbstractButton

PALETTES = {
    'Classic': dict(surface='#f2f3f5', base='#ffffff', alternate='#edf3fa', text='#172b43',
                    muted='#596b80', border='#adbed1', selection='#c9ddf3', accent='#24558a',
                    header='#dce8f5', error='#b52b3b', reply1='#24558a', reply2='#216446', prompt='#714083'),
    'Black': dict(surface='#141414', base='#080808', alternate='#111111', text='#e8e8e8',
                  muted='#a8a8a8', border='#383838', selection='#30363d', accent='#d4e1ee',
                  header='#1e1e1e', error='#f3a6af', reply1='#b5d0eb', reply2='#abd4b9', prompt='#cdbbdf'),
    'Dark Grey': dict(surface='#303030', base='#242424', alternate='#2c2c2c', text='#ededed',
                      muted='#b5b5b5', border='#606060', selection='#484848', accent='#dddddd',
                      header='#393939', error='#ffb3bb', reply1='#b8d8fa', reply2='#b4dfc3', prompt='#dac4ed'),
    'Light': dict(surface='#f4f7fb', base='#ffffff', alternate='#eaf1f8', text='#203247',
                  muted='#5e7085', border='#bccbdb', selection='#cedff3', accent='#245e96',
                  header='#e3edf7', error='#ad303f', reply1='#245e96', reply2='#23694f', prompt='#70458c'),
    'Midnight': dict(surface='#202731', base='#171e27', alternate='#222e3c', text='#dce6f2',
                 muted='#98aabe', border='#415269', selection='#344e6b', accent='#a7caef',
                 header='#29384a', error='#f2a0aa', reply1='#a7caef', reply2='#a2d6bc', prompt='#ccb5e6'),
}


def migrate_theme(settings):
    for key in ('Display/style', 'Display/header_theme', 'Colors/local/theme', 'Colors/remote/theme', 'Colors/queue/theme'):
        if settings.value(key, '', type=str) == 'Dark':
            settings.setValue(key, 'Midnight')


def theme_name(settings):
    name = settings.value('Display/style', 'Classic', type=str)
    if name == 'Follow OS':
        return 'Midnight' if os_dark() else 'Light'
    return 'Midnight' if name == 'Dark' else name


def os_dark(system=False):
    """Windows distinguishes app appearance from taskbar appearance."""
    import sys
    if sys.platform == 'win32':
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                    r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize') as key:
                return winreg.QueryValueEx(key, 'SystemUsesLightTheme' if system else 'AppsUseLightTheme')[0] == 0
        except OSError:
            pass
    app = QGuiApplication.instance()
    return bool(app and app.styleHints().colorScheme() == Qt.ColorScheme.Dark)


class SystemAppearance(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.signature = None
        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self.refresh)
        window.app_instance.styleHints().colorSchemeChanged.connect(self.refresh)
        self.timer.start()
        self.refresh()

    def refresh(self, *_):
        if getattr(self, '_closed', False):
            return
        window = self.window
        follow = window.preferences.value('Display/style', '', type=str) == 'Follow OS'
        signature = (follow, os_dark() if follow else None, os_dark(system=True))
        if signature == self.signature:
            return
        self.signature = signature
        if follow:
            apply_application(window.app_instance, window.preferences, window.STYLE)
            window.apply_display_settings()
            from .preferences import PreferencesDialog
            for dialog in window.findChildren(PreferencesDialog):
                dialog._refresh_theme_controls()
        from .tray_menu import tray_icons
        window.tray_normal, window.tray_transfer = tray_icons(signature[2])
        window.update_tray()

    def close(self):
        if getattr(self, '_closed', False):
            return
        self._closed = True
        self.timer.stop()
        self.window.app_instance.styleHints().colorSchemeChanged.disconnect(self.refresh)


def contrast(foreground, background):
    def luminance(value):
        c = QColor(value)
        channels = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4
                    for v in (c.redF(), c.greenF(), c.blueF())]
        return sum(v * weight for v, weight in zip(channels, (.2126, .7152, .0722)))
    low, high = sorted((luminance(foreground), luminance(background)))
    return (high + .05) / (low + .05)


def readable_text(foreground, background):
    if QColor(foreground).isValid() and contrast(foreground, background) >= 4.5:
        return QColor(foreground).name()
    return max(('#000000', '#ffffff'), key=lambda color: contrast(color, background))


def header_style(settings, native_palette):
    mode = settings.value('Display/header_theme', 'Follow theme', type=str)
    if mode == 'Follow theme':
        mode = theme_name(settings)
    if mode == 'Dark':
        mode = 'Midnight'
    p = PALETTES.get(mode)
    if p:
        start, end, text, border = p['header'], p['surface'], p['text'], p['border']
    elif mode == 'System':
        start = end = native_palette.color(QPalette.ColorRole.Window).name()
        text = readable_text(native_palette.color(QPalette.ColorRole.WindowText).name(), start)
        border = native_palette.color(QPalette.ColorRole.Mid).name()
    else:
        start, end, text, border = '#b9d5f4', '#edf4fc', '#183b63', '#aebfd2'
    background = (f'qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 {start},stop:1 {end})'
                  if settings.value('Display/gradient_indicator', True, type=bool) else start)
    return f'background:{background}; color:{text}; font-weight:600; padding:5px 7px; border:1px solid {border};'


def palette_for(settings):
    return PALETTES.get(theme_name(settings))


def scope_colors(settings, scope):
    p = palette_for(settings)
    if not p:
        return None
    if scope == 'log':
        return dict(background=p['base'], command=p['text'],
                    **{key: p[key] for key in ('error', 'reply1', 'reply2', 'prompt')})
    return dict(even=p['base'], odd=p['alternate'], text=p['text'], selection=p['selection'])


def comparison_color(settings, different):
    if theme_name(settings) in ('Midnight', 'Dark Grey', 'Black'):
        return '#49313b' if different else '#3c3829'
    return '#ffd9cc' if different else '#fff0c2'


def stylesheet(settings, classic):
    p = palette_for(settings)
    if not p:
        return '' if settings.value('Display/style', 'Classic', type=str) == 'System' else classic
    css = '''
QWidget {{ background:{surface}; color:{text}; }}
QWidget:disabled {{ color:{muted}; }}
QMenuBar,QToolBar,QStatusBar,QHeaderView::section {{ background:{header}; color:{text}; }}
QMenu,QToolTip {{ background:{surface}; color:{text}; border:1px solid {border}; }}
QMenu::item {{ padding:5px 26px; }}
QMenu::item:selected,QMenuBar::item:selected {{ background:{selection}; }}
QPushButton,QToolButton {{ background:{header}; border:1px solid {border}; padding:4px 8px; border-radius:2px; }}
QPushButton:hover,QToolButton:hover,QPushButton:pressed,QToolButton:checked {{ background:{selection}; }}
QLineEdit,QSpinBox,QDoubleSpinBox,QComboBox,QAbstractItemView,QPlainTextEdit,QTextEdit {{ background:{base}; color:{text}; border:1px solid {border}; selection-background-color:{selection}; selection-color:{text}; }}
QAbstractItemView {{ alternate-background-color:{alternate}; }}
QAbstractItemView::item:selected {{ background:{selection}; color:{text}; }}
QTreeWidget::item {{ padding:3px 5px; }}
QLineEdit,QSpinBox,QComboBox {{ padding:4px 6px; }}
QHeaderView::section {{ border:0; border-right:1px solid {border}; border-bottom:1px solid {border}; padding:5px 7px; }}
QTabWidget::pane,QGroupBox {{ border:1px solid {border}; }}
QGroupBox {{ margin-top:9px; padding-top:9px; }}
QGroupBox::title {{ subcontrol-origin:margin; left:8px; }}
QTabBar::tab {{ background:{header}; border:1px solid {border}; padding:5px 12px; }}
QTabBar::tab:selected {{ background:{selection}; color:{accent}; }}
QSplitter::handle,QScrollBar {{ background:{header}; }}
QScrollBar::handle {{ background:{border}; min-height:20px; min-width:20px; }}
QProgressBar {{ background:{base}; color:{text}; border:1px solid {border}; text-align:center; }}
QProgressBar::chunk {{ background:{selection}; }}
QLabel#brand {{ color:{accent}; font-size:19px; font-weight:600; }}
QLabel#muted {{ color:{muted}; }}
QLineEdit:focus,QSpinBox:focus,QDoubleSpinBox:focus,QComboBox:focus {{ border:2px solid {accent}; padding:3px 5px; }}
QPushButton:focus,QToolButton:focus {{ border:2px solid {accent}; padding:3px 7px; }}
QCheckBox:focus,QRadioButton:focus {{ color:{accent}; text-decoration:underline; }}
QAbstractItemView:focus,QPlainTextEdit:focus,QTextEdit:focus {{ border:2px solid {accent}; }}
'''.format(**p)
    if theme_name(settings) == 'Black':
        css += '''
QLineEdit:focus,QComboBox:focus,QSpinBox:focus { border:1px solid #899bad; }
QPushButton:hover,QToolButton:hover { background:#292d32; border-color:#718090; }
QPushButton:pressed,QToolButton:pressed { background:#363e47; }
QPushButton:disabled,QToolButton:disabled { background:#181818; color:#929292; border-color:#303030; }
QMenu::separator { height:1px; background:#383838; margin:4px 8px; }
QTabBar::tab:selected { border-top:2px solid #b6c9dc; }
QScrollBar::handle:hover { background:#626b75; }
'''
    return css


def apply_application(app, settings, classic):
    migrate_theme(settings)
    if not hasattr(app, '_fluxftp_original_palette'):
        app._fluxftp_original_palette = QPalette(app.palette())
        app._fluxftp_original_style = app.style().objectName()
    app.setStyleSheet('')
    palette = QPalette(app._fluxftp_original_palette)
    p = palette_for(settings)
    wanted_style = 'fusion' if p else app._fluxftp_original_style
    if app.style().objectName().lower() != wanted_style.lower():
        app.setStyle(wanted_style)
    if p:
        for role, key in (('Window', 'surface'), ('WindowText', 'text'), ('Base', 'base'),
                          ('AlternateBase', 'alternate'), ('Text', 'text'), ('Button', 'header'),
                          ('ButtonText', 'text'), ('Highlight', 'selection'), ('HighlightedText', 'text'),
                          ('ToolTipBase', 'surface'), ('ToolTipText', 'text'), ('Link', 'accent'),
                          ('Mid', 'border'), ('Light', 'header'), ('Dark', 'base'), ('Shadow', 'base')):
            palette.setColor(getattr(QPalette.ColorRole, role), QColor(p[key]))
        for role in ('Text', 'WindowText', 'ButtonText'):
            palette.setColor(QPalette.ColorGroup.Disabled, getattr(QPalette.ColorRole, role), QColor(p['muted']))
    app.setPalette(palette)
    app.setStyleSheet(stylesheet(settings, classic))


def apply_icons(window):
    p = palette_for(window.preferences)
    color = p['accent'] if p else None
    items = [*window.findChildren(QAction), *window.findChildren(QAbstractButton)]
    for item in items:
        if not hasattr(item, '_theme_original_icon'):
            if item.icon().isNull():
                continue
            item._theme_original_icon = QIcon(item.icon())
            item._theme_icon_color = None
    for item in items:
        if not hasattr(item, '_theme_original_icon'):
            continue
        if item._theme_icon_color == color:
            continue
        icon = item._theme_original_icon
        if color:
            themed = QIcon()
            ink, paper = QColor(color), QColor(p['header'])
            for size in (16, 32, 48):
                pixels = icon.pixmap(size, size).toImage()
                for y in range(pixels.height()):
                    for x in range(pixels.width()):
                        original = pixels.pixelColor(x, y)
                        if not original.alpha():
                            continue
                        amount = (.2126 * original.redF() + .7152 * original.greenF() + .0722 * original.blueF())
                        pixels.setPixelColor(x, y, QColor.fromRgbF(
                            ink.redF() * (1 - amount) + paper.redF() * amount,
                            ink.greenF() * (1 - amount) + paper.greenF() * amount,
                            ink.blueF() * (1 - amount) + paper.blueF() * amount,
                            original.alphaF()))
                themed.addPixmap(QPixmap.fromImage(pixels))
            icon = themed
        item.setIcon(icon)
        item._theme_icon_color = color
    from .toolbar_icons import apply_toolbar_icons
    apply_toolbar_icons(window)
