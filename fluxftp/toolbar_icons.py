"""Crisp, distinct toolbar symbols with descriptive hover text."""
from functools import lru_cache
from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QIcon, QPainter, QPalette, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QToolButton


FOLDER = 'M3 7V5h7l2 2h9v13H3Z'
GLYPHS = {
    'local': 'M3 3h18v13H3ZM8 21h8M12 16v5',
    'remote': 'M3 3h18v7H3ZM3 14h18v7H3ZM6 6.5h1M6 17.5h1M12 6.5h6M12 17.5h6M12 10v4',
    'sites': FOLDER + ' M7 12h10M7 16h6',
    'options': 'M4 6h16M4 12h16M4 18h16M8 3v6M16 9v6M10 15v6',
    'workspace': 'M3 4h18v16H3ZM3 8h18M12 8v12M6 14h3M7.5 12.5v3',
    'verify': 'M14 3H4v18h16V9M14 3v6h6ZM7 14l3 3 6-6',
    'disconnect': 'M8 3v4M14 3v4M6 7h10v3l-4 4v4H8v-4l-2-4ZM16 16l5 5M21 16l-5 5',
    'swap': 'M3 7h17l-4-4M20 7l-4 4M21 17H4l4-4M4 17l4 4',
    'parent': 'M12 21V3M5 10l7-7 7 7',
    'bookmark': 'M12 3l3 6 7 1-5 5 1 7-6-3-6 3 1-7-5-5 7-1Z',
    'start': 'M6 3l15 9-15 9Z',
    'stop': 'M5 5h14v14H5Z',
    'refresh': 'M20 9a8 8 0 1 0 0 6M20 3v6h-6',
    'compare': 'M2 3h8v18H2ZM14 3h8v18h-8ZM5 8h2M17 8h2M5 12h2M17 12h2M5 16h2M17 16h2',
    'right': 'M3 12h18M14 5l7 7-7 7',
    'left': 'M21 12H3M10 5l-7 7 7 7',
    'folder': FOLDER + ' M12 10v7M8.5 13.5h7',
    'rename': 'M4 16l-1 5 5-1L21 7l-4-4ZM14 6l4 4M12 21h9',
    'delete': 'M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7',
}

# Match existing action descriptions; callbacks and shortcut ownership stay intact.
DESCRIPTIONS = {
    'Manage, import, and connect': ('sites', 'Sites', 'Manage, import, and connect to saved sites (F3).'),
    'Configure connection': ('options', 'Options', 'Configure connections, transfers, display, and confirmations.'),
    'Open another two-pane': ('workspace', 'New workspace', 'Open another pair of file panes.'),
    'Verify files against': ('verify', 'Verify SFV', 'Check file checksums against an SFV manifest.'),
    'Connect to a saved': ('sites', 'Sites', 'Choose Quick Connect, a recent connection, or a saved site.'),
    'Disconnect': ('disconnect', 'Disconnect', 'Disconnect this pane from its remote server.'),
    'Switch the remote': ('swap', 'Switch servers', 'Exchange the remote servers between the two panes.'),
    'Parent directory': ('parent', 'Parent directory', 'Go up one folder.'),
    'Remote directory': ('bookmark', 'Bookmarks', 'Save or open a remote directory bookmark.'),
    'Start or resume': ('start', 'Start queue', 'Start or resume queued transfers.'),
    'Stop queued': ('stop', 'Stop queue', 'Stop queued transfers.'),
    'Refresh this': ('refresh', 'Refresh', 'Reload the current folder listing (F5).'),
    'Compare folder': ('compare', 'Compare folders', 'Compare the contents of both panes (Ctrl+D).'),
    'Transfer selected': ('right', 'Transfer selection', 'Transfer selected files and folders to the other pane (Ctrl+T).'),
    'Create a folder': ('folder', 'New folder', 'Create a folder in this pane.'),
    'Rename selected': ('rename', 'Rename', 'Rename the selected file or folder.'),
    'Delete selected': ('delete', 'Delete', 'Delete the selected files and folders.'),
}


@lru_cache(maxsize=128)
def icon_for(key, ink, disabled):
    icon = QIcon()
    for mode, color in ((QIcon.Mode.Normal, ink), (QIcon.Mode.Disabled, disabled)):
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
               f'<path d="{GLYPHS[key]}" fill="none" stroke="{color}" stroke-width="2" '
               'stroke-linecap="round" stroke-linejoin="round"/></svg>')
        renderer = QSvgRenderer(QByteArray(svg.encode()))
        for size in (16, 20, 24, 32, 40, 48, 64, 96):
            pixmap = QPixmap(size, size)
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            renderer.render(painter)
            painter.end()
            icon.addPixmap(pixmap, mode)
    return icon


def icon_colors(window):
    from .themes import palette_for
    palette = palette_for(window.preferences)
    ink = palette['text'] if palette else window.palette().color(QPalette.ColorRole.ButtonText).name()
    disabled = palette['muted'] if palette else window.palette().color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText).name()
    return ink, disabled


def apply_mode_icon(pane):
    mode = pane.mode
    target = 'Remote' if mode == 'Local' else 'Local'
    description = f'Showing {mode.lower()} files. Switch this pane to {target.lower()} files.'
    button = pane.mode_button
    button.setText(mode)
    button.setIcon(icon_for(mode.lower(), *icon_colors(pane.window)))
    button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
    button.setToolTip(f'{mode} — Switch to {target}\n{description}')
    button.setStatusTip(description)
    button.setAccessibleName(f'{mode}: switch to {target}')
    button.setAccessibleDescription(description)
    button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)


def apply_toolbar_icons(window):
    ink, disabled = icon_colors(window)
    bars = [(window.main_toolbar, False)]
    for index in range(window.tabs.count()):
        split = window.tabs.widget(index)
        for side in (0, 1):
            apply_mode_icon(split.widget(side))
        bars.extend((split.widget(side).file_toolbar, side == 1) for side in (0, 1))
    for bar, right_pane in bars:
        bar.setAttribute(Qt.WidgetAttribute.WA_AlwaysShowToolTips, True)
        items = [*bar.actions(), *bar.findChildren(QToolButton)]
        for item in items:
            if isinstance(item, QToolButton):
                item.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
                if item.objectName() == 'qt_toolbar_ext_button':
                    item.setAccessibleName('More toolbar commands')
                    item.setToolTip('More toolbar commands')
            spec = getattr(item, '_toolbar_description', None)
            if spec is None and isinstance(item, QToolButton):
                spec = getattr(item.defaultAction(), '_toolbar_description', None)
            if spec is None:
                spec = next((value for prefix, value in DESCRIPTIONS.items()
                             if item.toolTip().startswith(prefix)), None)
            if spec is None:
                continue
            item._toolbar_description = spec
            key, title, description = spec
            if not item.text():
                item.setText(title)
            if key == 'right' and right_pane:
                key = 'left'
            item.setIcon(icon_for(key, ink, disabled))
            item.setToolTip(title + '\n' + description)
            item.setStatusTip(description)
            if isinstance(item, QToolButton):
                item.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
                item.setAccessibleName(title)
                item.setAccessibleDescription(description)

