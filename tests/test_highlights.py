import json
import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QLineEdit, QDialogButtonBox
from fluxftp.core import Entry
from fluxftp.preferences import PreferencesDialog
from fluxftp.highlight_rules import highlight_rules, highlight_color
from test_queue import window


def rule(pattern='*.txt', color='#0000ff', regex=False):
    return dict(pattern=pattern, regex=regex, color=color)


def test_first_match_case_regex_and_empty():
    rules = highlight_rules(json.dumps([rule('*.TXT'), rule(r'^readme\.', '#00ff00', True)]))
    assert highlight_color('README.txt', rules) == '#0000ff'
    assert highlight_color('README.md', rules) == '#00ff00'
    assert highlight_color('other.bin', rules) is None
    assert highlight_rules('[]') == ()


@pytest.mark.parametrize('value', [None, {}, [rule(color='invalid')], [rule('[', regex=True)], [dict(pattern='x', regex='yes', color='red')]])
def test_invalid_saved_rules_rejected(value):
    with pytest.raises(ValueError): highlight_rules(json.dumps(value))


def test_controls_editor_order_and_empty_persist(window, monkeypatch):
    dialog = PreferencesDialog(window)
    assert not dialog.highlights_disabled.isChecked() and dialog.highlight_list.count() == 0
    assert not any(b.isEnabled() for b in (dialog.highlight_edit, dialog.highlight_delete, dialog.highlight_up, dialog.highlight_down))
    monkeypatch.setattr(dialog, '_ascii_rule_dialog', lambda *_: rule())
    dialog._edit_highlight_rule(False)
    monkeypatch.setattr(dialog, '_ascii_rule_dialog', lambda *_: rule('^README$', '#00ff00', True))
    dialog._edit_highlight_rule(False)
    assert not dialog.highlight_list.item(1).icon().isNull()
    assert dialog.highlight_list.item(1).foreground().color().name() == '#00ff00'
    assert dialog.highlight_up.isEnabled() and not dialog.highlight_down.isEnabled()
    dialog._move_highlight_rule(-1)
    assert dialog.highlight_list.item(0).text() == '^README$'
    assert not dialog.highlight_up.isEnabled() and dialog.highlight_down.isEnabled()
    monkeypatch.setattr(dialog, '_ascii_rule_dialog', lambda *_: None)
    old = window.preferences.value('Display/highlight_rules')
    dialog._edit_highlight_rule(True)
    assert window.preferences.value('Display/highlight_rules') == old
    while dialog.highlight_list.count():
        dialog.highlight_list.setCurrentRow(0)
        dialog._delete_highlight_rule()
    dialog.close()
    dialog = PreferencesDialog(window)
    assert dialog.highlight_list.count() == 0
    dialog.close()


def test_real_mask_and_color_editor(window):
    dialog = PreferencesDialog(window)
    def fill():
        editor = QApplication.activeModalWidget()
        edits = editor.findChildren(QLineEdit)
        edits[0].setText('*.nfo')
        edits[1].setText('#123456')
        editor.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Ok).click()
    QTimer.singleShot(30, fill)
    result = dialog._ascii_rule_dialog(kind='Highlights')
    assert result == rule('*.nfo', '#123456')
    dialog.close()


def test_colors_across_workspaces_disable_and_skip_precedence(window):
    window.preferences.setValue('Transfer/skip_enabled', False)
    window.preferences.setValue('Display/highlight_rules', json.dumps([rule('*', '#123456')]))
    panes = window.current_panes()
    window.add_workspace()
    panes += window.current_panes()
    for pane in panes: pane.show_entries(([Entry('folder', True), Entry('readme.txt', False, 4)], 'test'))
    for pane in panes:
        assert pane.tree.topLevelItem(0).foreground(0).color().name() == '#123456'
        assert pane.tree.topLevelItem(1).foreground(0).color().name() == '#123456'
    dialog = PreferencesDialog(window)
    dialog.highlights_disabled.setChecked(True)
    assert all(p.tree.topLevelItem(1).foreground(0).style() == Qt.BrushStyle.NoBrush for p in panes)
    dialog.highlights_disabled.setChecked(False)
    window.preferences.setValue('Transfer/skip_enabled', True)
    window.preferences.setValue('Transfer/skip_rules', json.dumps([dict(pattern='*.txt', regex=False)]))
    window.refresh_skip_colors()
    assert panes[0].tree.topLevelItem(1).foreground(0).color().name() == '#ff0000'
    assert panes[0].tree.topLevelItem(0).foreground(0).color().name() == '#123456'
    dialog.close()


def test_corrupt_settings_do_not_break_listing_or_dialog(window):
    window.preferences.setValue('Display/highlight_rules', '{bad')
    window.current_panes()[0].show_entries(([Entry('file.txt', False, 2)], 'test'))
    dialog = PreferencesDialog(window)
    assert dialog.highlight_list.count() == 0
    assert window.preferences.value('Display/highlight_rules') == '{bad'
    dialog.close()
