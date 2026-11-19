"""Build and stage a verified SQLite shared library for Linux releases."""
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
from urllib.request import urlretrieve


VERSION = '3.54.1'
ARCHIVE = 'sqlite-autoconf-3540100.tar.gz'
SOURCE_URL = f'https://sqlite.org/2026/{ARCHIVE}'
SOURCE_SHA3_256 = 'b6ced585c750916b1052667239a12c25a7ff34e7f06e1e6e01ecb0427657d9c4'


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def sqlite_version(library):
    runtime = ctypes.CDLL(str(library))
    runtime.sqlite3_libversion.restype = ctypes.c_char_p
    return runtime.sqlite3_libversion().decode('ascii')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', required=True, type=Path,
        help='PyInstaller bundle containing FluxFTP and _internal')
    args = parser.parse_args()
    bundle = args.bundle.resolve()
    internal = bundle / '_internal'
    if not internal.is_dir():
        parser.error(f'PyInstaller bundle is missing its internal library directory: {internal}')
    target = internal / 'libsqlite3.so.0'

    with tempfile.TemporaryDirectory(prefix='fluxftp-sqlite-') as temporary:
        work = Path(temporary)
        archive = work / ARCHIVE
        urlretrieve(SOURCE_URL, archive)
        digest = hashlib.sha3_256(archive.read_bytes()).hexdigest()
        if digest != SOURCE_SHA3_256:
            raise RuntimeError(f'SQLite source SHA3-256 mismatch: {digest}')

        source_dir = work / 'source'
        source_dir.mkdir()
        with tarfile.open(archive, 'r:gz') as source:
            source.extractall(source_dir, filter='data')
        source_root = source_dir / 'sqlite-autoconf-3540100'
        prefix = work / 'install'
        subprocess.run([str(source_root / 'configure'), f'--prefix={prefix}',
            '--enable-shared', '--disable-static'], cwd=source_root, check=True)
        subprocess.run(['make', f'-j{os.cpu_count() or 2}'], cwd=source_root, check=True)
        subprocess.run(['make', 'install'], cwd=source_root, check=True)
        built_library = prefix / 'lib' / 'libsqlite3.so.0'
        if not built_library.is_file():
            raise RuntimeError('SQLite build did not produce libsqlite3.so.0.')
        built_version = sqlite_version(built_library)
        if built_version != VERSION:
            raise RuntimeError(f'Expected SQLite {VERSION}; built {built_version}.')
        shutil.copy2(built_library.resolve(), target)

    staged_version = sqlite_version(target)
    if staged_version != VERSION:
        raise RuntimeError(f'Staged SQLite version check failed: {staged_version}.')
    provenance = {
        'version': staged_version,
        'source_url': SOURCE_URL,
        'source_sha3_256': SOURCE_SHA3_256,
        'library_sha256': sha256(target),
        'build_options': ['--enable-shared', '--disable-static'],
    }
    (internal / 'sqlite-provenance.json').write_text(
        json.dumps(provenance, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(provenance, indent=2))


if __name__ == '__main__':
    main()
