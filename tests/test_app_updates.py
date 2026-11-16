import hashlib
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from fluxftp import app_updates


def offer():
    return app_updates.UpdateOffer(
        '0.146.0', 'FluxFTP-0.146.0-windows-x64.zip',
        'https://github.com/Taoism/FluxFTP/releases/download/v0.146.0/asset.zip',
        'SHA256SUMS.txt', 'https://github.com/Taoism/FluxFTP/releases/download/v0.146.0/SHA256SUMS.txt',
        'https://github.com/Taoism/FluxFTP/releases/tag/v0.146.0', 'Release notes')


def test_downloaded_archive_digest_is_rechecked_in_private_windows_staging(tmp_path, monkeypatch):
    import ctypes

    update_offer = offer()
    payload = b'verified package bytes'
    digest = hashlib.sha256(payload).hexdigest()
    monkeypatch.setattr(app_updates.tempfile, 'gettempdir', lambda: str(tmp_path))

    def download(url, destination, _limit):
        content = (f'{digest}  {update_offer.asset_name}\n'.encode('ascii')
                   if url == update_offer.checksum_url else payload)
        destination.write_bytes(content)
        return hashlib.sha256(content).hexdigest()

    monkeypatch.setattr(app_updates, '_download', download)
    downloaded = app_updates.download_update(update_offer)
    assert downloaded.path.read_bytes() == payload and downloaded.sha256 == digest

    private = tmp_path / 'private-updater'
    monkeypatch.setattr(app_updates.tempfile, 'mkdtemp', lambda **_: (private.mkdir(), str(private))[1])
    protected = []
    monkeypatch.setattr(app_updates, '_secure_windows_update_directory', protected.append)
    shell32 = SimpleNamespace(ShellExecuteW=lambda *_args: 33)
    monkeypatch.setattr(ctypes, 'windll', SimpleNamespace(shell32=shell32), raising=False)
    target = tmp_path / 'Program Files' / 'FluxFTP'
    app_updates.start_windows_installer(downloaded.path, update_offer, target, 123, downloaded.sha256)

    assert protected == [private]
    config_path = private / 'update.json'
    config = json.loads(config_path.read_text(encoding='utf-8'))
    assert Path(config['archive']).parent == private
    assert config['sha256'] == digest and config['version'] == '0.146.0'
    script = (private / 'install.ps1').read_text(encoding='utf-8')
    assert 'Get-FileHash' in script and '$PSScriptRoot' in script
    assert '$env:TEMP' not in script

    downloaded.path.write_bytes(b'tampered')
    with pytest.raises(ValueError, match='changed after checksum verification'):
        app_updates.start_windows_installer(downloaded.path, update_offer, target, 123, downloaded.sha256)


def test_windows_updater_directory_acl_blocks_inheritance_and_low_integrity_writes(tmp_path, monkeypatch):
    windows = tmp_path / 'Windows'
    system32 = windows / 'System32'
    system32.mkdir(parents=True)
    private = tmp_path / 'private-update'
    private.mkdir()
    monkeypatch.setenv('WINDIR', str(windows))
    calls = []

    def run(command, **_kwargs):
        if Path(command[0]).name == 'whoami.exe':
            return SimpleNamespace(stdout='"user","S-1-5-21-1-2-3-1000"\n')
        calls.append(command)
        return SimpleNamespace(stdout='')

    monkeypatch.setattr(subprocess, 'run', run)
    app_updates._secure_windows_update_directory(private)
    assert len(calls) == 2
    assert '/inheritance:r' in calls[0] and '*S-1-5-21-1-2-3-1000:(OI)(CI)F' in calls[0]
    assert '*S-1-5-18:(OI)(CI)F' in calls[0] and '*S-1-5-32-544:(OI)(CI)F' in calls[0]
    assert calls[1][-2:] == ['/setintegritylevel', '(OI)(CI)M']


def test_available_release_downloads_automatically_without_prompt(monkeypatch):
    from fluxftp.app_updates_ui import ApplicationUpdates

    update_offer = offer()
    updater = ApplicationUpdates.__new__(ApplicationUpdates)
    calls = []
    updater._download = lambda item: calls.append(item)
    updater._offer(update_offer)
    assert calls == [update_offer]


def test_verified_download_is_held_until_application_exit(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from fluxftp.app_updates_ui import ApplicationUpdates

    messages, starts = [], []
    window = SimpleNamespace(
        statusBar=lambda: SimpleNamespace(showMessage=lambda *args: messages.append(args)),
        log=SimpleNamespace(appendPlainText=messages.append),
    )
    updater = ApplicationUpdates.__new__(ApplicationUpdates)
    updater.window = window
    updater.install_root = tmp_path / 'FluxFTP'
    updater.pending_update = None
    archive = tmp_path / 'update.zip'
    downloaded = SimpleNamespace(path=archive, sha256='a' * 64)
    update_offer = offer()
    updater._downloaded(downloaded, update_offer)
    assert updater.pending_update == (downloaded, update_offer)
    monkeypatch.setattr(updater, 'window_thread_platform', lambda: 'win32')
    monkeypatch.setattr(app_updates, 'start_windows_installer',
                        lambda *args: starts.append(args))
    assert updater.install_pending() is True
    assert starts and starts[0][0] == archive and starts[0][1] == update_offer
    assert updater.pending_update is None
    assert any('install when you exit' in item for item in messages if isinstance(item, str))
