"""Prepare a portable Ubuntu-built Linux release archive."""
import argparse
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import platform
from packaging.requirements import Requirement
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def source_manifest():
    paths = [ROOT / 'pyproject.toml', ROOT / 'README.md', ROOT / 'LICENSE',
        ROOT / 'THIRD_PARTY_NOTICES.md', ROOT / 'release_tools.py']
    for folder in ('fluxftp', 'scripts', 'docs', 'licenses'):
        paths.extend(path for path in (ROOT / folder).rglob('*') if path.is_file())
    return {path.relative_to(ROOT).as_posix(): sha256(path)
        for path in sorted(set(paths)) if path.is_file()}


def copy_dependency_notices(stage):
    notices = stage / 'licenses' / 'python-packages'
    inventory = []
    pending = ['fluxftp']
    runtime_names = set()
    while pending:
        parent = pending.pop()
        for raw_requirement in metadata.requires(parent) or ():
            requirement = Requirement(raw_requirement)
            if requirement.marker and not requirement.marker.evaluate(environment={'extra': ''}):
                continue
            package = requirement.name
            normalized = package.lower().replace('_', '-').replace('.', '-')
            if normalized not in runtime_names:
                runtime_names.add(normalized)
                pending.append(package)
    distributions = []
    for package in runtime_names:
        distribution = metadata.distribution(package)
        distributions.append(distribution)
    for distribution in sorted(distributions,
            key=lambda item: (item.metadata.get('Name') or '').lower()):
        package = distribution.metadata.get('Name') or 'unknown'
        copied = []
        for entry in distribution.files or ():
            normalized = str(entry).replace('\\', '/')
            if '.dist-info/' not in normalized:
                continue
            if not any(word in entry.name.lower() for word in
                    ('license', 'copying', 'notice', 'authors')):
                continue
            source = Path(distribution.locate_file(entry))
            if not source.is_file():
                continue
            relative = Path(normalized.split('.dist-info/', 1)[-1])
            target = notices / package / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            copied.append(target.relative_to(stage).as_posix())
        inventory.append({
            'name': package,
            'version': distribution.version,
            'license': distribution.metadata.get('License-Expression') or
                distribution.metadata.get('License', ''),
            'notices': copied,
        })
    (stage / 'dependency-inventory.json').write_text(
        json.dumps(inventory, indent=2) + '\n', encoding='utf-8')


def remove_unused_qt_components(bundle):
    # These optional Qt components are not imported by FluxFTP. Excluding them
    # avoids distributing unnecessary PDF and virtual-keyboard plugins.
    for relative in (
        '_internal/PySide6/Qt/lib/libQt6Pdf.so.6',
        '_internal/PySide6/Qt/lib/libQt6VirtualKeyboard.so.6',
        '_internal/PySide6/Qt/lib/libQt6VirtualKeyboardQml.so.6',
        '_internal/PySide6/Qt/plugins/imageformats/libqpdf.so',
        '_internal/PySide6/Qt/plugins/platforminputcontexts/libqtvirtualkeyboardplugin.so',
    ):
        path = bundle / relative
        if path.exists():
            path.unlink()


def glibc_symbol_floor(bundle):
    readelf = shutil.which('readelf')
    if not readelf:
        raise RuntimeError('readelf is required to record the package glibc floor.')
    requirements = set()
    for path in [bundle / 'FluxFTP', *bundle.rglob('*.so'), *bundle.rglob('*.so.*')]:
        if not path.is_file():
            continue
        result = subprocess.run([readelf, '--version-info', str(path)],
            text=True, capture_output=True, check=False)
        if result.returncode:
            continue
        for line in result.stdout.splitlines():
            for token in line.split():
                if token.startswith('GLIBC_') and token[6:7].isdigit():
                    requirements.add(token)
    def version_key(value):
        return tuple(int(part) for part in value.removeprefix('GLIBC_').split('.'))
    return max(requirements, key=version_key) if requirements else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', required=True, type=Path,
        help='PyInstaller FluxFTP folder produced by release_tools.py')
    parser.add_argument('--output', type=Path, default=ROOT / 'dist')
    args = parser.parse_args()
    if not sys.platform.startswith('linux') or platform.machine() not in ('x86_64', 'amd64'):
        parser.error('Build this archive on x86_64 Linux.')
    os_release = Path('/etc/os-release').read_text(encoding='utf-8')
    if 'ID=ubuntu' not in os_release or 'VERSION_ID="24.04"' not in os_release:
        parser.error('Build on Ubuntu 24.04 LTS to establish the Linux compatibility floor.')

    source = args.bundle.resolve()
    output = args.output.resolve()
    if not (source / 'FluxFTP').is_file():
        parser.error('The supplied bundle must contain the FluxFTP executable.')
    if output.exists() and not output.is_dir():
        parser.error('Output must be a directory.')
    output.mkdir(parents=True, exist_ok=True)
    version = tomllib.loads((ROOT / 'pyproject.toml').read_text('utf-8'))['project']['version']
    name = f'FluxFTP-{version}-linux-x86_64-ubuntu24.04'
    archive = output / f'{name}.tar.gz'
    if archive.exists():
        parser.error(f'Archive already exists; preserve it and choose a new output path: {archive}')

    with tempfile.TemporaryDirectory(prefix='fluxftp-linux-release-') as temporary:
        stage = Path(temporary) / name
        shutil.copytree(source, stage)
        remove_unused_qt_components(stage)
        for relative in ('README.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md'):
            shutil.copyfile(ROOT / relative, stage / relative)
        shutil.copytree(ROOT / 'docs', stage / 'docs', dirs_exist_ok=True)
        shutil.copytree(ROOT / 'licenses', stage / 'licenses', dirs_exist_ok=True)
        python_license = Path('/usr/share/doc/python3.12/copyright')
        if not python_license.is_file():
            raise RuntimeError(f'Python license file is missing: {python_license}')
        shutil.copyfile(python_license, stage / 'licenses' / 'Python.txt')
        copy_dependency_notices(stage)

        backend_provenance = json.loads((stage / '_internal' / 'cloud_tools' / 'provenance.json').read_text('utf-8'))
        glibc_floor = glibc_symbol_floor(stage)
        (stage / 'START_HERE.txt').write_text(
            f'FluxFTP {version} for Linux x86_64\n\n'
            'Extract this entire archive and run ./FluxFTP. Keep the _internal '
            'folder beside the executable. Python is not required.\n'
            'Built on Ubuntu 24.04 LTS; the recorded glibc symbol floor is '
            f'{glibc_floor or "not detected"}. GTK/XCB, FUSE and Secret Service '
            'system libraries remain host requirements.\n'
            'This package is unsigned. Your settings are stored in your user '
            'configuration directory, not in this archive.\n'
            'No account credentials, logs or deployment backups are included.\n\n'
            'See README.md, docs/, LICENSE, THIRD_PARTY_NOTICES.md and licenses/.\n',
            encoding='utf-8')

        manifest = {
            'version': version,
            'platform': platform.platform(),
            'machine': platform.machine(),
            'python': sys.version,
            'glibc_build': platform.libc_ver(),
            'glibc_symbol_floor': glibc_floor,
            'signed': False,
            'source_sha256': source_manifest(),
            'rclone': backend_provenance,
        }
        (stage / 'release-manifest.json').write_text(
            json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
        files = {path.relative_to(stage).as_posix(): sha256(path)
            for path in sorted(stage.rglob('*')) if path.is_file()}
        (stage / 'SHA256SUMS.txt').write_text(
            ''.join(f'{digest}  {path}\n' for path, digest in files.items()),
            encoding='ascii')

        env = dict(os.environ, QT_QPA_PLATFORM='offscreen')
        smoke = subprocess.run([str(stage / 'FluxFTP'), '--smoke-test'],
            cwd=stage, env=env, text=True, capture_output=True, timeout=90)
        if smoke.returncode:
            raise RuntimeError('Packaged GUI smoke check failed:\n' + smoke.stdout + smoke.stderr)
        with tarfile.open(archive, 'w:gz') as package:
            package.add(stage, arcname=name, recursive=True)

    with tarfile.open(archive, 'r:gz') as package:
        members = package.getmembers()
        if not any(item.name == f'{name}/FluxFTP' and item.isfile() and item.mode & 0o111
                for item in members):
            raise RuntimeError('Archive integrity check failed: executable bit was not preserved.')
        if not any(item.name == f'{name}/_internal' and item.isdir() for item in members):
            raise RuntimeError('Archive integrity check failed: PyInstaller runtime folder is missing.')
    digest = sha256(archive)
    (output / 'SHA256SUMS.txt').write_text(f'{digest}  {archive.name}\n', encoding='ascii')
    print(json.dumps({'archive': str(archive), 'bytes': archive.stat().st_size,
        'sha256': digest, 'glibc_symbol_floor': glibc_floor}, indent=2))


if __name__ == '__main__':
    main()
