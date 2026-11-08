"""Folder icons that remain legible on dark application palettes."""
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QStyle


def _mix(first, second, weight):
    a, b = QColor(first), QColor(second)
    return QColor.fromRgbF(
        a.redF() * (1 - weight) + b.redF() * weight,
        a.greenF() * (1 - weight) + b.greenF() * weight,
        a.blueF() * (1 - weight) + b.blueF() * weight,
    ).name()


@lru_cache(maxsize=64)
def _folder_icon(fill, flap, edge, icon_set):
    if icon_set == 'Outline':
        artwork = (f'<path d="M3 5h6.8l2 2H21v13H3Z" fill="none" stroke="{edge}" '
                   'stroke-width="1.7" stroke-linejoin="round"/>'
                   f'<path d="M4 8.7h16" fill="none" stroke="{edge}" stroke-width="1"/>')
    else:
        artwork = (f'<path d="M3 5h6.8l2 2H21v13H3Z" fill="{fill}" stroke="{edge}" '
                   'stroke-width="1.4" stroke-linejoin="round"/>'
                   f'<path d="M4 5.8h5.1l1.5 1.3H4Z" fill="{flap}"/>'
                   f'<path d="M3.8 9h16.4" fill="none" stroke="{edge}" stroke-width=".8"/>')
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">{artwork}</svg>'
    renderer = QSvgRenderer(QByteArray(svg.encode('utf-8')))
    icon = QIcon()
    for size in (16, 20, 24, 32):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        renderer.render(painter)
        painter.end()
        icon.addPixmap(pixmap)
    return icon


def directory_icon(pane):
    custom = pane.window.preferences.value('Display/folder_icon_path', '', type=str)
    if custom and Path(custom).is_file():
        icon = QIcon(custom)
        if not icon.isNull():
            return icon
    from .themes import palette_for
    colors = palette_for(pane.window.preferences)
    if colors:
        base, surface, text = colors['base'], colors['surface'], colors['text']
    else:
        palette = pane.tree.palette()
        base = palette.color(QPalette.ColorRole.Base).name()
        surface = palette.color(QPalette.ColorRole.Window).name()
        text = palette.color(QPalette.ColorRole.Text).name()
    return folder_icon_for(pane.window.preferences, base, surface, text)


def folder_icon_for(settings, base, surface, text):
    light_background = QColor(base).lightness() >= 128
    icon_set = settings.value('Display/folder_icon_set', 'Soft grayscale', type=str)
    if icon_set == 'Black & white':
        fill = '#4a4d52' if light_background else '#e2e4e7'
        flap = '#757980' if light_background else '#b6bbc1'
        edge = '#303338' if light_background else '#f3f4f5'
    elif icon_set == 'Windows tan':
        fill, flap, edge = '#d5aa50', '#f0d487', '#8b6626'
    elif icon_set == 'Outline':
        fill = flap = base
        edge = _mix(base, text, .62)
    elif light_background:
        fill = _mix(surface, base, .24)
        edge = _mix(base, text, .38)
        flap = _mix(fill, text, .16)
    else:
        fill = _mix(base, surface, .58)
        edge = _mix(base, text, .42)
        flap = _mix(fill, text, .18)
    return _folder_icon(fill, flap, edge, icon_set)
