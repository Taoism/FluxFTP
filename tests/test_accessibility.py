from PySide6.QtCore import Qt
from PySide6.QtGui import QAccessible, QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (QApplication, QWidget, QLineEdit, QComboBox, QAbstractSpinBox,
    QAbstractItemView, QAbstractButton, QPlainTextEdit, QTextEdit, QScrollArea)

from fluxftp.app import SiteDialog
from fluxftp.preferences import PreferencesDialog
from fluxftp.site_manager import SitesDialog
from fluxftp.themes import PALETTES, contrast, stylesheet
from test_queue import window


def unnamed_controls(root):
    types = (QLineEdit, QComboBox, QAbstractSpinBox, QAbstractItemView,
             QAbstractButton, QPlainTextEdit, QTextEdit)
    missing = []
    for control in root.findChildren(QWidget):
        if not isinstance(control, types) or not control.isVisibleTo(root) or not control.isEnabled() or not control.focusPolicy():
            continue
        interface = QAccessible.queryAccessibleInterface(control)
        if interface and not interface.text(QAccessible.Text.Name):
            missing.append((type(control).__name__, control.objectName()))
    return missing


def test_visible_controls_have_screen_reader_names(window):
    QAccessible.setActive(True)
    window.show()
    QApplication.processEvents()
    assert not unnamed_controls(window)
    from fluxftp.display_options import AddressBar
    for address in window.findChildren(AddressBar):
        buttons = address.findChildren(QAbstractButton)
        assert buttons
        assert all(QAccessible.queryAccessibleInterface(button).text(QAccessible.Text.Name)
                   == 'Drives, recent folders and bookmarks' for button in buttons)
    for factory, mapping in ((PreferencesDialog, 'page_by_name'),
                             (lambda w: SiteDialog(w, {}), '_page_indices'), (SitesDialog, None)):
        dialog = factory(window)
        dialog.show()
        for name, index in (getattr(dialog, mapping).items() if mapping else [('sites', 0)]):
            if mapping:
                dialog.pages.setCurrentIndex(index)
            QApplication.processEvents()
            assert not unnamed_controls(dialog), (name, unnamed_controls(dialog))
        dialog.close()


def test_site_fields_follow_visual_keyboard_order_and_password_is_protected(window):
    dialog = SiteDialog(window, {})
    dialog.show()
    dialog.activateWindow()
    QTest.qWait(30)
    dialog.protocol.setFocus()
    for expected in (dialog.fields['host'], dialog.port, dialog.fields['name'], dialog.fields['username'], dialog.password):
        QTest.keyClick(QApplication.focusWidget(), Qt.Key.Key_Tab)
        assert QApplication.focusWidget() is expected
    QTest.keyClick(dialog.password, Qt.Key.Key_Backtab)
    assert QApplication.focusWidget() is dialog.fields['username']
    interface = QAccessible.queryAccessibleInterface(dialog.password)
    assert interface.state().passwordEdit
    dialog.close()


def test_logs_do_not_trap_tab_and_workspace_height_has_keyboard_control(window):
    window.show()
    window.activateWindow()
    QTest.qWait(30)
    window.view_commands.show_panel('system')
    window.log.setFocus()
    QTest.keyClick(window.log, Qt.Key.Key_Tab)
    assert QApplication.focusWidget() is not window.log
    row = window.header_rows.rows['overview']
    row.set_height(180)
    row.height_grip.setFocus()
    QTest.keyClick(row.height_grip, Qt.Key.Key_Down)
    assert row.height() == 196
    QTest.keyClick(row.height_grip, Qt.Key.Key_Up)
    assert row.height() == 180
    QTest.keyClick(row.height_grip, Qt.Key.Key_Home)
    assert row.height() < 180
    window.layout_lock.action.setChecked(True)
    assert not row.height_grip.isEnabled()


def test_options_pages_scroll_at_large_fonts_and_close_stays_visible(window):
    dialog = PreferencesDialog(window)
    font = QFont(dialog.font())
    font.setPointSize(18)
    dialog.setFont(font)
    dialog.resize(850, 500)
    dialog.show()
    for name, index in dialog.page_by_name.items():
        dialog.pages.setCurrentIndex(index)
        QApplication.processEvents()
        page = dialog.pages.currentWidget()
        assert isinstance(page, QScrollArea), name
        assert page.widgetResizable()
        assert dialog.rect().contains(dialog.close_button.mapTo(dialog, dialog.close_button.rect().center()))
    dialog.grab().save('logs/accessibility-large-font-options.png')
    dialog.close()


def test_builtin_theme_text_contrast_and_focus_styles(window):
    for name, palette in PALETTES.items():
        for key in ('text', 'muted'):
            assert contrast(palette[key], palette['surface']) >= 4.5, (name, key)
        window.preferences.setValue('Display/style', name)
        css = stylesheet(window.preferences, '')
        assert 'QPushButton:focus' in css and 'QComboBox:focus' in css
