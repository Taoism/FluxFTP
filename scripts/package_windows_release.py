"""Prepare a public Windows ZIP from a validated application folder."""
import argparse
import hashlib
from importlib import metadata
import json
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', required=True, type=Path)
    parser.add_argument('--python-license', required=True, type=Path)
    parser.add_argument('--output', type=Path, default=ROOT / 'build/public-release')
    args = parser.parse_args()
    sys.path.insert(0, str(ROOT))
    from fluxftp import __version__
    source, output = args.package.resolve(), args.output.resolve()
    if output.exists(): parser.error('Output already exists; choose a fresh directory.')
    if not (source / 'FluxFTP.exe').is_file(): parser.error('Expected FluxFTP.exe in the package.')
    output.mkdir(parents=True)
    name = f'FluxFTP-{__version__}-windows-x64'
    stage = output / name
    shutil.copytree(source, stage)
    # These optional plugins are not used by FluxFTP. Keep the public package
    # free of GPL-only PDF/virtual-keyboard components and Windows system ICU.
    for relative in ('_internal/PySide6/Qt6Pdf.dll', '_internal/PySide6/Qt6VirtualKeyboard.dll',
        '_internal/PySide6/plugins/imageformats/qpdf.dll',
        '_internal/PySide6/plugins/platforminputcontexts/qtvirtualkeyboardplugin.dll',
        '_internal/icu.dll', '_internal/icuuc.dll'):
        path = stage / relative
        if path.exists():
            path.chmod(0o666)
            path.unlink()
    for filename in ('README.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md'):
        shutil.copyfile(ROOT / filename, stage / filename)
    shutil.copyfile(ROOT / 'README.md', stage / '_internal/README.md')
    shutil.copytree(ROOT / 'docs', stage / 'docs')
    shutil.copytree(ROOT / 'docs', stage / '_internal/docs')
    notices = stage / 'licenses'
    shutil.copytree(ROOT / 'licenses', notices)
    shutil.copyfile(args.python_license, notices / 'Python.txt')
    inventory = []
    for distribution in sorted(metadata.distributions(), key=lambda d: d.metadata['Name'].lower()):
        package = distribution.metadata['Name']
        if package.lower() == 'fluxftp': continue
        copied = []
        for entry in distribution.files or []:
            if '.dist-info/' not in str(entry).replace('\\', '/'): continue
            if not any(word in entry.name.lower() for word in ('license', 'copying', 'notice', 'authors')): continue
            path = Path(distribution.locate_file(entry))
            if not path.is_file(): continue
            target = notices / 'python-packages' / package / Path(str(entry).split('.dist-info/', 1)[-1])
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
            copied.append(target.relative_to(stage).as_posix())
        inventory.append(dict(name=package, version=distribution.version,
            license=distribution.metadata.get('License-Expression') or distribution.metadata.get('License', ''),
            notices=copied))
    (stage / 'dependency-inventory.json').write_text(json.dumps(inventory, indent=2) + '\n', encoding='utf-8')
    (stage / 'START_HERE.txt').write_text(
        'FluxFTP ' + __version__ + ' for Windows x64\n\n'
        'Extract this entire ZIP to a writable folder and run FluxFTP.exe.\n'
        'Keep the _internal folder beside the executable. Python is not required.\n'
        'This package is unsigned; Windows may display an unknown-publisher warning.\n'
        'Your settings are stored in your Windows user profile, not this ZIP.\n'
        'No accounts, credentials, logs or deployment backups are included.\n\n'
        'Source and updates: https://github.com/Taoism/FluxFTP\n'
        'See LICENSE, THIRD_PARTY_NOTICES.md and licenses/ for component notices.\n', encoding='utf-8')
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    hashes = {p.relative_to(stage).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(stage.rglob('*')) if p.is_file()}
    (stage / 'release-manifest.json').write_text(json.dumps(dict(version=__version__, source_commit=revision,
        signed=False, files=hashes), indent=2) + '\n', encoding='utf-8')
    archive = output / (name + '.zip')
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as zip:
        for path in sorted(stage.rglob('*')):
            if path.is_file(): zip.write(path, path.relative_to(output))
    with zipfile.ZipFile(archive) as zip:
        if zip.testzip(): raise RuntimeError('ZIP integrity check failed')
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (output / 'SHA256SUMS.txt').write_text(f'{digest}  {archive.name}\n', encoding='ascii')
    print(json.dumps(dict(archive=str(archive), bytes=archive.stat().st_size, sha256=digest)))


if __name__ == '__main__': main()
