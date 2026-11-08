"""Compact, distinct icons for saved-site protocols and site folders."""
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtSvg import QSvgRenderer


SITE_MARKS = {
    'FTP · Unencrypted': ('FTP', '#3678c8'),
    'FTPS · AUTH TLS': ('TLS', '#168567'),
    'FTPS · AUTH SSL': ('SSL', '#168567'),
    'FTPS · Implicit TLS': ('TLS', '#168567'),
    'SFTP · SSH': ('SSH', '#9a62b5'),
    'SCP · SSH': ('SCP', '#b07830'),
    'Amazon S3': ('S3', '#d78a22'),
    'Backblaze B2': ('B2', '#d44335'),
    'Box': ('BOX', '#3276ca'),
    'Dropbox': ('DB', '#2876df'),
    'Google Cloud Storage': ('GCS', '#4285f4'),
    'Google Drive': ('GD', '#30a66a'),
    'Microsoft Azure Blob Storage': ('AZ', '#2670bd'),
    'Microsoft OneDrive': ('OD', '#2c86c8'),
    'Microsoft OneDrive for Business': ('OD', '#2c86c8'),
    'Microsoft SharePoint Online': ('SP', '#4776aa'),
    'OpenStack Swift': ('SW', '#d34a42'),
    'WebDAV': ('WEB', '#62818e'),
}


def _svg(mark, color):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
            f'<path d="M12 2.5a9.5 9.5 0 1 0 0 19 9.5 9.5 0 0 0 0-19Z" fill="{color}"/>'
            f'<text x="12" y="15.3" text-anchor="middle" font-family="Segoe UI, sans-serif" '
            f'font-size="{7 if len(mark) > 2 else 9}" font-weight="700" fill="#fff">{mark}</text></svg>')


@lru_cache(maxsize=64)
def _site_icon(mark, color):
    icon = QIcon()
    renderer = QSvgRenderer(QByteArray(_svg(mark, color).encode('utf-8')))
    for size in (16, 20, 24, 32, 48):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        renderer.render(painter)
        painter.end()
        icon.addPixmap(pixmap)
    return icon


def site_icon(protocol):
    mark, color = SITE_MARKS.get(protocol, ('?', '#667788'))
    return _site_icon(mark, color)


def folder_icon(window):
    custom = window.preferences.value('Display/folder_icon_path', '', type=str)
    if custom and Path(custom).is_file():
        icon = QIcon(custom)
        if not icon.isNull():
            return icon
    from .folder_icons import folder_icon_for
    from .themes import palette_for
    colors = palette_for(window.preferences)
    if colors:
        base, surface, text = colors['base'], colors['surface'], colors['text']
    else:
        palette = window.palette()
        base = palette.color(QPalette.ColorRole.Base).name()
        surface = palette.color(QPalette.ColorRole.Window).name()
        text = palette.color(QPalette.ColorRole.Text).name()
    return folder_icon_for(window.preferences, base, surface, text)
