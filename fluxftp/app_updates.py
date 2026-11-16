"""Find and download verified, platform-specific FluxFTP releases."""
from dataclasses import dataclass
import csv
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import sys
import tempfile
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


API_URL = 'https://api.github.com/repos/Taoism/FluxFTP/releases/latest'
MAX_ASSET_BYTES = 400 * 1024 * 1024
TRUSTED_DOWNLOAD_HOSTS = ('github.com', 'objects.githubusercontent.com', 'release-assets.githubusercontent.com')


@dataclass(frozen=True)
class UpdateOffer:
    version: str
    asset_name: str
    asset_url: str
    checksum_name: str
    checksum_url: str
    release_url: str
    notes: str


@dataclass(frozen=True)
class DownloadedUpdate:
    path: Path
    sha256: str


def version_key(value):
    match = re.fullmatch(r'v?(\d+(?:\.\d+){1,3})', str(value).strip())
    if not match:
        raise ValueError('GitHub release tag is not a stable FluxFTP version.')
    return tuple(int(part) for part in match.group(1).split('.'))


def supported_install_root():
    if not getattr(sys, 'frozen', False):
        return None
    executable = Path(sys.executable).resolve()
    root = executable.parent
    manifest = root / 'release-manifest.json'
    if not manifest.is_file():
        return None
    if sys.platform == 'win32':
        if any(part.casefold() == 'program files' for part in root.parts):
            return root
        return None
    if sys.platform.startswith('linux'):
        if root == Path('/opt/fluxftp') or re.fullmatch(r'FluxFTP-\d+(?:\.\d+){1,2}-linux-x86_64-ubuntu24\.04', root.name):
            return root
    return None


def platform_assets(version):
    if sys.platform == 'win32':
        return f'FluxFTP-{version}-windows-x64.zip', 'SHA256SUMS.txt'
    if not sys.platform.startswith('linux') or platform.machine().lower() not in ('x86_64', 'amd64'):
        return None
    try:
        release = platform.freedesktop_os_release()
    except (OSError, AttributeError):
        return None
    distro = release.get('ID', '').lower()
    version_id = release.get('VERSION_ID', '')
    if distro == 'ubuntu' and version_key(version_id) >= (24, 4):
        return f'FluxFTP-{version}-linux-x86_64-ubuntu24.04.tar.gz', 'SHA256SUMS-linux-ubuntu24.04.txt'
    if distro == 'debian' and version_key(version_id) >= (13,):
        return f'fluxftp_{version}-1_amd64.deb', 'SHA256SUMS-debian13.txt'
    return None


def _read_url(url, limit=2 * 1024 * 1024):
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or parsed.hostname not in ('api.github.com', 'github.com'):
        raise ValueError('Update source is not an official HTTPS GitHub URL.')
    request = Request(url, headers={
        'Accept': 'application/vnd.github+json',
        'User-Agent': 'FluxFTP-Update-Checker',
        'X-GitHub-Api-Version': '2022-11-28',
    })
    with urlopen(request, timeout=20) as response:
        final_host = urlsplit(response.geturl()).hostname
        if final_host not in TRUSTED_DOWNLOAD_HOSTS + ('api.github.com',):
            raise ValueError('GitHub redirected the update request to an untrusted host.')
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError('Update metadata or checksum is unexpectedly large.')
    return data


def check_latest(current_version):
    release = json.loads(_read_url(API_URL).decode('utf-8'))
    if release.get('draft') or release.get('prerelease'):
        return None
    tag = release.get('tag_name', '')
    latest = version_key(tag)
    if latest <= version_key(current_version):
        return None
    version = '.'.join(str(part) for part in latest)
    names = platform_assets(version)
    if names is None:
        return None
    asset_name, checksum_name = names
    assets = {item.get('name'): item for item in release.get('assets', []) if isinstance(item, dict)}
    asset, checksum = assets.get(asset_name), assets.get(checksum_name)
    if not asset or not checksum:
        return None
    release_url = release.get('html_url', '')
    if urlsplit(release_url).hostname != 'github.com':
        raise ValueError('GitHub release URL did not match the official project host.')
    return UpdateOffer(version, asset_name, asset.get('browser_download_url', ''), checksum_name,
        checksum.get('browser_download_url', ''), release_url, str(release.get('body') or ''))


def _download(url, destination, limit):
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or parsed.hostname != 'github.com':
        raise ValueError('Update asset is not hosted on GitHub.')
    request = Request(url, headers={'User-Agent': 'FluxFTP-Update-Checker',
                                   'Accept': 'application/octet-stream'})
    digest, total = hashlib.sha256(), 0
    with urlopen(request, timeout=45) as response:
        if urlsplit(response.geturl()).hostname not in TRUSTED_DOWNLOAD_HOSTS:
            raise ValueError('GitHub redirected the update download to an untrusted host.')
        length = response.headers.get('Content-Length')
        if length and int(length) > limit:
            raise ValueError('Update asset exceeds the supported download size.')
        with destination.open('wb') as output:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > limit:
                    raise ValueError('Update asset exceeded the supported download size.')
                digest.update(chunk)
                output.write(chunk)
    return digest.hexdigest()


def _sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def download_update(offer):
    directory = Path(tempfile.gettempdir()) / 'FluxFTP-updates'
    directory.mkdir(parents=True, exist_ok=True)
    asset = directory / offer.asset_name
    checksum_file = directory / offer.checksum_name
    try:
        _download(offer.checksum_url, checksum_file, 128 * 1024)
        expected = None
        for line in checksum_file.read_text('ascii').splitlines():
            parts = line.strip().split()
            if len(parts) >= 2 and parts[-1].lstrip('*') == offer.asset_name:
                expected = parts[0].lower()
                break
        if not expected or not re.fullmatch(r'[a-f0-9]{64}', expected):
            raise ValueError('The GitHub release checksum file did not contain this platform asset.')
        actual = _download(offer.asset_url, asset, MAX_ASSET_BYTES)
        if actual != expected:
            asset.unlink(missing_ok=True)
            raise ValueError('The downloaded update failed its SHA-256 verification.')
        return DownloadedUpdate(asset, actual)
    finally:
        checksum_file.unlink(missing_ok=True)


def _secure_windows_update_directory(directory):
    """Restrict updater files to the current user and trusted elevated identities."""
    import subprocess
    system32 = Path(os.environ['WINDIR']) / 'System32'
    identity = subprocess.run([str(system32 / 'whoami.exe'), '/user', '/fo', 'csv', '/nh'],
        check=True, capture_output=True, text=True).stdout.strip()
    records = list(csv.reader(identity.splitlines()))
    if len(records) != 1 or len(records[0]) < 2 or not re.fullmatch(r'S-1-(?:\d+-)+\d+', records[0][1]):
        raise OSError('Could not determine the Windows account SID for the update helper.')
    icacls = system32 / 'icacls.exe'
    subprocess.run([str(icacls), str(directory), '/inheritance:r', '/grant:r',
        f'*{records[0][1]}:(OI)(CI)F', '*S-1-5-18:(OI)(CI)F', '*S-1-5-32-544:(OI)(CI)F'],
        check=True, capture_output=True, text=True)
    subprocess.run([str(icacls), str(directory), '/setintegritylevel', '(OI)(CI)M'],
        check=True, capture_output=True, text=True)


def start_windows_installer(archive, offer, install_root, process_id, expected_sha256):
    import ctypes
    import subprocess
    if not re.fullmatch(r'[a-fA-F0-9]{64}', expected_sha256):
        raise ValueError('The verified update SHA-256 is invalid.')
    archive = Path(archive)
    if _sha256_file(archive) != expected_sha256.lower():
        raise ValueError('The downloaded update changed after checksum verification.')
    directory = Path(tempfile.mkdtemp(prefix='FluxFTP-update-'))
    try:
        _secure_windows_update_directory(directory)
        private_archive = directory / Path(offer.asset_name).name
        shutil.copyfile(archive, private_archive)
        if _sha256_file(private_archive) != expected_sha256.lower():
            raise ValueError('The downloaded update changed while preparing the installer.')
        config_path = directory / 'update.json'
        script_path = directory / 'install.ps1'
        config_path.write_text(json.dumps({
            'archive': str(private_archive), 'target': str(install_root), 'pid': int(process_id),
            'version': offer.version, 'sha256': expected_sha256.lower(),
        }), encoding='utf-8')
        script_path.write_text(r'''
$ErrorActionPreference = 'Stop'
$config = Get-Content -LiteralPath $args[0] -Raw | ConvertFrom-Json
if ($config.version -notmatch '^\d+(\.\d+){1,2}$') { throw 'The update version is invalid.' }
if (-not (Test-Path -LiteralPath (Join-Path $config.target 'FluxFTP.exe'))) { throw 'The target is not a FluxFTP installation.' }
try { Wait-Process -Id $config.pid -ErrorAction SilentlyContinue } catch {}
$archiveHash = (Get-FileHash -LiteralPath $config.archive -Algorithm SHA256).Hash.ToLowerInvariant()
if ($archiveHash -ne $config.sha256) { throw 'The update archive failed SHA-256 verification.' }
$parent = Split-Path -Parent $config.target
$privateRoot = $PSScriptRoot
$work = Join-Path $privateRoot ('extracted-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $work | Out-Null
try {
Expand-Archive -LiteralPath $config.archive -DestinationPath $work
$source = Get-ChildItem -LiteralPath $work -Directory | Select-Object -First 1
$manifest = Get-Content -LiteralPath (Join-Path $source.FullName 'release-manifest.json') -Raw | ConvertFrom-Json
if ($manifest.version -ne $config.version) { throw 'The update manifest version does not match.' }
$staging = Join-Path $parent ('FluxFTP-update-' + $config.version)
$backup = $config.target + '.previous-' + $config.version
if (Test-Path -LiteralPath $staging) { throw 'FluxFTP update staging path already exists.' }
Copy-Item -LiteralPath $source.FullName -Destination $staging -Recurse
Move-Item -LiteralPath $config.target -Destination $backup
try { Move-Item -LiteralPath $staging -Destination $config.target }
catch { if (-not (Test-Path -LiteralPath $config.target)) { Move-Item -LiteralPath $backup -Destination $config.target }; throw }
Start-Process -FilePath (Join-Path $config.target 'FluxFTP.exe') -WorkingDirectory $config.target
} finally {
if (Test-Path -LiteralPath $work) { Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue }
Start-Sleep -Milliseconds 250
Remove-Item -LiteralPath $privateRoot -Recurse -Force -ErrorAction SilentlyContinue
}
'''.strip() + '\n', encoding='utf-8')
        arguments = f'-NoProfile -ExecutionPolicy Bypass -File "{script_path}" "{config_path}"'
        result = ctypes.windll.shell32.ShellExecuteW(None, 'runas', 'powershell.exe', arguments, None, 0)
        if result <= 32:
            raise OSError('Windows declined the elevated update helper.')
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        raise


def start_ubuntu_installer(archive, offer, install_root, process_id):
    import shlex
    import subprocess
    import uuid
    if not os.access(install_root.parent, os.W_OK):
        raise PermissionError('The FluxFTP install folder is not writable by this user.')
    script = Path(tempfile.gettempdir()) / f'fluxftp-update-{uuid.uuid4().hex}.sh'
    q_archive, q_root = shlex.quote(str(archive)), shlex.quote(str(install_root))
    q_launcher = shlex.quote(str(install_root / 'FluxFTP'))
    script.write_text(f'''#!/bin/sh
while kill -0 {int(process_id)} 2>/dev/null; do sleep 1; done
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
tar -xzf {q_archive} -C "$work"
source=$(find "$work" -mindepth 1 -maxdepth 1 -type d -print -quit)
test -x "$source/FluxFTP"
test -f "$source/release-manifest.json"
backup={q_root}.previous-{offer.version}
mv {q_root} "$backup"
if ! mv "$source" {q_root}; then mv "$backup" {q_root}; exit 1; fi
nohup {q_launcher} >/dev/null 2>&1 &
''', encoding='utf-8')
    script.chmod(0o700)
    subprocess.Popen(['sh', str(script)], close_fds=True, start_new_session=True)


def start_debian_installer(package, offer, process_id, expected_sha256):
    import shutil
    import subprocess
    if not shutil.which('pkexec') or not shutil.which('apt-get'):
        raise OSError('pkexec and apt-get are needed to install Debian updates interactively.')
    if not re.fullmatch(r'[a-fA-F0-9]{64}', expected_sha256):
        raise ValueError('The verified update SHA-256 is invalid.')
    package = Path(package)
    if _sha256_file(package) != expected_sha256.lower():
        raise ValueError('The downloaded update changed after checksum verification.')
    private_directory = Path(tempfile.mkdtemp(prefix='fluxftp-deb-update-'))
    private_directory.chmod(0o700)
    private_package = private_directory / Path(offer.asset_name).name
    try:
        shutil.copyfile(package, private_package)
        private_package.chmod(0o600)
        if _sha256_file(private_package) != expected_sha256.lower():
            raise ValueError('The downloaded update changed while preparing the installer.')
        result = subprocess.run(['pkexec', 'apt-get', 'install', '-y', str(private_package)],
            timeout=900, check=False)
        if result.returncode:
            raise OSError('The package manager did not complete the FluxFTP update.')
    finally:
        shutil.rmtree(private_directory, ignore_errors=True)
    script = Path(tempfile.gettempdir()) / f'fluxftp-restart-{offer.version}.sh'
    script.write_text(f'''#!/bin/sh
while kill -0 {int(process_id)} 2>/dev/null; do sleep 1; done
exec /usr/bin/fluxftp
''', encoding='utf-8')
    script.chmod(0o700)
    subprocess.Popen(['sh', str(script)], close_fds=True, start_new_session=True)
