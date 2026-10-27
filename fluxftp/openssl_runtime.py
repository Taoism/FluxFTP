"""Early Windows runtime selection. Must not import ssl or hashlib before loading DLLs.

Updates are immutable side-by-side pairs; the packaged runtime is never modified.
This module also runs as the first PyInstaller runtime hook.
"""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

# hashlib would load the bundled libcrypto before we can select a replacement.
try:
    from _sha2 import sha256
except ImportError:  # Source tooling on Python <= 3.11.
    from _sha256 import sha256

DLLS = ('libcrypto-3.dll', 'libssl-3.dll')
DLLS4 = ('libcrypto-4-x64.dll', 'libssl-4-x64.dll')
BASE_VERSION = (3, 5, 8)
_handles = []
_ready_file = None


def version(value):
    match = re.fullmatch(r'([34])\.(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})', value)
    if not match:
        raise ValueError('Only stable OpenSSL 3.x and 4.x releases are supported')
    return tuple(map(int, match.groups()))


def adapter_directory():
    if getattr(sys, 'frozen', False):
        return Path(sys._MEIPASS) / 'ssl4-adapter'
    return Path(__file__).resolve().parents[1] / 'runtime' / 'ssl4-adapter'


def branch(location):
    try:
        value = read_json(location / 'branch.json')['major']
    except FileNotFoundError:
        return 3
    if type(value) is not int or value not in (3, 4):
        raise ValueError('Invalid OpenSSL branch')
    return value


def selection_file(location, major=3):
    return location / ('selected.json' if major == 3 else 'selected-4.json')


def choose_branch(location, major):
    if major not in (3, 4):
        raise ValueError('Invalid OpenSSL branch')
    # A 4.x selection is accepted only when a validated complete runtime exists.
    if major == 4:
        validate_pair(available_directory(location, major))
    write_json(location / 'branch.json', {'major': major})


def available_directory(location, major):
    if selection_file(location, major).exists():
        return selected_directory(location, major)
    if major == 4:
        directory = adapter_directory()
        validate_pair(directory)
        return directory
    return None


def root():
    return Path(os.environ['LOCALAPPDATA']) / 'FluxFTP' / 'runtime-updates' / 'windows-cp313'


def migrate_legacy(location):
    """Preserve verified 3.x selections from older per-executable update stores."""
    if selection_file(location).exists() or not location.parent.exists():
        return
    candidates = []
    for old in location.parent.iterdir():
        if not old.is_dir() or not re.fullmatch('[0-9a-f]{20}', old.name):
            continue
        try:
            directory = selected_directory(old)
            candidates.append((version(validate_pair(directory)['version']), directory))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    if candidates:
        import shutil
        _, source = max(candidates, key=lambda entry: entry[0])
        destination = location / source.name
        if not destination.exists():
            shutil.copytree(source, destination)
        validate_pair(destination)
        write_json(selection_file(location), {'directory': destination.name})


def read_json(path):
    if path.stat().st_size > 65536:
        raise ValueError('Runtime metadata too large')
    return json.loads(path.read_text(encoding='utf-8'))


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def validate_pair(directory):
    manifest = read_json(directory / 'manifest.json')
    release = version(manifest['version'])
    major = release[0]
    if release < (BASE_VERSION if major == 3 else (4, 1, 0)):
        raise ValueError('Runtime downgrade refused')
    if not re.fullmatch('[0-9a-f]{40}', manifest['commit']):
        raise ValueError('Invalid source commit')
    names = DLLS if major == 3 else (*DLLS4, '_ssl.pyd', '_hashlib.pyd')
    if major == 4:
        trusted = read_json(adapter_directory() / 'adapter.json')
        if trusted['python'] != f'{sys.version_info.major}.{sys.version_info.minor}':
            raise ValueError('OpenSSL adapter Python version mismatch')
        for name in ('_ssl.pyd', '_hashlib.pyd'):
            if manifest['sha256'][name] != trusted['sha256'][name]:
                raise ValueError('OpenSSL adapter checksum mismatch')
    for name in names:
        path = directory / name
        if path.stat().st_size > 32 * 1024 * 1024:
            raise ValueError('Runtime DLL too large')
        data = path.read_bytes()
        if sha256(data).hexdigest() != manifest['sha256'][name]:
            raise ValueError('Runtime checksum mismatch')
        if len(data) < 64 or data[:2] != b'MZ':
            raise ValueError('Invalid Windows DLL')
        offset = int.from_bytes(data[60:64], 'little')
        if data[offset:offset + 6] != b'PE\0\0\x64\x86':
            raise ValueError('Runtime must be Windows AMD64')
    return manifest


def load_pair(directory):
    manifest = validate_pair(directory)
    if '_ssl' in sys.modules or '_hashlib' in sys.modules:
        raise RuntimeError('TLS was loaded before runtime selection')
    _handles.append(os.add_dll_directory(str(directory)))
    names = DLLS if version(manifest['version'])[0] == 3 else DLLS4
    for name in names:
        _handles.append(ctypes.WinDLL(str(directory / name)))
    if names == DLLS4:
        import importlib.util
        for name in ('_ssl', '_hashlib'):
            spec = importlib.util.spec_from_file_location(name, directory / (name + '.pyd'))
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
    import ssl
    if not ssl.OPENSSL_VERSION.startswith('OpenSSL ' + manifest['version'] + ' '):
        raise RuntimeError('Python loaded an unexpected OpenSSL runtime')
    # Confirm both actual loaded modules came from the selected immutable pair.
    get_name = ctypes.windll.kernel32.GetModuleFileNameW
    get_name.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint]
    get_name.restype = ctypes.c_uint
    for handle, name in zip(_handles[-2:], names):
        buffer = ctypes.create_unicode_buffer(32768)
        if not get_name(handle._handle, buffer, len(buffer)):
            raise OSError('Cannot identify loaded TLS library')
        if Path(buffer.value).resolve() != (directory / name).resolve():
            raise RuntimeError('Loaded TLS library path mismatch')
    context = ssl.create_default_context()
    if context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
        raise RuntimeError('TLS certificate verification unavailable')
    if not ssl.RAND_status() or not context.get_ciphers():
        raise RuntimeError('TLS runtime self-check failed')
    return manifest


def command(*args):
    if getattr(sys, 'frozen', False):
        return [sys.executable, *args]
    return [sys.executable, '-m', 'fluxftp.openssl_runtime', *args]


def probe(directory):
    result = subprocess.run(command('--openssl-probe', str(directory)),
                            timeout=45, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise RuntimeError('Isolated OpenSSL runtime check failed')


def selected_directory(location, major=3):
    selected = read_json(selection_file(location, major))['directory']
    if not re.fullmatch(r'[34]\.[0-9]+\.[0-9]+-[0-9a-f]{40}', selected):
        raise ValueError('Invalid runtime directory')
    directory = location / selected
    manifest = validate_pair(directory)
    if version(manifest['version'])[0] != major:
        raise ValueError('OpenSSL selection branch mismatch')
    if selected != manifest['version'] + '-' + manifest['commit']:
        raise ValueError('Runtime identity mismatch')
    if (directory / 'rejected.json').exists():
        raise ValueError('Runtime previously failed startup')
    return directory


def ready():
    if _ready_file:
        import ssl
        try:
            write_json(_ready_file.parent / 'status.json', {
                'message': f'{ssl.OPENSSL_VERSION} is active from the verified update.'})
        except OSError:
            pass
        write_json(_ready_file, {'ready': True})


def bootstrap():
    """Supervise updated startup; fallback runs in a clean process, never unloads TLS."""
    global _ready_file
    args = sys.argv[1:]
    if args[:1] == ['--openssl-probe']:
        try:
            load_pair(Path(args[1]).resolve())
            from .openssl_probe import self_test
            self_test()
        except Exception:
            raise SystemExit(1)
        raise SystemExit(0)
    if args[:1] == ['--openssl-child']:
        directory, marker = Path(args[1]), Path(args[2])
        sys.argv = [sys.argv[0], *args[3:]]
        try:
            load_pair(directory)
        except Exception:
            raise SystemExit(1)
        _ready_file = marker
        return
    if sys.platform != 'win32' or not getattr(sys, 'frozen', False) or '--smoke-test' in args:
        return
    location = root()
    directory = None
    try:
        migrate_legacy(location)
        directory = available_directory(location, branch(location))
        if directory is None:
            return
        probe(directory)
        marker = location / ('ready-' + uuid.uuid4().hex + '.json')
        child = subprocess.Popen(command('--openssl-child', str(directory), str(marker), *args),
                                 creationflags=subprocess.CREATE_NO_WINDOW)
        # Do not kill a live application on a slow machine. Parent waits only until
        # readiness or process exit; no forced restart can interrupt its transfers.
        while child.poll() is None and not marker.exists():
            time.sleep(0.1)
        if marker.exists():
            marker.unlink(missing_ok=True)
            raise SystemExit(0)
        raise RuntimeError('Updated application failed before desktop readiness')
    except Exception as error:
        # A failed update never prevents the original bundled application opening.
        try:
            if directory and directory.parent == location:
                write_json(directory / 'rejected.json', {'reason': type(error).__name__})
            write_json(location / 'status.json', {'message':
                'OpenSSL update failed validation or startup; using the bundled libraries.'})
        except OSError:
            pass


if __name__ == '__main__':
    bootstrap()
