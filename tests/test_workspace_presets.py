import json
from dataclasses import asdict

from PySide6.QtWidgets import QMessageBox

from fluxftp.core import Site
from test_queue import window


def test_workspace_preset_roundtrip_tabs_folders_and_layout(window, tmp_path, monkeypatch):
    first_folder = tmp_path / 'first'
    second_folder = tmp_path / 'second'
    first_folder.mkdir()
    second_folder.mkdir()
    (first_folder / 'one.txt').write_text('one', encoding='utf-8')
    (second_folder / 'two.txt').write_text('two', encoding='utf-8')

    window.tabs.setTabText(0, 'First pair')
    window.tabs.currentWidget().setProperty('auto_workspace_name', False)
    first = window.current_panes()[0]
    first.change_mode('Local')
    first.navigate(str(first_folder))
    window.add_workspace()
    window.tabs.setTabText(1, 'Second pair')
    window.tabs.currentWidget().setProperty('auto_workspace_name', False)
    second = window.current_panes()[1]
    second.change_mode('Local')
    second.navigate(str(second_folder))
    window.tools_commands.pane_order('Local then Site')
    window.tabs.setCurrentIndex(1)
    preset = window.workspace_preset_data('Saved pairs')
    assert 'password' not in json.dumps(preset).casefold()
    assert window.validate_workspace_preset(preset) is preset

    window.tabs.setTabText(0, 'Temporary')
    monkeypatch.setattr(QMessageBox, 'question', lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    assert window.apply_workspace_preset(preset)
    assert [window.tabs.tabText(index) for index in range(window.tabs.count())] == ['First pair', 'Second pair']
    assert window.tabs.currentIndex() == 1
    assert str(first_folder) in [window.tabs.widget(0).widget(side).path for side in range(2)]
    assert str(second_folder) in [window.tabs.widget(1).widget(side).path for side in range(2)]
    assert window.preferences.value('Display/pane_order') == 'Local then Site'


def test_workspace_preset_rejects_site_and_layout_injection(window):
    preset = dict(format=1, name='unsafe', items=[dict(name='Tab', panes=[
        dict(mode='Local', bookmark='', local_path='', remote_path='/'),
        dict(mode='Remote', bookmark='missing', local_path='', remote_path='/')], splitter='')], layout={
            'Network/password': 'secret'})
    try:
        window.validate_workspace_preset(preset)
    except ValueError as error:
        assert 'unsupported layout setting' in str(error)
    else:
        raise AssertionError('Workspace preset cannot inject unrelated preferences.')


def test_workspace_preset_restores_saved_site_reference_without_password(window, monkeypatch):
    profile = asdict(Site(name='Archive', host='archive.example.test', port=22,
        protocol='SFTP · SSH', username='publisher', root='/incoming'))
    window.sites['Archive'] = profile
    pane = window.tabs.currentWidget().widget(0)
    pane.remote_site = window.site_from_profile(profile)
    pane.remote_path = '/incoming/releases'
    pane.remote_password = 'do-not-store-this'
    pane.change_mode('Local')
    preset = window.workspace_preset_data('Remote pair')
    remote = next(item for item in preset['items'][0]['panes'] if item['bookmark'] == 'Archive')
    assert remote['bookmark'] == 'Archive'
    assert remote['remote_path'] == '/incoming/releases'
    assert 'do-not-store-this' not in json.dumps(preset)

    monkeypatch.setattr(QMessageBox, 'question', lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    assert window.apply_workspace_preset(preset)
    restored = next(window.tabs.currentWidget().widget(side) for side in range(2)
        if window.tabs.currentWidget().widget(side).remote_site is not None)
    assert restored.mode == 'Local'
    assert restored.remote_site.host == 'archive.example.test'
    assert restored.remote_path == '/incoming/releases'
    assert restored.remote_password == ''
