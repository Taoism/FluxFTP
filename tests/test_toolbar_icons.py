from PySide6.QtWidgets import QToolButton
from PySide6.QtCore import Qt
from PySide6.QtGui import QAccessible
from fluxftp.toolbar_icons import apply_toolbar_icons
from test_queue import window


def test_toolbar_descriptions_themes_and_direction(window):
    for theme in ('Light', 'Midnight', 'System'):
        window.preferences.setValue('Display/style', theme)
        window.apply_colors()
        apply_toolbar_icons(window)
        for pane in window.current_panes():
            for button in pane.file_toolbar.findChildren(QToolButton):
                assert button.focusPolicy() == Qt.FocusPolicy.StrongFocus
                assert QAccessible.queryAccessibleInterface(button).text(QAccessible.Text.Name)
                if button.objectName() == 'qt_toolbar_ext_button':
                    continue
                assert button.toolTip()
                if hasattr(button, '_toolbar_description'):
                    assert not button.icon().isNull()
                    assert button.accessibleName()
        left, right = window.current_panes()
        def transfer(pane):
            return next(a for a in pane.file_toolbar.actions()
                        if a.toolTip().startswith('Transfer selection'))
        assert transfer(left).icon().cacheKey() != transfer(right).icon().cacheKey()
    window.add_workspace()
    assert not window.current_panes()[0].bookmark_button.icon().isNull()
    window.set_toolbar_icon_size(16)
    assert window.toolbar_icon_size() == 16


def test_remote_local_icon_tracks_mode(window, monkeypatch):
    pane = window.current_panes()[0]
    monkeypatch.setattr(pane, 'refresh', lambda: None)
    pane.busy = False
    pane.change_mode('Local')
    apply_toolbar_icons(window)
    local = pane.mode_button.icon().cacheKey()
    assert not pane.mode_button.icon().isNull()
    assert 'Switch to Remote' in pane.mode_button.toolTip()
    pane.toggle_mode()
    assert pane.mode == 'Remote'
    assert pane.mode_button.icon().cacheKey() != local
    assert 'Switch to Local' in pane.mode_button.toolTip()
    pane.toggle_mode()
    assert pane.mode_button.icon().cacheKey() == local
