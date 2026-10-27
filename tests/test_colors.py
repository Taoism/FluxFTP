import logging
from PySide6.QtWidgets import QApplication
from fluxftp.preferences import PreferencesDialog
from fluxftp.color_options import color_value, LIST_DEFAULTS, LOG_DEFAULTS, ColorControl
from fluxftp import color_options
from fluxftp.diagnostics import Trace
from test_queue import window as base_window
import pytest
from test_integration import servers
from test_ui import settle


@pytest.fixture
def window(base_window):
    base_window.preferences.setValue('Display/style', 'System')
    return base_window


def test_defaults_and_independent_themes(window):
    dialog = PreferencesDialog(window)
    assert len(dialog.color_controls) == 15
    for scope, roles in LIST_DEFAULTS.items():
        assert dialog.color_themes[scope].currentText() == '(Default)'
        for role, value in roles.items(): assert color_value(window.preferences, scope, role) == value
    for role, value in LOG_DEFAULTS.items(): assert color_value(window.preferences, 'log', role) == value
    dialog.color_themes['remote'].setCurrentText('Midnight')
    left = window.current_panes()[0]
    assert '#171e27' in left.tree.styleSheet() and '#dce6f2' in left.tree.styleSheet()
    window.preferences.setValue('Display/hide_unfocused_selection', True)
    window.apply_colors()
    assert 'background:transparent; color:#dce6f2' in left.tree.styleSheet()
    assert '#171e27' not in window.queue.styleSheet()
    left.change_mode('Local')
    assert '#171e27' not in left.tree.styleSheet()
    dialog._set_display_color('local', 'odd', '#abcdef')
    assert dialog.color_themes['local'].currentText() == 'Custom'
    assert '#abcdef' in left.tree.styleSheet()
    dialog.color_themes['local'].setCurrentText('(Default)')
    assert color_value(window.preferences, 'local', 'odd') == '#ffffff'
    dialog.color_themes['queue'].setCurrentText('Light')
    assert '#eaf1f8' in window.queue.styleSheet()
    window.add_workspace()
    assert '#171e27' in window.current_panes()[1].tree.styleSheet()
    dialog.close()
    dialog = PreferencesDialog(window)
    assert dialog.color_themes['remote'].currentText() == 'Midnight'
    dialog.close()


def test_picker_cancel_and_custom_color(window, monkeypatch):
    from PySide6.QtGui import QColor
    control = ColorControl('#ffffff')
    changes = []
    control.changed.connect(changes.append)
    monkeypatch.setattr(color_options.QColorDialog, 'getColor', lambda *_: QColor())
    control._choose()
    assert not changes
    monkeypatch.setattr(color_options.QColorDialog, 'getColor', lambda *_: QColor('#123456'))
    control._choose()
    assert changes == ['#123456'] and control.combo.currentData() == '#123456'
    control.close()


def block_colors(window):
    QApplication.processEvents()
    window.ftp_highlighter.rehighlight()
    block = window.ftp_log.document().firstBlock()
    result = {}
    while block.isValid():
        formats = block.layout().formats()
        if formats: result[block.text()] = formats[0].format.foreground().color().name()
        block = block.next()
    return result


def test_log_categories_recolor_existing_text_and_background(window):
    messages = [('FTP > NOOP', 1, 'command'), ('FTP < 200 OK', 1, 'reply1'),
        ('FTP < 220 Hello', 2, 'reply2'), ('FTP < 550 Missing', 2, 'error'),
        ('ERROR: Failed', 1, 'error'), ('Connecting', 1, 'prompt')]
    for text, side, _ in messages: window.append_ftp_message(text, side)
    colors = block_colors(window)
    for text, side, kind in messages: assert colors[f'[{side}] {text}'] == LOG_DEFAULTS[kind]
    dialog = PreferencesDialog(window)
    dialog._set_display_color('log', 'reply2', '#abcdef')
    dialog._set_display_color('log', 'background', '#123456')
    assert block_colors(window)['[2] FTP < 220 Hello'] == '#abcdef'
    assert '#123456' in window.ftp_log.styleSheet()
    dialog.close()


def test_trace_listener_receives_only_redacted_messages():
    events = []
    trace = Trace('secret-token')
    trace.listener = lambda text, level: events.append((text, level))
    trace.event('FTP < 200 secret-token\nPASS other-password')
    trace.event('Failure secret-token', logging.ERROR)
    assert 'secret-token' not in str(events) and 'other-password' not in str(events)
    assert '\nPASS [REDACTED]' in events[0][0]
    assert events[1][1] == logging.ERROR


def test_background_ftp_listings_keep_pane_identity_and_redact(window, servers):
    _, site = servers()
    left, right = window.current_panes()
    left.set_remote(site, 'password')
    right.set_remote(site, 'password')
    left.refresh()
    right.refresh()
    settle(window)
    text = window.ftp_log.toPlainText()
    assert '[1] FTP < 220' in text and '[2] FTP < 220' in text
    assert '[1] FTP > PASS [REDACTED]' in text
    assert 'password' not in text.lower()
    colors = block_colors(window)
    assert any(text.startswith('[1] FTP < 220') and color == '#0000ff' for text, color in colors.items())
    assert any(text.startswith('[2] FTP < 220') and color == '#008000' for text, color in colors.items())


def test_legacy_selection_and_invalid_colors_fall_back(window):
    window.preferences.setValue('Display/selection_color', '#123456')
    assert color_value(window.preferences, 'local', 'selection') == '#123456'
    window.preferences.setValue('Colors/local/selection', 'bad color')
    assert color_value(window.preferences, 'local', 'selection') == '#123456'
    window.preferences.setValue('Colors/local/selection', '#abcdef')
    assert color_value(window.preferences, 'local', 'selection') == '#abcdef'


def test_queue_persists_pane_identity_and_accepts_old_descriptions(window, tmp_path):
    from fluxftp.queue_manager import write_queue, read_queue
    from test_queue import descriptor
    path = tmp_path / 'sides.json'
    old = descriptor(tmp_path)
    write_queue(path, [old, dict(old, source_side=2, destination_side=1)])
    loaded = read_queue(path)
    assert (loaded[0]['source_side'], loaded[0]['destination_side']) == (1, 2)
    assert (loaded[1]['source_side'], loaded[1]['destination_side']) == (2, 1)
