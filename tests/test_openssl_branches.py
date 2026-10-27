import json
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace

import pytest

from fluxftp import openssl_runtime as runtime, openssl4_updates as updates4


def test_old_per_executable_selection_migrates_once(tmp_path):
    from test_openssl_updates import make_pair
    old = tmp_path / ('a' * 20)
    pair = make_pair(old)
    runtime.write_json(old / 'selected.json', {'directory': pair.name})
    new = tmp_path / 'windows-cp313'
    runtime.migrate_legacy(new)
    assert runtime.selected_directory(new).name == pair.name
    assert runtime.selected_directory(old).exists()
    runtime.choose_branch(new, 3)
    runtime.migrate_legacy(new)
    assert runtime.branch(new) == 3


def test_failed_4_runtime_can_receive_newer_update(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, 'available_directory', lambda *args: (_ for _ in ()).throw(ValueError('rejected')))
    monkeypatch.setattr(runtime, 'validate_pair', lambda *args: {'version': '4.1.0'})
    monkeypatch.setattr(updates4, 'newest_release', lambda: ('4.1.1', 'url', 'a' * 64))
    staged = []
    monkeypatch.setattr(updates4, 'stage', lambda *args: staged.append(args))
    assert '4.1.1' in updates4.check(tmp_path, force=True)
    assert staged and staged[0][1] == '4.1.1'


def test_branch_selection_preserves_both_channels_and_requires_adapter(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, 'adapter_directory', lambda: tmp_path / 'absent')
    assert runtime.branch(tmp_path) == 3
    with pytest.raises(OSError):
        runtime.choose_branch(tmp_path, 4)
    assert runtime.branch(tmp_path) == 3
    monkeypatch.setattr(runtime, 'available_directory', lambda *args: tmp_path)
    monkeypatch.setattr(runtime, 'validate_pair', lambda *args: {})
    runtime.choose_branch(tmp_path, 4)
    assert runtime.branch(tmp_path) == 4
    runtime.choose_branch(tmp_path, 3)
    assert runtime.branch(tmp_path) == 3
    assert runtime.selection_file(tmp_path, 3) != runtime.selection_file(tmp_path, 4)


def test_adapter_hash_cannot_be_replaced(tmp_path):
    adapter = runtime.adapter_directory()
    if not adapter.exists():
        pytest.skip('Windows adapter build needed')
    shutil.copytree(adapter, tmp_path / 'runtime')
    manifest = runtime.read_json(tmp_path / 'runtime' / 'manifest.json')
    manifest['sha256']['_ssl.pyd'] = '0' * 64
    runtime.write_json(tmp_path / 'runtime' / 'manifest.json', manifest)
    with pytest.raises(ValueError, match='adapter checksum'):
        runtime.validate_pair(tmp_path / 'runtime')


@pytest.mark.parametrize('url', ['http://github.com/openssl/installer/releases/download/x/a.msi',
    'https://github.com/other/repo/releases/download/x/a.msi',
    'https://github.com.evil.test/openssl/installer/releases/download/a',
    'https://user@github.com/openssl/installer/releases/download/a'])
def test_official_package_url_allowlist(url):
    assert not updates4.allowed(url)


def test_release_filter_and_checksums(monkeypatch):
    def release(value, **extra):
        return dict(tag_name='publish/' + value + '-1', assets=[dict(
            name=f'OpenSSL-x64-VS-{value}.msi', browser_download_url='url', digest='sha256:' + 'a' * 64)], **extra)
    data = [release('4.1.0'), release('4.2.0', prerelease=True), release('4.0.3'), release('3.6.5')]
    monkeypatch.setattr(updates4, 'fetch', lambda url, output, limit: output.write(json.dumps(data).encode()))
    assert updates4.newest_release()[0] == '4.1.0'


def test_package_checksum_failure_preserves_selection(tmp_path, monkeypatch):
    runtime.write_json(runtime.selection_file(tmp_path, 4), {'directory': 'previous'})
    monkeypatch.setattr(updates4, 'fetch', lambda url, output, limit: output.write(b'bad package'))
    with pytest.raises(ValueError, match='checksum'):
        updates4.stage(tmp_path, '4.1.1', 'url', 'a' * 64)
    assert runtime.read_json(runtime.selection_file(tmp_path, 4))['directory'] == 'previous'


def test_real_official_package_extract_probe_and_select(tmp_path, monkeypatch):
    package = Path(__file__).parents[1] / 'build-tools/ssl4/openssl-4.1.0.msi'
    if sys.platform != 'win32' or not package.exists():
        pytest.skip('Official Windows package not cached')
    monkeypatch.setattr(updates4, 'fetch', lambda url, output, limit: output.write(package.read_bytes()))
    digest = runtime.sha256(package.read_bytes()).hexdigest()
    result = updates4.stage(tmp_path, '4.1.0', 'https://github.com/openssl/installer/releases/download/publish/4.1.0-1/OpenSSL-x64-VS-4.1.0.msi', digest)
    assert runtime.selected_directory(tmp_path, 4) == result
    assert runtime.branch(tmp_path) == 3  # Updates must never switch the chosen major.
    runtime.choose_branch(tmp_path, 4)
    runtime.probe(result)


def test_ssl_options_page_and_switch_persistence(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication, QWidget, QFormLayout, QRadioButton, QLabel
    from PySide6.QtCore import QSettings
    from fluxftp.openssl_update_ui import add_controls
    from fluxftp.preferences import PreferencesDialog
    assert ('SSL', []) in PreferencesDialog.TREE
    application = QApplication.instance() or QApplication([])
    monkeypatch.setattr(runtime, 'root', lambda: tmp_path)
    window = SimpleNamespace(preferences=QSettings(str(tmp_path / 'prefs.ini'), QSettings.IniFormat),
                             runtime_updater=SimpleNamespace(start=lambda **kwargs: None))
    widget = QWidget()
    add_controls(QFormLayout(widget), window)
    widget.findChild(QRadioButton, 'ssl_branch_4').click()
    assert runtime.branch(tmp_path) == 4
    assert '4.x' in widget.findChild(QLabel, 'ssl_pending_version').text()
    import ssl
    assert ssl.OPENSSL_VERSION in widget.findChild(QLabel, 'ssl_active_version').text()
    widget.findChild(QRadioButton, 'ssl_branch_3').click()
    assert runtime.branch(tmp_path) == 3
    widget.close()
