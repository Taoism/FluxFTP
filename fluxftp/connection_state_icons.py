"""Theme-aware FluxFTP status icons for connections and transfers."""
from pathlib import Path
from functools import lru_cache

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon

STATES = frozenset(('idle', 'connecting', 'connected', 'uploading', 'downloading', 'fxp', 'error'))
QUEUE_STATE_ROLE = Qt.ItemDataRole.UserRole + 52


@lru_cache(maxsize=14)
def connection_state_icon(state, dark=False):
    """Load one of the packaged connection-state SVGs for a light or dark surface."""
    if state not in STATES:
        raise ValueError(f'Unknown connection state: {state}')
    theme = 'dark' if dark else 'light'
    path = Path(__file__).parent / 'assets' / 'connection-states' / theme / f'{state}.svg'
    return QIcon(str(path))


def set_queue_state(row, state, settings):
    from .themes import dark_theme
    row.setData(7, QUEUE_STATE_ROLE, state)
    row.setIcon(7, connection_state_icon(state, dark_theme(settings)))
    tree = row.treeWidget()
    if tree is not None:
        refresh_application_state_icon(tree.window())


def refresh_application_state_icon(window):
    if not hasattr(window, 'queue') or not hasattr(window, 'preferences'):
        return
    from .queue_tree import STATE_ROLE, rows
    active = [row.data(7, QUEUE_STATE_ROLE) for row in rows(window.queue)
              if row.data(0, STATE_ROLE) == 'active']
    if active:
        state = next((value for value in ('error', 'fxp', 'connecting', 'uploading', 'downloading')
                      if value in active), 'connecting')
    elif any(row.data(0, STATE_ROLE) == 'failed' for row in rows(window.queue)):
        state = 'error'
    else:
        state = 'idle'
    from .themes import dark_theme
    dark = dark_theme(window.preferences)
    if state == 'idle':
        icon_name = 'fluxftp-icon-dark.svg' if dark else 'fluxftp-icon.svg'
        icon = QIcon(str(Path(__file__).parent / 'assets' / icon_name))
    else:
        icon = connection_state_icon(state, dark)
    window.setWindowIcon(icon)
    if hasattr(window, 'app_instance'):
        window.app_instance.setWindowIcon(icon)


def refresh_queue_state_icons(window, dark):
    if not hasattr(window, 'queue'):
        return
    from .queue_tree import rows
    for row in rows(window.queue):
        state = row.data(7, QUEUE_STATE_ROLE)
        if state in STATES:
            row.setIcon(7, connection_state_icon(state, dark))
    refresh_application_state_icon(window)


def transfer_state(route):
    route = str(route).casefold()
    if route == 'upload':
        return 'uploading'
    if route in ('download', 'copy'):
        return 'downloading'
    if route in ('fxp', 'relay'):
        return 'fxp'
    return 'connecting'
