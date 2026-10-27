from PySide6.QtWidgets import QMessageBox
from fluxftp.app import Window
from fluxftp.toolbar_visibility import NAMES
from test_queue import window


def test_named_hide_restore_and_shared_rows(window, monkeypatch):
    controls = window.toolbar_visibility
    assert [a.text() for a in controls.menu.actions()] == list(NAMES.values())
    assert window.edit_menu.actions()[-1].menu() == controls.edit_menu
    assert controls.edit_menu.actions() == controls.menu.actions()
    window.header_rows.join('tools', 'connection')
    menu = controls.context_menu('tools', window.main_toolbar)
    menu.actions()[0].trigger()
    assert window.header_rows.rows['tools'].isHidden()
    assert not window.header_rows.rows['connection'].isHidden()
    window.header_rows.rebuild()
    window.apply_layout_settings()
    assert window.header_rows.rows['tools'].isHidden()
    controls.edit_menu.actions()[0].trigger()
    assert not window.header_rows.rows['tools'].isHidden()
    for key in ('tools', 'connection'):
        controls.set_visible(key, False)
    band = next(b for g, b in zip(window.header_rows.groups, window.header_rows.bands) if 'tools' in g)
    assert band.isHidden()
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.StandardButton.Yes)
    window.reset_layout()
    assert all(a.isChecked() for a in controls.actions.values())
    assert all(not row.isHidden() for row in window.header_rows.rows.values())


def test_pane_toolbar_visibility_persists_and_applies_to_new_workspaces(window):
    window.toolbar_visibility.set_visible('left', False)
    window.toolbar_visibility.set_visible('connection', False)
    window.add_workspace()
    for index in range(window.tabs.count()):
        split = window.tabs.widget(index)
        assert split.widget(0).file_toolbar.isHidden()
        assert not split.widget(1).file_toolbar.isHidden()
    reopened = Window()
    reopened._smoke_test = True
    try:
        assert reopened.header_rows.rows['connection'].isHidden()
        assert reopened.current_panes()[0].file_toolbar.isHidden()
        reopened.toolbar_visibility.actions['left'].trigger()
        assert not reopened.current_panes()[0].file_toolbar.isHidden()
    finally:
        reopened.close()
