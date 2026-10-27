from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication
from fluxftp.preferences import PreferencesDialog
from fluxftp.color_options import color_value
from fluxftp.themes import PALETTES, apply_application
from test_queue import window


def test_coordinated_themes_preserve_custom_colors_and_update_existing_widgets(window):
    settings = window.preferences
    settings.setValue('Colors/remote/even', '#ffeedd')
    settings.setValue('Colors/log/error', '#123456')
    dialog = PreferencesDialog(window)
    assert 'Themes' in dialog.page_by_name and 'Style' not in dialog.page_by_name
    for theme in PALETTES:
        dialog.theme_combo.setCurrentText(theme)
        p = PALETTES[theme]
        assert settings.value('Display/style', 'Classic') == theme
        for pane in window.current_panes():
            assert p['base'] in pane.tree.styleSheet()
            assert p['text'] in pane.title_label.styleSheet()
        assert p['base'] in window.queue.styleSheet()
        assert p['base'] in window.ftp_log.styleSheet()
        assert color_value(settings, 'log', 'error') == p['error']
        assert not dialog.color_controls[('log', 'error')].isEnabled()
        assert all(combo.currentText() == theme for combo in dialog.color_themes.values())
        assert QApplication.instance().palette().color(QPalette.ColorRole.Base).name() == p['base']
        window.add_workspace()
        assert p['base'] in window.current_panes()[0].tree.styleSheet()
    assert settings.value('Colors/remote/even') == '#ffeedd'
    dialog.theme_combo.setCurrentText('System')
    assert color_value(settings, 'remote', 'even') == '#ffeedd'
    assert color_value(settings, 'log', 'error') == '#123456'
    assert dialog.color_controls[('log', 'error')].isEnabled()
    dialog.close()


def test_header_modes_and_white_command_contrast(window):
    from fluxftp.themes import readable_text, contrast
    settings = window.preferences
    settings.setValue('Display/style', 'System')
    settings.setValue('Colors/log/background', '#ffffff')
    settings.setValue('Colors/log/command', '#ffffff')
    window.apply_colors()
    assert color_value(settings, 'log', 'command') == '#000000'
    assert settings.value('Colors/log/command') == '#ffffff'
    window.append_ftp_message('FTP > NOOP', 1)
    window.ftp_highlighter.rehighlight()
    block = window.ftp_log.document().firstBlock()
    assert block.layout().formats()[0].format.foreground().color().name() == '#000000'
    dialog = PreferencesDialog(window)
    dialog.theme_combo.setCurrentText('Dark Grey')
    for pane in window.current_panes():
        assert '#393939' in pane.title_label.styleSheet()
    dialog.header_theme_combo.setCurrentText('Light')
    for pane in window.current_panes():
        assert PALETTES['Light']['header'] in pane.title_label.styleSheet()
    dialog.close()
    dialog = PreferencesDialog(window)
    assert dialog.header_theme_combo.currentText() == 'Light'
    dialog.header_theme_combo.setCurrentText('Follow theme')
    for theme in ('Classic', 'System', *PALETTES):
        dialog.theme_combo.setCurrentText(theme)
        p = PALETTES.get(theme)
        native = window.app_instance._fluxftp_original_palette
        background = p['base'] if p else native.color(QPalette.ColorRole.Base).name()
        foreground = p['text'] if p else readable_text(native.color(QPalette.ColorRole.Text).name(), background)
        assert foreground in window.ftp_command_input.styleSheet()
        assert contrast(foreground, background) >= 4.5
    assert readable_text('#ffffff', '#ffffff') == '#000000'
    assert readable_text('#000000', '#000000') == '#ffffff'
    dialog.close()


def test_theme_persistence_and_system_palette_restore(window):
    app = QApplication.instance()
    dialog = PreferencesDialog(window)
    dialog.theme_combo.setCurrentText('Midnight')
    dialog.close()
    dialog = PreferencesDialog(window)
    assert dialog.theme_combo.currentText() == 'Midnight'
    apply_application(app, window.preferences, window.STYLE)
    assert app.palette().color(QPalette.ColorRole.Base).name() == PALETTES['Midnight']['base']
    dialog.theme_combo.setCurrentText('System')
    assert app.styleSheet() == ''
    assert app.palette() == app._fluxftp_original_palette
    dialog.close()


def test_legacy_dark_migration_and_compact_add_button(window):
    from PySide6.QtWidgets import QToolButton
    window.preferences.setValue('Display/style', 'Dark')
    window.preferences.setValue('Display/header_theme', 'Dark')
    dialog = PreferencesDialog(window)
    assert dialog.theme_combo.currentText() == 'Midnight'
    assert dialog.header_theme_combo.currentText() == 'Midnight'
    assert window.tabs.cornerWidget().text() == '+'
    buttons = [button for button in window.workspace_navigation.findChildren(QToolButton)
               if button.accessibleName() == 'New workspace']
    assert buttons and all(button.text() == '+' for button in buttons)
    count = window.tabs.count()
    window.tabs.cornerWidget().click()
    assert window.tabs.count() == count + 1
    dialog.close()


def test_theme_text_contrast():
    def luminance(value):
        c = QColor(value)
        channels = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4
                    for v in (c.redF(), c.greenF(), c.blueF())]
        return sum(v * weight for v, weight in zip(channels, (.2126, .7152, .0722)))
    for p in PALETTES.values():
        from fluxftp.themes import contrast
        for background in ('base', 'alternate', 'header', 'surface', 'selection'):
            assert contrast(p['text'], p[background]) >= 4.5
        for text in ('text', 'error', 'reply1', 'reply2', 'prompt'):
            a, b = sorted((luminance(p[text]), luminance(p['base'])))
            assert (b + .05) / (a + .05) >= 4.5


def test_reselecting_preset_restores_header_and_color_defaults(window):
    dialog = PreferencesDialog(window)
    dialog.theme_combo.setCurrentText('Black')
    dialog.header_theme_combo.setCurrentText('Light')
    window.preferences.setValue('Layout/custom_active_font', True)
    window.preferences.setValue('Layout/active_color', '#ff0000')
    dialog.theme_combo.textActivated.emit('Black')
    assert dialog.header_theme_combo.currentText() == 'Follow theme'
    assert not window.preferences.value('Layout/custom_active_font', type=bool)
    assert PALETTES['Black']['header'] in window.current_panes()[0].title_label.styleSheet()
    assert all(not control.isEnabled() for control in dialog.color_controls.values())
    dialog.theme_combo.setCurrentText('System')
    combo = dialog.color_themes['remote']
    combo.setCurrentText('Black')
    window.preferences.setValue('Colors/remote/even', '#ffffff')
    combo.textActivated.emit('Black')
    assert color_value(window.preferences, 'remote', 'even') == PALETTES['Black']['base']
    dialog._set_display_color('remote', 'even', '#abcdef')
    assert combo.currentText() == 'Custom'
    combo.setCurrentText('Black')
    assert color_value(window.preferences, 'remote', 'even') == PALETTES['Black']['base']
    dialog.close()
