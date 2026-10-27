from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication
from fluxftp.font_options import display_font, default_font
from fluxftp.preferences import PreferencesDialog
from fluxftp import preferences as module
from test_queue import window


def test_independent_fonts_apply_to_all_workspaces_and_new_panes(window, monkeypatch):
    dialog = PreferencesDialog(window)
    window.add_workspace()
    chosen = QFont('Courier New', 17)
    chosen.setBold(True)
    chosen.setItalic(True)
    monkeypatch.setattr(module.FontPicker, 'getFont', lambda *_: (chosen, True))
    dialog._choose_font('files')
    for i in range(window.tabs.count()):
        for p in (window.tabs.widget(i).widget(0), window.tabs.widget(i).widget(1)):
            assert p.tree.font().pointSize() == 17 and p.tree.font().italic() and p.tree.font().bold()
    assert window.queue.font().pointSize() == 17
    assert window.log.font().pointSize() != 17
    window.add_workspace()
    assert window.current_panes()[0].tree.font().pointSize() == 17
    assert '17 pt' in dialog.font_previews['files'].text()
    assert 'Bold' in dialog.font_previews['files'].text()
    dialog.close()


def test_application_and_logs_override_stylesheet_and_persist(window, monkeypatch):
    dialog = PreferencesDialog(window)
    window.show()
    app_font = QFont('Arial', 15)
    monkeypatch.setattr(module.FontPicker, 'getFont', lambda *_: (app_font, True))
    dialog._choose_font('application')
    QApplication.processEvents()
    assert window.quick_host.font().pointSize() == 15
    log_font = QFont('Courier New', 13)
    monkeypatch.setattr(module.FontPicker, 'getFont', lambda *_: (log_font, True))
    dialog._choose_font('logs')
    for widget in (window.log, window.ftp_log, window.transfer_log, window.tasks_log):
        assert widget.font().pointSize() == 13
    assert window.quick_host.font().pointSize() == 15
    dialog.close()
    dialog = PreferencesDialog(window)
    assert display_font(window.preferences, 'logs').family() == 'Courier New'
    assert '13 pt' in dialog.font_previews['logs'].text()
    dialog.close()


def test_cancel_and_default_reset(window, monkeypatch):
    dialog = PreferencesDialog(window)
    monkeypatch.setattr(module.FontPicker, 'getFont', lambda *_: (QFont('Arial', 18), False))
    dialog._choose_font('application')
    assert not window.preferences.contains('Display/font_application')
    for scope in ('application', 'files', 'logs'):
        window.preferences.setValue('Display/font_' + scope, QFont('Arial', 18).toString())
    window.preferences.setValue('Display/font', QFont('Arial', 19).toString())
    dialog._reset_fonts()
    for scope in ('application', 'files', 'logs'):
        assert not window.preferences.contains('Display/font_' + scope)
        assert display_font(window.preferences, scope) == default_font(scope)
    assert not window.preferences.contains('Display/font')
    assert window.queue.font().family() == default_font('files').family()
    dialog.close()


def test_legacy_font_deserializes_instead_of_becoming_family_name(window):
    font = QFont('Arial', 16)
    font.setUnderline(True)
    window.preferences.setValue('Display/font', font.toString())
    restored = display_font(window.preferences, 'application')
    assert restored.family() == 'Arial' and restored.pointSize() == 16 and restored.underline()
    window.preferences.setValue('Display/font_application', QFont('Courier New', 12).toString())
    assert display_font(window.preferences, 'application').family() == 'Courier New'


def test_invalid_saved_font_uses_default(window):
    window.preferences.setValue('Display/font_logs', '')
    assert display_font(window.preferences, 'logs') == default_font('logs')


def test_typed_size_accept_cancel_and_reopen(window):
    from PySide6.QtCore import QTimer, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QDialogButtonBox
    from fluxftp.font_picker import FontPicker

    options = PreferencesDialog(window)
    def enter_size():
        picker = window.findChild(FontPicker)
        picker.size.setFocus()
        picker.size.selectAll()
        QTest.keyClicks(picker.size, '17.5')
        QTest.mouseClick(picker.buttons.button(QDialogButtonBox.StandardButton.Ok), Qt.MouseButton.LeftButton)
    QTimer.singleShot(30, enter_size)
    options._choose_font('files')
    assert display_font(window.preferences, 'files').pointSizeF() == 17.5
    assert window.queue.font().pointSizeF() == 17.5
    # Close/delete the modal instance retained by its parent before reopening.
    for picker in window.findChildren(FontPicker):
        picker.setParent(None)
        picker.deleteLater()
    def cancel_size():
        picker = window.findChild(FontPicker)
        assert picker.size.value() == 17.5
        picker.size.selectAll()
        QTest.keyClicks(picker.size, '24')
        picker.reject()
    QTimer.singleShot(30, cancel_size)
    options._choose_font('files')
    assert display_font(window.preferences, 'files').pointSizeF() == 17.5
    options.close()

