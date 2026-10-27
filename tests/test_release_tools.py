import pytest
import release_tools


def test_windows_signs_then_verifies(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(release_tools.sys, 'platform', 'win32')
    monkeypatch.setattr(release_tools, 'run', lambda *args: calls.append(args))
    release_tools.sign_windows(tmp_path / 'app.exe', 'a' * 40, 'https://timestamp.example.com')
    assert calls[0][:2] == ('signtool', 'sign')
    assert '/tr' in calls[0] and '/td' in calls[0]
    assert calls[1][:4] == ('signtool', 'verify', '/pa', '/all')


def test_failed_signing_never_claims_verification(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(release_tools.sys, 'platform', 'win32')
    def fail(*args):
        calls.append(args)
        raise RuntimeError('signing failed')
    monkeypatch.setattr(release_tools, 'run', fail)
    with pytest.raises(RuntimeError):
        release_tools.sign_windows(tmp_path / 'app.exe', 'a' * 40, 'https://timestamp.example.com')
    assert len(calls) == 1


def test_native_backend_digest_must_match(monkeypatch, tmp_path):
    monkeypatch.setattr(release_tools.sys, 'platform', 'linux')
    backend = tmp_path / 'rclone'
    backend.write_bytes(b'wrong binary')
    with pytest.raises(ValueError, match='checksum'):
        release_tools.package_native(backend, '0' * 64)


def test_linux_signature_verified(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(release_tools.sys, 'platform', 'linux')
    monkeypatch.setattr(release_tools, 'run', lambda *args: calls.append(args))
    release_tools.sign_linux(tmp_path / 'app.tar.gz', 'release@example.com')
    assert '--detach-sign' in calls[0]
    assert calls[1][:2] == ('gpg', '--verify')


def test_macos_notarization_and_gatekeeper(monkeypatch, tmp_path):
    calls = []
    bundle = tmp_path / 'FluxFTP.app'
    bundle.mkdir()
    monkeypatch.setattr(release_tools.sys, 'platform', 'darwin')
    monkeypatch.setattr(release_tools, 'run', lambda *args: calls.append(args))
    release_tools.notarize_macos(bundle, 'fluxftp-release')
    assert calls[0][:3] == ('codesign', '--verify', '--deep')
    assert calls[2][:3] == ('xcrun', 'notarytool', 'submit')
    assert calls[-1][:2] == ('spctl', '--assess')
