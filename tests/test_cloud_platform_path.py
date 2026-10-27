from pathlib import Path
import pytest
from fluxftp import cloud


@pytest.mark.parametrize('system,name', [('win32', 'rclone.exe'), ('darwin', 'rclone'), ('linux', 'rclone')])
def test_packaged_cloud_backend_native_name(monkeypatch, tmp_path, system, name):
    folder = tmp_path / 'cloud_tools'
    folder.mkdir()
    expected = folder / name
    expected.write_bytes(b'fixture')
    monkeypatch.setattr(cloud.sys, 'platform', system)
    monkeypatch.setattr(cloud.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(cloud.sys, '_MEIPASS', str(tmp_path), raising=False)
    assert cloud.executable() == expected
