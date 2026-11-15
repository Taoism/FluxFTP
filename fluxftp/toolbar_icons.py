"""Crisp, distinct toolbar symbols with descriptive hover text."""
from functools import lru_cache
from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPalette, QPixmap
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
    'refresh_both': 'M4 8a7 7 0 0 1 12-3l2 2M18 3v4h-4M20 16a7 7 0 0 1-12 3l-2-2M6 21v-4h4',
    'log': 'M4 4h16v16H4ZM7 8h10M7 12h10M7 16h6',
    'queue_window': 'M4 4h16v16H4ZM7 8h2M11 8h6M7 12h2M11 12h6M7 16h2M11 16h6',
    'terminal': 'M3 4h18v16H3ZM6 8l3 3-3 3M11 15h6',
    'layout': 'M3 4h18v16H3ZM3 9h18M9 9v11M13 13h5M13 16h5',
    'layout_save': 'M4 3h14l3 3v15H3V3ZM7 3v6h10V3M7 21v-8h10v8',
    'layout_load': 'M12 3v13m-5-5 5 5 5-5M4 21h16',
    'layout_default': 'M3 4h18v16H3ZM7 8h10M7 12h6M7 16h10',
    'popup': 'M3 4h18v13H3ZM8 21h8m-4-4v4M8 9h3m2 0h3M8 12h8',
    'lock_toolbar': 'M4 8h16v12H4ZM7 8V5a5 5 0 0 1 10 0v3m-5 5v3',
    'lock_dock': 'M3 4h18v16H3ZM3 9h18M9 9v11M14 13h4m-2-2v4',
    'tray': 'M3 5h18v14H3ZM3 12h5l2 3h4l2-3h5M8 8h8',
    'command': 'M4 5h2m4 0h10M4 12h2m4 0h10M4 19h2m4 0h10M7 5v0M7 12v0M7 19v0',
    'close': 'M5 5l14 14M19 5 5 19',
    'copy': 'M8 8h12v13H8ZM4 16H3V3h12v2',
    'paste': 'M8 4h8l1 3h3v14H4V7h3ZM9 4V2h6v2',
    'edit': 'M4 16l-1 5 5-1L20 8l-4-4ZM14 6l4 4',
    'view': 'M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6S2 12 2 12ZM12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6Z',
    'execute': 'M6 3l15 9-15 9ZM3 4v16',
    'clear': 'M4 7h16M9 7V4h6v3m-9 0 1 14h10l1-14M10 11v6m4-6v6',
    'select': 'M4 4h16v16H4ZM7 12l3 3 7-7',
    'undo': 'M9 8 4 12l5 4M4 12h10a6 6 0 0 1 6 6',
    'redo': 'M15 8l5 4-5 4m5-4H10a6 6 0 0 0-6 6',
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
    'Refresh both': ('refresh_both', 'Refresh Both', 'Reload the file listings in both panes.'),
    'Toggle log': ('log', 'Log Window', 'Show or hide the log window.'),
    'Toggle transfer': ('queue_window', 'Transfer Window', 'Show or hide the transfer queue.'),
    'Open the FTP command terminal': ('terminal', 'FTP Terminal', 'Open the FTP command terminal in the FTP Log panel.'),
}


ACCENT_PAIRS = {
    'local': ('#70baff', '#1769aa'),
    'remote': ('#c29bff', '#7042a0'),
    'sites': ('#ffd166', '#9a6800'),
    'options': ('#9db0c4', '#526579'),
    'workspace': ('#54d7c5', '#087e72'),
    'verify': ('#77d99a', '#21804b'),
    'disconnect': ('#ff8d91', '#ae353b'),
    'swap': ('#71c9e8', '#147491'),
    'parent': ('#b3ceff', '#4269ae'),
    'bookmark': ('#ffbd70', '#a45d14'),
    'start': ('#74df99', '#168447'),
    'stop': ('#ff8e94', '#bb343a'),
    'refresh': ('#75d5e1', '#167481'),
    'compare': ('#c2a0fa', '#69439d'),
    'right': ('#70baff', '#1769aa'),
    'left': ('#70baff', '#1769aa'),
    'folder': ('#ffd166', '#9a6800'),
    'rename': ('#ffbe79', '#a65f19'),
    'delete': ('#ff8d91', '#ae353b'),
    'refresh_both': ('#75d5e1', '#167481'),
    'log': ('#9db0c4', '#526579'),
    'queue_window': ('#c29bff', '#7042a0'),
    'terminal': ('#77d99a', '#21804b'),
    'layout': ('#91b9e8', '#3c6293'),
    'layout_save': ('#77d99a', '#21804b'),
    'layout_load': ('#bca2ed', '#6d4eaa'),
    'layout_default': ('#afbdca', '#536879'),
    'popup': ('#70d2c3', '#167c6f'),
    'lock_toolbar': ('#ffbe79', '#a65f19'),
    'lock_dock': ('#ffbe79', '#a65f19'),
    'tray': ('#c2a0fa', '#69439d'),
    'command': ('#9db0c4', '#526579'),
    'close': ('#ff8d91', '#ae353b'),
    'copy': ('#70baff', '#1769aa'),
    'paste': ('#ffd166', '#9a6800'),
    'edit': ('#ffbe79', '#a65f19'),
    'view': ('#75d5e1', '#167481'),
    'execute': ('#74df99', '#168447'),
    'clear': ('#ff8d91', '#ae353b'),
    'select': ('#77d99a', '#21804b'),
    'undo': ('#71c9e8', '#147491'),
    'redo': ('#71c9e8', '#147491'),
}

# Compact, filled artwork for the full-color Windows toolbar style.
COLOR_GLYPHS = {
    'local': '<rect x="2" y="3" width="20" height="14" rx="2" fill="#42647f"/><rect x="4" y="5" width="16" height="10" rx="1" fill="#70c9f4"/><path d="M10 17h4v3h4v2H6v-2h4z" fill="#71879a"/><circle cx="18" cy="4.5" r=".8" fill="#c6f4ff"/>',
    'remote': '<rect x="3" y="2" width="18" height="9" rx="2" fill="#477fc1"/><rect x="3" y="13" width="18" height="9" rx="2" fill="#315c96"/><path d="M5 5h2v2H5zm0 11h2v2H5z" fill="#7ff0a7"/><path d="M9 5h9v2H9zm0 11h9v2H9z" fill="#d9efff"/>',
    'sites': '<path d="M2 6a2 2 0 0 1 2-2h6l2 2h8a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2z" fill="#e5a82d"/><path d="M3 9h18l-2 9H4z" fill="#ffd45c"/><circle cx="16" cy="14" r="3" fill="#55aee7"/><path d="M13 14h6m-3-3a5 5 0 0 0 0 6m0-6a5 5 0 0 1 0 6" fill="none" stroke="#e9f8ff" stroke-width=".8"/>',
    'options': '<path d="M3 5h18v2H3zm0 6h18v2H3zm0 6h18v2H3z" fill="#879bad"/><circle cx="8" cy="6" r="2.5" fill="#36a6e8" stroke="#fff" stroke-width="1"/><circle cx="16" cy="12" r="2.5" fill="#f2b83f" stroke="#fff" stroke-width="1"/><circle cx="10" cy="18" r="2.5" fill="#52bd77" stroke="#fff" stroke-width="1"/>',
    'workspace': '<rect x="2" y="3" width="20" height="18" rx="2" fill="#4f6d8a"/><rect x="4" y="5" width="16" height="3" rx="1" fill="#9bc6e8"/><path d="M12 9v10M5 9h6v10H5z" fill="#55b5ef"/><path d="M13 9h6v10h-6z" fill="#65c993"/><circle cx="6" cy="6.5" r=".6" fill="#fff"/><circle cx="8" cy="6.5" r=".6" fill="#fff"/>',
    'verify': '<path d="M5 2h10l5 5v14a1 1 0 0 1-1 1H5a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2" fill="#edf4f8"/><path d="M15 2v5h5" fill="#b9d6e8"/><path d="M6 11h7v1.5H6zm0 3h5v1.5H6z" fill="#8ca7ba"/><circle cx="15.5" cy="16" r="4.5" fill="#48b86a"/><path d="m13.2 16 1.6 1.6 3-3.3" fill="none" stroke="#fff" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/>',
    'disconnect': '<path d="M8 2h3v6H8zm5 0h3v6h-3z" fill="#758a9e"/><path d="M6 6h12v4a6 6 0 0 1-5 5.9V20h-3v-4.1A6 6 0 0 1 6 10z" fill="#f2a83a"/><path d="M16 15 22 21m0-6-6 6" stroke="#e34c51" stroke-width="2.4" stroke-linecap="round"/>',
    'swap': '<path d="M3 7h15l-3-3 2-2 6 6-6 6-2-2 3-3H3z" fill="#39a7e8"/><path d="M21 17H6l3 3-2 2-6-6 6-6 2 2-3 3h15z" fill="#edaa36"/>',
    'parent': '<path d="M2 8a2 2 0 0 1 2-2h6l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2z" fill="#efbd48"/><path d="M12 18V9m-4 4 4-4 4 4" fill="none" stroke="#2877b7" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>',
    'bookmark': '<path d="M6 3a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v19l-6-4-6 4z" fill="#e8a52d"/><path d="M8 4h8v13l-4-2.5L8 17z" fill="#ffd66a"/><path d="M10 7h4v2h-4z" fill="#fff0b2"/>',
    'start': '<circle cx="12" cy="12" r="10" fill="#43ae66"/><circle cx="12" cy="12" r="7.5" fill="#66ce7d"/><path d="M10 7.5 17 12l-7 4.5z" fill="#fff"/>',
    'stop': '<circle cx="12" cy="12" r="10" fill="#d84a4f"/><circle cx="12" cy="12" r="7.5" fill="#f26769"/><rect x="8.5" y="8.5" width="7" height="7" rx="1" fill="#fff"/>',
    'refresh': '<path d="M20.5 8A9 9 0 0 0 5 5L3 7h5V2L6.4 3.6a11 11 0 0 1 16 3.5z" fill="#38a6e8"/><path d="M3.5 16A9 9 0 0 0 19 19l2-2h-5v5l1.6-1.6a11 11 0 0 1-16-3.5z" fill="#2582c5"/>',
    'compare': '<rect x="2" y="3" width="9" height="18" rx="1.5" fill="#438cc9"/><rect x="13" y="3" width="9" height="18" rx="1.5" fill="#657fba"/><path d="M4 7h5v1.5H4zm0 4h5v1.5H4zm0 4h5v1.5H4z" fill="#d9efff"/><path d="M15 7h5v1.5h-5zm0 4h5v1.5h-5zm0 4h5v1.5h-5z" fill="#e9e1ff"/>',
    'right': '<path d="M2 5a2 2 0 0 1 2-2h6l2 2h7a2 2 0 0 1 2 2v3H4a2 2 0 0 0-2 2z" fill="#eeb83e"/><path d="M2 12a2 2 0 0 1 2-2h18l-2 10H4a2 2 0 0 1-2-2z" fill="#ffd465"/><path d="M5 15h10m-4-4 4 4-4 4" fill="none" stroke="#267cb8" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>',
    'left': '<path d="M22 5a2 2 0 0 0-2-2h-6l-2 2H5a2 2 0 0 0-2 2v3h17a2 2 0 0 1 2 2z" fill="#eeb83e"/><path d="M22 12a2 2 0 0 0-2-2H2l2 10h16a2 2 0 0 0 2-2z" fill="#ffd465"/><path d="M19 15H9m4-4-4 4 4 4" fill="none" stroke="#267cb8" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>',
    'folder': '<path d="M2 6a2 2 0 0 1 2-2h6l2 2h8a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2z" fill="#e5a52d"/><path d="M3 9h18l-2 9H4z" fill="#ffd465"/><path d="M12 11v5m-2.5-2.5h5" stroke="#3183bd" stroke-width="1.8" stroke-linecap="round"/>',
    'rename': '<path d="M3 17.5 16.5 4a2.1 2.1 0 0 1 3 3L6 20.5 2 22z" fill="#f3b33e"/><path d="m15 5.5 3.5 3.5M3 17.5 6.5 21" stroke="#bd6d28" stroke-width="1.4"/><path d="m2 22 1-4.5L6.5 21z" fill="#65829a"/>',
    'delete': '<path d="M4 6h16l-1 15H5z" fill="#e95c61"/><path d="M3 4h18v2H3zm6-3h6l1 3H8z" fill="#bd3942"/><path d="M8 9h1.5v9H8zm3.3 0h1.5v9h-1.5zm3.3 0h1.5v9h-1.5z" fill="#ffe7e7"/>',
    'refresh_both': '<path d="M20.5 8A9 9 0 0 0 5 5L3 7h5V2L6.4 3.6a11 11 0 0 1 16 3.5z" fill="#38a6e8"/><path d="M3.5 16A9 9 0 0 0 19 19l2-2h-5v5l1.6-1.6a11 11 0 0 1-16-3.5z" fill="#2582c5"/>',
    'log': '<path d="M5 2h10l5 5v14a1 1 0 0 1-1 1H5a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2" fill="#f1f5f8"/><path d="M15 2v5h5" fill="#a8cde3"/><path d="M7 10h10v1.5H7zm0 4h10v1.5H7zm0 4h7v1.5H7z" fill="#438fc4"/>',
    'queue_window': '<rect x="2" y="3" width="20" height="18" rx="2" fill="#526e8d"/><rect x="4" y="5" width="16" height="14" rx="1" fill="#eaf1f7"/><circle cx="7" cy="9" r="1.4" fill="#42b66c"/><circle cx="7" cy="12.5" r="1.4" fill="#e7ab37"/><circle cx="7" cy="16" r="1.4" fill="#4ca4db"/><path d="M10 8h7v2h-7zm0 3.5h7v2h-7zm0 3.5h7v2h-7z" fill="#8399ad"/>',
    'terminal': '<rect x="2" y="3" width="20" height="18" rx="2" fill="#344b62"/><rect x="4" y="5" width="16" height="13" rx="1" fill="#182a3b"/><path d="m7 9 3 2.5L7 14m5 0h5" fill="none" stroke="#59d47d" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/><circle cx="6" cy="19.5" r=".7" fill="#e8ad3b"/><circle cx="8.5" cy="19.5" r=".7" fill="#e8ad3b"/>',
    'layout': '<rect x="2" y="3" width="20" height="18" rx="2" fill="#526e8d"/><path d="M3 8h18M9 8v12" stroke="#fff" stroke-width="1.5"/><rect x="12" y="11" width="7" height="2" rx="1" fill="#65c993"/><rect x="12" y="15" width="7" height="2" rx="1" fill="#65b7e8"/>',
    'layout_save': '<path d="M4 2h13l5 5v15H2V2z" fill="#4e87be"/><path d="M7 2v7h10V2M6 22v-9h12v9" fill="#eaf3fa"/><path d="M8 15h8v5H8z" fill="#65c993"/>',
    'layout_load': '<path d="M4 3h16v18H4z" fill="#eaf3fa" stroke="#5c7892" stroke-width="1.5"/><path d="M12 6v10m-4-4 4 4 4-4M7 19h10" fill="none" stroke="#2584c1" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>',
    'layout_default': '<rect x="2" y="3" width="20" height="18" rx="2" fill="#d8e4ef" stroke="#58748f"/><path d="M5 7h14M5 11h9M5 15h14" stroke="#438fc4" stroke-width="2"/><circle cx="18" cy="16" r="4" fill="#58bd78"/><path d="m16 16 1.3 1.3 2.7-3" fill="none" stroke="#fff" stroke-width="1.2"/>',
    'popup': '<rect x="2" y="3" width="20" height="15" rx="2" fill="#edf4f8" stroke="#5c7892"/><path d="M8 22h8m-4-4v4M5 7h5m3 0h6M5 11h13" stroke="#438fc4" stroke-width="1.5"/><circle cx="19" cy="18" r="4" fill="#f0b340"/>',
    'lock_toolbar': '<rect x="3" y="8" width="18" height="14" rx="2" fill="#6ba7d4"/><path d="M8 8V5a4 4 0 0 1 8 0v3" fill="none" stroke="#64788b" stroke-width="2.5"/><circle cx="12" cy="14" r="1.5" fill="#fff"/><path d="M12 15v3" stroke="#fff" stroke-width="1.5"/>',
    'lock_dock': '<rect x="2" y="3" width="20" height="18" rx="2" fill="#dce7f1" stroke="#58748f"/><path d="M3 8h18M9 8v12" stroke="#438fc4" stroke-width="1.5"/><rect x="13" y="12" width="6" height="5" rx="1" fill="#e8a52d"/><path d="M14 12v-1a2 2 0 0 1 4 0v1" fill="none" stroke="#9a6800" stroke-width="1.5"/>',
    'tray': '<rect x="2" y="4" width="20" height="16" rx="2" fill="#dce7f1" stroke="#58748f"/><path d="M2 11h6l2 4h4l2-4h6" fill="none" stroke="#438fc4" stroke-width="2"/><circle cx="12" cy="8" r="2" fill="#58bd78"/>',
    'command': '<rect x="3" y="4" width="18" height="16" rx="2" fill="#eaf1f7" stroke="#58748f"/><circle cx="7" cy="8" r="1.3" fill="#42a5dc"/><circle cx="7" cy="12" r="1.3" fill="#55bd78"/><circle cx="7" cy="16" r="1.3" fill="#edaa36"/><path d="M10 8h7m-7 4h7m-7 4h7" stroke="#687f95" stroke-width="1.5"/>',
    'close': '<circle cx="12" cy="12" r="10" fill="#e95c61"/><path d="m8 8 8 8m0-8-8 8" stroke="#fff" stroke-width="2"/>',
    'copy': '<rect x="7" y="7" width="14" height="15" rx="2" fill="#eaf3fa" stroke="#4b86b7"/><path d="M4 17H3V3h13v2" fill="none" stroke="#e0a936" stroke-width="2"/>',
    'paste': '<path d="M7 4h10l1 3h3v15H3V7h3z" fill="#eaf3fa" stroke="#58748f"/><path d="M8 4V2h8v2m-9 7h10m-10 4h10" fill="none" stroke="#438fc4" stroke-width="2"/>',
    'edit': '<path d="M3 17 16 4a2 2 0 0 1 3 3L6 20 2 22z" fill="#f3b33e"/><path d="m15 5 4 4M3 17l3 3" stroke="#bd6d28" stroke-width="1.5"/>',
    'view': '<path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6S2 12 2 12" fill="#dceaf5" stroke="#438fc4" stroke-width="1.5"/><circle cx="12" cy="12" r="3.2" fill="#58bd78"/><circle cx="12" cy="12" r="1.2" fill="#fff"/>',
    'execute': '<circle cx="12" cy="12" r="10" fill="#54bb74"/><path d="m9 6 9 6-9 6z" fill="#fff"/>',
    'clear': '<path d="M4 7h16l-1 15H5z" fill="#e95c61"/><path d="M3 4h18v3H3zm6-2h6l1 2H8z" fill="#bd3942"/><path d="M9 10v8m6-8v8" stroke="#fff" stroke-width="1.5"/>',
    'select': '<rect x="2" y="3" width="20" height="18" rx="2" fill="#eaf3fa" stroke="#58748f"/><path d="m6 12 4 4 8-8" fill="none" stroke="#42ad64" stroke-width="2.5"/>',
    'undo': '<path d="M10 7 3 12l7 5v-3h5a5 5 0 0 1 5 5 7 7 0 0 0-7-8h-3z" fill="#49a4d9"/>',
    'redo': '<path d="m14 7 7 5-7 5v-3H9a5 5 0 0 0-5 5 7 7 0 0 1 7-8h3z" fill="#49a4d9"/>'
}


@lru_cache(maxsize=256)
def icon_for(key, ink, disabled, icon_set='Theme matched'):
    icon = QIcon()
    width = '2.8' if icon_set == 'Bold' else '2'
    dark_background = QColor(ink).lightness() >= 128
    for mode, color in ((QIcon.Mode.Normal, ink), (QIcon.Mode.Disabled, disabled)):
        windows = icon_set in ('Windows', 'Classic color') and mode == QIcon.Mode.Normal
        if icon_set == 'Light (for dark theme)':
            color = '#e2e5e9' if mode == QIcon.Mode.Normal else '#8b9199'
        elif icon_set == 'Dark (for light theme)':
            color = '#343a40' if mode == QIcon.Mode.Normal else '#a0a5ab'
        elif icon_set == 'Color accents' and mode == QIcon.Mode.Normal:
            color = ACCENT_PAIRS.get(key, ('#70baff', '#1769aa'))[0 if dark_background else 1]
        if windows:
            svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
                   f'{COLOR_GLYPHS.get(key, "")}</svg>')
        else:
            svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
                   f'<path d="{GLYPHS[key]}" fill="none" stroke="{color}" stroke-width="{width}" '
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


def toolbar_button_style(window):
    layout = window.preferences.value('Display/icon_layout', 'Icons only', type=str)
    return {
        'Icons only': Qt.ToolButtonStyle.ToolButtonIconOnly,
        'Text beside icons': Qt.ToolButtonStyle.ToolButtonTextBesideIcon,
        'Text below icons': Qt.ToolButtonStyle.ToolButtonTextUnderIcon,
    }.get(layout, Qt.ToolButtonStyle.ToolButtonIconOnly)


def toolbar_icon_set(window):
    return window.preferences.value('Display/icon_set', 'Windows', type=str)


def apply_mode_icon(pane):
    mode = pane.mode
    target = 'Remote' if mode == 'Local' else 'Local'
    description = f'Showing {mode.lower()} files. Switch this pane to {target.lower()} files.'
    button = pane.mode_button
    button.setText(mode)
    button.setIcon(icon_for(mode.lower(), *icon_colors(pane.window), toolbar_icon_set(pane.window)))
    button.setToolButtonStyle(toolbar_button_style(pane.window))
    button.setToolTip(f'{mode} — Switch to {target}\n{description}')
    button.setStatusTip(description)
    button.setAccessibleName(f'{mode}: switch to {target}')
    button.setAccessibleDescription(description)
    button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)


def popup_action_icon_key(action):
    label = action.text().split('\t', 1)[0].replace('&', '').strip().casefold()
    if not label:
        return 'command'
    if 'rename' in label:
        return 'rename'
    if 'duplicate' in label or 'copy' in label or 'export' in label:
        return 'copy'
    if 'paste' in label:
        return 'paste'
    if 'folder' in label or 'directory' in label:
        return 'folder'
    if 'delete' in label or 'remove' in label:
        return 'delete'
    if 'close' in label or 'exit' in label or 'abort' in label:
        return 'close'
    if 'clear' in label:
        return 'clear'
    if 'refresh' in label or 'reload' in label:
        return 'refresh'
    if 'compare' in label:
        return 'compare'
    if 'queue' in label and ('transfer' not in label or 'selected queue' in label):
        return 'queue_window'
    if 'transfer' in label or 'upload' in label or 'download' in label:
        return 'right'
    if 'pause' in label or 'stop' in label:
        return 'stop'
    if 'resume' in label or 'start' in label or 'run' in label:
        return 'start'
    if 'select' in label or 'mark' in label:
        return 'select'
    if 'view' in label or 'find' in label or 'search' in label:
        return 'view'
    if 'edit' in label or 'properties' in label:
        return 'edit'
    if 'open' in label or 'navigate' in label or 'parent' in label:
        return 'folder'
    if 'log' in label or 'timestamp' in label:
        return 'log'
    if 'restore' in label or 'default' in label:
        return 'layout_default'
    if 'move' in label or 'switch' in label:
        return 'swap'
    return 'command'


def apply_popup_menu_icons(window, menu):
    ink, disabled = icon_colors(window)
    icon_set = toolbar_icon_set(window)
    def apply(current):
        for action in current.actions():
            if action.isSeparator():
                continue
            action.setIcon(icon_for(popup_action_icon_key(action), ink, disabled, icon_set))
            if action.menu():
                apply(action.menu())
    apply(menu)


def apply_toolbar_icons(window):
    ink, disabled = icon_colors(window)
    bars = [(window.main_toolbar, False)]
    for index in range(window.tabs.count()):
        split = window.tabs.widget(index)
        for side in (0, 1):
            apply_mode_icon(split.widget(side))
        bars.extend((split.widget(side).file_toolbar, side == 1) for side in (0, 1))
    for bar, right_pane in bars:
        bar.setToolButtonStyle(toolbar_button_style(window))
        bar.setAttribute(Qt.WidgetAttribute.WA_AlwaysShowToolTips, True)
        items = [*bar.actions(), *bar.findChildren(QToolButton)]
        for item in items:
            if isinstance(item, QToolButton):
                item.setToolButtonStyle(toolbar_button_style(window))
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
            item.setIcon(icon_for(key, ink, disabled, toolbar_icon_set(window)))
            item.setToolTip(title + '\n' + description)
            item.setStatusTip(description)
            if isinstance(item, QToolButton):
                item.setAccessibleName(title)
                item.setAccessibleDescription(description)
    if hasattr(window, 'layout_commands'):
        window.layout_commands.apply_icons()

