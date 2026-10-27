"""Build an unsigned development app with the current Python runtime."""
import argparse
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cloud', action='store_true', help='Include the locally built cloud backend')
    args = parser.parse_args()
    if (ROOT / 'dist/FluxFTP-dev').exists():
        parser.error('dist/FluxFTP-dev already exists; move it aside before building again.')
    command = [sys.executable, '-m', 'PyInstaller', '--clean', '--windowed',
        '--name', 'FluxFTP-dev', '--distpath', str(ROOT / 'dist'),
        '--workpath', str(ROOT / 'build/dev'), '--specpath', str(ROOT / 'build/dev'),
        '--add-data', f'{ROOT / "README.md"}:.',
        '--add-data', f'{ROOT / "LICENSE"}:.',
        '--add-data', f'{ROOT / "THIRD_PARTY_NOTICES.md"}:.',
        '--add-data', f'{ROOT / "docs"}:docs',
        '--add-data', f'{ROOT / "licenses"}:licenses']
    if args.cloud:
        backend = ROOT / 'runtime/rclone' / ('rclone.exe' if sys.platform == 'win32' else 'rclone')
        if not backend.is_file(): parser.error('Build the cloud backend first; see docs/BUILDING.md.')
        command.extend(['--add-binary', f'{backend}:cloud_tools'])
    subprocess.run([*command, str(ROOT / 'launch.py')], cwd=ROOT, check=True)
    print('Built an unsigned development package using the current Python TLS runtime.')


if __name__ == '__main__':
    main()
