import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QMessageBox

from fluxftp.file_associations import validate_rule, matching_rule, read_rules, AssociationEditor
from fluxftp.association_actions import open_associated_file, launch_application, ExternalFileSession
from fluxftp.preferences import PreferencesDialog
from fluxftp.core import Entry
from test_queue import window
from test_integration import servers
from test_ui import settle


def rule(masks='*.txt', program=None):
    return dict(masks=masks, program=program or sys.executable)


def set_rules(window, kind, rules):
    window.preferences.setValue('Associations/' + kind, json.dumps(rules))


def test_order_masks_semicolons_and_independent_lists(window):
    set_rules(window, 'view', [rule('*.txt; *.NFO'), rule('*.*', str(Path(sys.executable).with_name('other.exe')))])
    set_rules(window, 'edit', [rule('README')])
    assert matching_rule(window.preferences, 'view', 'INFO.nfo')['program'] == sys.executable
    assert matching_rule(window.preferences, 'view', 'README')['masks'] == '*.*'
    assert matching_rule(window.preferences, 'edit', 'readme')['masks'] == 'README'
    assert matching_rule(window.preferences, 'edit', 'file.txt') is None


@pytest.mark.parametrize('value', [rule(' ; '), rule('folder/*.txt'), rule('*.txt', 'relative.exe'),
                                  {'masks': '*.txt'}, rule('bad\nmask')])
def test_invalid_rules(value):
    with pytest.raises(ValueError):
        validate_rule(value)


def test_empty_lists_crud_order_persistence_and_buttons(window, monkeypatch):
    dialog = PreferencesDialog(window)
    view, edit = (dialog.association_lists[kind] for kind in ('view', 'edit'))
    assert not view.rules and not edit.rules
    assert view.buttons['Add'].isEnabled()
    assert all(not view.buttons[label].isEnabled() for label in ('Edit', 'Delete', 'Move Up', 'Move Down'))
    values = iter([rule('*.txt;*.nfo'), rule('*.*'), rule('*.log')])
    def accept(editor):
        editor.rule = next(values)
        return True
    monkeypatch.setattr(AssociationEditor, 'exec', accept)
    view.edit(False)
    view.edit(False)
    assert view.buttons['Move Up'].isEnabled() and not view.buttons['Move Down'].isEnabled()
    view.move(-1)
    assert view.rules[0]['masks'] == '*.*'
    view.edit(True)
    assert view.rules[0]['masks'] == '*.log'
    view.delete()
    assert read_rules(window.preferences, 'view') == [rule('*.txt;*.nfo')]
    assert read_rules(window.preferences, 'edit') == []
    dialog.close()
    again = PreferencesDialog(window)
    assert again.association_lists['view'].rules == [rule('*.txt;*.nfo')]
    again.close()


def test_invalid_saved_data_is_retained(window):
    window.preferences.setValue('Associations/view', '{bad json')
    dialog = PreferencesDialog(window)
    control = dialog.association_lists['view']
    assert control.invalid and not control.buttons['Add'].isEnabled()
    assert 'retained' in control.notice.text()
    assert window.preferences.value('Associations/view') == '{bad json'
    dialog.close()


def test_editor_validates_application_and_keeps_cancelled_data(window, tmp_path, monkeypatch):
    errors = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: errors.append(args[-1]))
    editor = AssociationEditor(window)
    editor.program.setText(str(tmp_path / 'missing.exe'))
    editor.validate()
    assert errors and not editor.result()
    editor.program.setText(sys.executable)
    editor.masks.setText('*.txt; *.nfo')
    editor.validate()
    assert editor.result() and editor.rule == rule('*.txt;*.nfo')


def test_launch_passes_filename_as_single_argument_without_shell(tmp_path):
    script = tmp_path / 'file & space.py'
    output = tmp_path / 'result.txt'
    script.write_text('from pathlib import Path\nPath(__file__).with_name("result.txt").write_text("ok")\n')
    process = launch_application(sys.executable, script)
    assert process.wait(timeout=15) == 0
    assert output.read_text() == 'ok'


def test_local_dispatch_and_unmatched_remote_fallback(window, tmp_path, monkeypatch):
    from fluxftp import association_actions, remote_editor
    pane = window.current_panes()[0]
    pane.local_path = str(tmp_path)
    pane.change_mode('Local')
    file = tmp_path / 'file.txt'
    file.write_text('hello')
    set_rules(window, 'edit', [rule()])
    launched = []
    monkeypatch.setattr(association_actions, 'launch_application', lambda *args: launched.append(args) or SimpleNamespace(poll=lambda: 0))
    window.mouse_file(pane, Entry(file.name, False, 5), 'Edit File')
    assert launched == [(sys.executable, str(file))]
    menu = window.default_file_menu(pane)
    # Unmatched remote actions preserve the existing built-in behavior.
    pane.mode = 'Remote'
    fallbacks = []
    monkeypatch.setattr(remote_editor, 'open_remote_text', lambda *args, **kwargs: fallbacks.append(kwargs))
    window.mouse_file(pane, Entry('other.bin', False, 4), 'View File')
    assert fallbacks == [{'editable': False}]


@pytest.mark.parametrize('tls', [False, True])
def test_remote_binary_edit_upload_conflict_and_cleanup(window, servers, monkeypatch, tls):
    from fluxftp import association_actions
    root, site = servers(tls)
    original = b'\x00\xffbinary\r\n'
    remote = root / 'data.bin'
    remote.write_bytes(original)
    pane = window.current_panes()[0]
    pane.set_remote(site, 'password', '/')
    set_rules(window, 'edit', [rule('*.bin')])
    launched = []
    monkeypatch.setattr(association_actions, 'launch_application', lambda *args: launched.append(args) or SimpleNamespace(poll=lambda: 0))
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **kw: QMessageBox.StandardButton.Yes)
    assert open_associated_file(window, pane, Entry('data.bin', False, len(original)), True)
    settle(window)
    session = window.mouse_windows[-1]
    assert isinstance(session, ExternalFileSession) and session.local.read_bytes() == original
    assert Path(launched[0][1]) == session.local
    session.local.write_bytes(b'new\x00bytes')
    session.upload_changes()
    settle(window)
    assert remote.read_bytes() == b'new\x00bytes'
    # Do not upload automatically on application exit or local save.
    session.local.write_bytes(b'not yet uploaded')
    assert remote.read_bytes() == b'new\x00bytes'
    remote.write_bytes(b'other writer')
    session.upload_changes()
    settle(window)
    assert remote.read_bytes() == b'other writer'
    assert 'Upload refused' in session.status.text()
    assert session.local.read_bytes() == b'not yet uploaded'
    path = session.local
    session.close()
    assert not path.exists()


def test_view_sessions_are_distinct_never_upload_and_close_can_cancel(window, servers, monkeypatch):
    from fluxftp import association_actions
    root, site = servers()
    (root / 'one.dat').write_bytes(b'\xff\x00')
    pane = window.current_panes()[0]
    pane.set_remote(site, 'password', '/')
    set_rules(window, 'view', [rule('*.*')])
    monkeypatch.setattr(association_actions, 'launch_application', lambda *args: SimpleNamespace(poll=lambda: 0))
    for _ in range(2):
        open_associated_file(window, pane, Entry('one.dat', False, 2), False)
        settle(window)
    first, second = window.mouse_windows[-2:]
    assert first.local != second.local
    first.local.write_bytes(b'view changed')
    first.upload_changes()
    assert not first.saving and (root / 'one.dat').read_bytes() == b'\xff\x00'
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **kw: QMessageBox.StandardButton.Cancel)
    assert not first.close() and first.local.exists()
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **kw: QMessageBox.StandardButton.Yes)
    first.close()
    second.close()


def test_failed_download_does_not_open_application(window, servers, monkeypatch):
    from fluxftp import association_actions
    _, site = servers()
    pane = window.current_panes()[0]
    pane.set_remote(site, 'password', '/')
    set_rules(window, 'view', [rule('*.*')])
    errors, launches = [], []
    monkeypatch.setattr(window, 'error', errors.append)
    monkeypatch.setattr(association_actions, 'launch_application', lambda *args: launches.append(args))
    open_associated_file(window, pane, Entry('missing.dat', False, 3), False)
    settle(window)
    assert errors and not launches and not pane.busy
