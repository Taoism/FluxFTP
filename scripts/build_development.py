"""Build an unsigned development app with the current Python runtime."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cloud', action='store_true', help='Include the locally built cloud backend')
    args = parser.parse_args()
    output = ROOT / 'dist/FluxFTP-dev'
    staging = ROOT / 'dist/FluxFTP'
    if output.exists():
        parser.error('dist/FluxFTP-dev already exists; move it aside before building again.')
    if staging.exists():
        parser.error('dist/FluxFTP already exists; move it aside before building again.')
    command = [sys.executable, '-m', 'PyInstaller', '--clean', '--windowed',
        '--name', 'FluxFTP', '--distpath', str(ROOT / 'dist'),
        '--workpath', str(ROOT / 'build/dev'), '--specpath', str(ROOT / 'build/dev'),
        # keyring selects OS vaults through entry-point metadata at runtime.
        # Keep those dynamically loaded backend modules in frozen builds.
        '--collect-submodules', 'keyring.backends', '--copy-metadata', 'keyring',
        '--add-data', f'{ROOT / "README.md"}{os.pathsep}.',
        '--add-data', f'{ROOT / "LICENSE"}{os.pathsep}.',
        '--add-data', f'{ROOT / "THIRD_PARTY_NOTICES.md"}{os.pathsep}.',
        '--add-data', f'{ROOT / "docs"}{os.pathsep}docs',
        '--add-data', f'{ROOT / "licenses"}{os.pathsep}licenses',
        '--add-data', f'{ROOT / "fluxftp" / "assets" / "cloud-providers"}{os.pathsep}fluxftp/assets/cloud-providers']
    if args.cloud:
        backend = ROOT / 'runtime/rclone' / ('rclone.exe' if sys.platform == 'win32' else 'rclone')
        if not backend.is_file(): parser.error('Build the cloud backend first; see docs/BUILDING.md.')
        command.extend(['--add-binary', f'{backend}{os.pathsep}cloud_tools'])
    subprocess.run([*command, str(ROOT / 'launch.py')], cwd=ROOT, check=True)
    staging.replace(output)
    print('Built an unsigned development package using the current Python TLS runtime.')


if __name__ == '__main__':
    main()
