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
    parser.add_argument('--test-build', action='store_true',
                        help='Build or replace the reusable dist/FluxFTP-Test folder')
    args = parser.parse_args()
    output_name = 'FluxFTP-Test' if args.test_build else 'FluxFTP-dev'
    output = ROOT / 'dist' / output_name
    staging_root = ROOT / 'dist' / ('.fluxftp-test-staging' if args.test_build else '')
    staging = staging_root / output_name
    if not args.test_build:
        if output.exists():
            parser.error('dist/FluxFTP-dev already exists; move it aside before building again.')
        if staging.exists():
            parser.error('dist/FluxFTP already exists; move it aside before building again.')
    elif staging_root.exists():
        import shutil
        shutil.rmtree(staging_root)
    command = [sys.executable, '-m', 'PyInstaller', '--clean', '--windowed',
        '--noconfirm', '--name', output_name, '--distpath', str(staging_root),
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
    command.extend(['--add-data', f'{ROOT / "fluxftp" / "assets" / "fluxftp-icon.svg"}{os.pathsep}fluxftp/assets'])
    command.extend(['--add-data', f'{ROOT / "fluxftp" / "assets" / "fluxftp-icon-dark.svg"}{os.pathsep}fluxftp/assets'])
    command.extend(['--add-data', f'{ROOT / "fluxftp" / "assets" / "connection-states"}{os.pathsep}fluxftp/assets/connection-states'])
    if sys.platform == 'win32':
        command.extend(['--icon', str(ROOT / 'fluxftp' / 'assets' / 'fluxftp.ico')])
    if args.cloud:
        backend = ROOT / 'runtime/rclone' / ('rclone.exe' if sys.platform == 'win32' else 'rclone')
        if not backend.is_file(): parser.error('Build the cloud backend first; see docs/BUILDING.md.')
        command.extend(['--add-binary', f'{backend}{os.pathsep}cloud_tools'])
    subprocess.run([*command, str(ROOT / 'launch.py')], cwd=ROOT, check=True)
    if args.test_build and output.exists():
        import shutil
        shutil.rmtree(output)
    staging.replace(output)
    print(f'Built {output} using the current Python TLS runtime.')


if __name__ == '__main__':
    main()
