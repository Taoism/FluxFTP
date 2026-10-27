import json
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

from fluxftp import openssl_runtime as runtime, openssl_updates as updates

COMMIT = '763bc5ab30fe645cf70e3bacb0c8c04908a46620'


def fake_dll():
    data = bytearray(128)
    data[:2] = b'MZ'
    data[60:64] = (64).to_bytes(4, 'little')
    data[64:70] = b'PE\0\0\x64\x86'
    return bytes(data)


def make_pair(location, release='3.5.9', real=False):
    directory = location / (release + '-' + COMMIT)
    directory.mkdir(parents=True)
    hashes = {}
    for name in runtime.DLLS:
        if real:
            source = Path(__file__).parents[1] / 'runtime/python-3.13.15/DLLs' / name
            if not source.is_file():
                pytest.skip('Maintained Windows TLS runtime fixture is not included in the public source checkout')
            shutil.copyfile(source, directory / name)
        else:
            (directory / name).write_bytes(fake_dll())
        hashes[name] = runtime.sha256((directory / name).read_bytes()).hexdigest()
    runtime.write_json(directory / 'manifest.json', {
        'version': release, 'commit': COMMIT, 'sha256': hashes})
    return directory


@pytest.mark.parametrize('bad', ['2.0.0', '5.0.0', '3.5.9-beta1', '3.5.09', '../3.5.9'])
def test_version_rejects_other_branches_prereleases_and_paths(bad):
    with pytest.raises(ValueError):
        runtime.version(bad)


def test_numeric_stable_release_selection(monkeypatch):
    refs = [{'ref': 'refs/tags/openssl-bin-' + value,
             'object': {'type': 'commit', 'sha': COMMIT}}
            for value in ['3.5.9', '3.5.10', '3.5.11-beta1', '3.6.0']]
    monkeypatch.setattr(updates, 'api', lambda _: refs)
    assert updates.newest_release() == ('3.6.0', COMMIT)


def test_annotated_tag_is_resolved_and_invalid_commit_rejected(monkeypatch):
    monkeypatch.setattr(updates, 'api', lambda _: {'object': {'type': 'commit', 'sha': COMMIT}})
    assert updates.commit_for({'type': 'tag', 'sha': 'a' * 40}) == COMMIT
    with pytest.raises(ValueError):
        updates.commit_for({'type': 'commit', 'sha': '../other'})


@pytest.mark.parametrize('url', ['http://api.github.com/repos/python/cpython-bin-deps/tags',
    'https://raw.githubusercontent.com/other/repo/library.dll',
    'https://api.github.com.evil.test/repos/python/cpython-bin-deps/tags'])
def test_download_source_allowlist(url):
    with pytest.raises(ValueError):
        updates.fetch(url)


def mock_download(monkeypatch, corrupt=False):
    data = fake_dll()
    blob = updates.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
    tree = {'tree': [{'path': 'amd64/' + name, 'type': 'blob', 'mode': '100644',
                     'sha': blob, 'size': len(data)} for name in runtime.DLLS]}
    monkeypatch.setattr(updates, 'api', lambda _: tree)
    monkeypatch.setattr(updates, 'fetch', lambda *args: data + b'bad' if corrupt else data)
    monkeypatch.setattr(updates, 'probe', lambda directory: None)


def test_corrupt_download_does_not_change_selection(tmp_path, monkeypatch):
    mock_download(monkeypatch, corrupt=True)
    runtime.write_json(tmp_path / 'selected.json', {'directory': 'previous'})
    with pytest.raises(ValueError, match='blob verification'):
        updates.stage(tmp_path, '3.5.9', COMMIT)
    assert runtime.read_json(tmp_path / 'selected.json')['directory'] == 'previous'
    assert not list(tmp_path.glob('download-*'))


def test_successful_pair_is_published_only_after_probe(tmp_path, monkeypatch):
    mock_download(monkeypatch)
    checked = []
    def probe(directory):
        assert not (tmp_path / 'selected.json').exists()
        checked.append(runtime.validate_pair(directory))
    monkeypatch.setattr(updates, 'probe', probe)
    result = updates.stage(tmp_path, '3.5.9', COMMIT)
    assert checked[0]['commit'] == COMMIT
    assert runtime.selected_directory(tmp_path) == result


def test_probe_failure_preserves_previous_runtime(tmp_path, monkeypatch):
    mock_download(monkeypatch)
    previous = make_pair(tmp_path, '3.5.8')
    runtime.write_json(tmp_path / 'selected.json', {'directory': previous.name})
    def fail(_):
        raise RuntimeError('probe failed')
    monkeypatch.setattr(updates, 'probe', fail)
    with pytest.raises(RuntimeError):
        updates.stage(tmp_path, '3.5.9', COMMIT)
    assert runtime.selected_directory(tmp_path) == previous
    assert not (tmp_path / ('3.5.9-' + COMMIT)).exists()


def test_tampering_wrong_architecture_and_path_traversal(tmp_path):
    directory = make_pair(tmp_path)
    (directory / runtime.DLLS[0]).write_bytes(b'corrupt')
    with pytest.raises(ValueError, match='checksum'):
        runtime.validate_pair(directory)
    runtime.write_json(tmp_path / 'selected.json', {'directory': '../outside'})
    with pytest.raises(ValueError, match='directory'):
        runtime.selected_directory(tmp_path)
    manifest = runtime.read_json(directory / 'manifest.json')
    data = fake_dll().replace(b'\x64\x86', b'\x4c\x01')
    (directory / runtime.DLLS[0]).write_bytes(data)
    manifest['sha256'][runtime.DLLS[0]] = runtime.sha256(data).hexdigest()
    runtime.write_json(directory / 'manifest.json', manifest)
    with pytest.raises(ValueError, match='AMD64'):
        runtime.validate_pair(directory)


def test_daily_checks_and_no_downgrade(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(updates, 'newest_release', lambda: calls.append(True) or ('3.5.8', COMMIT))
    assert 'up to date' in updates.check(tmp_path, current=(3, 5, 9))
    assert updates.check(tmp_path) is None
    assert len(calls) == 1
    updates.check(tmp_path, force=True)
    assert len(calls) == 2
    assert not (tmp_path / 'selected.json').exists()


def test_pending_update_is_not_downgraded(tmp_path, monkeypatch):
    directory = make_pair(tmp_path, '3.5.10')
    runtime.write_json(tmp_path / 'selected.json', {'directory': directory.name})
    monkeypatch.setattr(updates, 'newest_release', lambda: ('3.5.9', COMMIT))
    assert 'next launch' in updates.check(tmp_path, current=(3, 5, 8))
    assert runtime.selected_directory(tmp_path) == directory


def test_rejected_runtime_cannot_be_reactivated(tmp_path, monkeypatch):
    directory = make_pair(tmp_path)
    runtime.write_json(directory / 'rejected.json', {'reason': 'startup failure'})
    with pytest.raises(ValueError, match='previously failed'):
        updates.stage(tmp_path, '3.5.9', COMMIT)


def prepare_bootstrap(tmp_path, monkeypatch):
    directory = make_pair(tmp_path)
    runtime.write_json(tmp_path / 'selected.json', {'directory': directory.name})
    monkeypatch.setattr(runtime, 'root', lambda: tmp_path)
    monkeypatch.setattr(runtime, 'probe', lambda _: None)
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'argv', ['FluxFTP.exe'])
    return directory


def test_failed_updated_desktop_falls_back_without_loading_bad_tls(tmp_path, monkeypatch):
    directory = prepare_bootstrap(tmp_path, monkeypatch)
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: SimpleNamespace(poll=lambda: 1))
    runtime.bootstrap()
    assert (directory / 'rejected.json').exists()
    assert 'bundled libraries' in runtime.read_json(tmp_path / 'status.json')['message']


def test_successful_desktop_handoff_does_not_launch_second_window(tmp_path, monkeypatch):
    directory = prepare_bootstrap(tmp_path, monkeypatch)
    def child(args, **kwargs):
        runtime.write_json(Path(args[3]), {'ready': True})
        return SimpleNamespace(poll=lambda: None)
    monkeypatch.setattr(subprocess, 'Popen', child)
    with pytest.raises(SystemExit) as result:
        runtime.bootstrap()
    assert result.value.code == 0
    assert not (directory / 'rejected.json').exists()
    assert not list(tmp_path.glob('ready-*'))


def test_real_windows_pair_loads_and_negotiates_verified_tls(tmp_path):
    if sys.platform != 'win32':
        pytest.skip('Windows runtime')
    directory = make_pair(tmp_path, '3.5.8', real=True)
    runtime.probe(directory)
    # A claimed version that does not match the actual DLLs must be rejected.
    manifest = runtime.read_json(directory / 'manifest.json')
    manifest['version'] = '3.5.9'
    runtime.write_json(directory / 'manifest.json', manifest)
    with pytest.raises(RuntimeError, match='runtime check failed'):
        runtime.probe(directory)


def test_ui_controls_persist_and_manual_check_is_explicit(tmp_path):
    from PySide6.QtWidgets import QApplication, QWidget, QFormLayout, QCheckBox, QPushButton
    from PySide6.QtCore import QSettings
    from fluxftp.openssl_update_ui import add_controls
    application = QApplication.instance() or QApplication([])
    calls = []
    settings = QSettings(str(tmp_path / 'updater.ini'), QSettings.IniFormat)
    window = SimpleNamespace(preferences=settings, runtime_updater=SimpleNamespace(
        start=lambda **kwargs: calls.append(kwargs)))
    widget = QWidget()
    form = QFormLayout(widget)
    add_controls(form, window)
    checkbox = widget.findChild(QCheckBox)
    assert checkbox.isChecked()
    checkbox.setChecked(False)
    assert settings.value('Updates/openssl_enabled', type=bool) is False
    widget.findChild(QPushButton).click()
    assert calls == [{'force': True}]
    widget.close()


def test_ready_replaces_stale_pending_status(tmp_path, monkeypatch):
    runtime.write_json(tmp_path / 'status.json', {'message': 'Ready on next launch'})
    monkeypatch.setattr(runtime, '_ready_file', tmp_path / 'ready.json')
    runtime.ready()
    assert 'is active' in runtime.read_json(tmp_path / 'status.json')['message']
    assert runtime.read_json(tmp_path / 'ready.json')['ready']
