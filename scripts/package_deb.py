"""Build and inspect a native Debian package from a tested FluxFTP bundle."""
import argparse
from pathlib import Path
import shutil
import subprocess
import tempfile
import tomllib
import os

ROOT = Path(__file__).resolve().parents[1]
MAINTAINER = 'Taoism <366831+Taoism@users.noreply.github.com>'
DEPENDS = [
    'libc6 (>= 2.38)', 'libatomic1', 'libdbus-1-3', 'libegl1', 'libfontconfig1',
    'libfreetype6', 'libgcc-s1', 'libgl1', 'libglib2.0-0', 'libstdc++6', 'libx11-6', 'libx11-xcb1',
    'libxcb-cursor0', 'libxcb-icccm4', 'libxcb-image0', 'libxcb-keysyms1',
    'libxcb-randr0', 'libxcb-render-util0', 'libxcb-render0', 'libxcb-shape0',
    'libxcb-shm0', 'libxcb-sync1', 'libxcb-util1', 'libxcb-xfixes0', 'libxcb-xkb1',
    'libxkbcommon0', 'libxkbcommon-x11-0', 'libzstd1', 'zlib1g',
]


def run(*args):
    subprocess.run([str(arg) for arg in args], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True,
        help='Tested one-folder FluxFTP build with FluxFTP and _internal entries')
    parser.add_argument('--output', type=Path, default=ROOT / 'dist')
    args = parser.parse_args()
    if not shutil.which('dpkg-deb'):
        parser.error('Install dpkg-dev to build a Debian package.')
    os_release = Path('/etc/os-release').read_text(encoding='utf-8')
    if 'ID=debian' not in os_release or 'VERSION_ID="13"' not in os_release:
        parser.error('Build the native package on Debian 13.')
    bundle, output = args.bundle.resolve(), args.output.resolve()
    if not (bundle / 'FluxFTP').is_file() or not (bundle / '_internal').is_dir():
        parser.error('Bundle must contain FluxFTP and _internal.')
    output.mkdir(parents=True, exist_ok=True)
    version = tomllib.loads((ROOT / 'pyproject.toml').read_text('utf-8'))['project']['version']
    deb_version = version + '-1'
    deb = output / f'fluxftp_{deb_version}_amd64.deb'
    if deb.exists():
        parser.error(f'Package already exists; preserve it and choose a new output path: {deb}')

    with tempfile.TemporaryDirectory(prefix='fluxftp-deb-') as temporary:
        stage = Path(temporary) / 'package-root'
        control = stage / 'DEBIAN'
        opt = stage / 'opt' / 'fluxftp'
        launcher = stage / 'usr' / 'bin' / 'fluxftp'
        applications = stage / 'usr' / 'share' / 'applications'
        documentation = stage / 'usr' / 'share' / 'doc' / 'fluxftp'
        control.mkdir(parents=True)
        opt.parent.mkdir(parents=True)
        shutil.copytree(bundle, opt)
        for relative in ('FluxFTP', '_internal/cloud_tools/rclone'):
            executable = opt / relative
            executable.chmod(executable.stat().st_mode | 0o111)
        launcher.parent.mkdir(parents=True)
        launcher.write_text('#!/bin/sh\nexec /opt/fluxftp/FluxFTP "$@"\n', encoding='utf-8')
        launcher.chmod(0o755)
        applications.mkdir(parents=True)
        (applications / 'fluxftp.desktop').write_text(
            '[Desktop Entry]\n'
            'Type=Application\n'
            'Name=FluxFTP\n'
            'GenericName=File Transfer Client\n'
            'Comment=FTP, FTPS, SFTP and cloud storage client\n'
            'Exec=fluxftp\n'
            'TryExec=fluxftp\n'
            'Icon=network-server\n'
            'Terminal=false\n'
            'Categories=Network;FileTransfer;Utility;\n', encoding='utf-8')
        documentation.mkdir(parents=True)
        for relative in ('README.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md',
                'dependency-inventory.json', 'release-manifest.json', 'build-provenance.json', 'START_HERE.txt'):
            source = (opt / '_internal' / relative) if relative not in ('build-provenance.json',) else (opt / relative)
            if source.is_file():
                shutil.copyfile(source, documentation / relative)
        for folder in ('docs', 'licenses'):
            source = opt / '_internal' / folder
            if source.is_dir():
                shutil.copytree(source, documentation / folder, dirs_exist_ok=True)
        (documentation / 'copyright').write_text(
            'Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/\n'
            'Upstream-Name: FluxFTP\n'
            'Source: https://github.com/Taoism/FluxFTP\n\n'
            'Files: *\n'
            'Copyright: 2026 FluxFTP contributors\n'
            'License: MIT\n'
            ' The complete MIT license is in the LICENSE file. Third-party license\n'
            ' texts and notices are in THIRD_PARTY_NOTICES.md and licenses/.\n',
            encoding='utf-8')
        installed_size = sum(path.stat().st_size for path in stage.rglob('*') if path.is_file()) // 1024
        (control / 'control').write_text(
            'Package: fluxftp\n'
            f'Version: {deb_version}\n'
            'Section: net\n'
            'Priority: optional\n'
            'Architecture: amd64\n'
            f'Maintainer: {MAINTAINER}\n'
            'Homepage: https://github.com/Taoism/FluxFTP\n'
            f'Installed-Size: {installed_size}\n'
            f'Depends: {", ".join(DEPENDS)}\n'
            'Suggests: gnome-keyring, sshfs\n'
            'Description: Multi-platform file transfer and cloud storage client\n'
            ' FTP, FTPS, SFTP and cloud storage client with synchronization,\n'
            ' verification reports and deployment rollback.\n', encoding='utf-8')
        run('dpkg-deb', '--build', '--root-owner-group', stage, deb)
        info = subprocess.run(['dpkg-deb', '--info', str(deb)],
            check=True, text=True, capture_output=True).stdout
        contents = subprocess.run(['dpkg-deb', '--contents', str(deb)],
            check=True, text=True, capture_output=True).stdout
        for required in ('Package: fluxftp', f'Version: {deb_version}', 'Architecture: amd64'):
            if required not in info:
                raise RuntimeError(f'Debian package metadata is missing {required!r}.')
        for required in ('./opt/fluxftp/FluxFTP', './usr/bin/fluxftp', './usr/share/applications/fluxftp.desktop'):
            if required not in contents:
                raise RuntimeError(f'Debian package is missing {required!r}.')
        extracted = Path(temporary) / 'installed'
        run('dpkg-deb', '--extract', deb, extracted)
        environment = dict(os.environ, QT_QPA_PLATFORM='offscreen',
            XDG_CONFIG_HOME=str(Path(temporary) / 'config'))
        smoke = subprocess.run([str(extracted / 'opt/fluxftp/FluxFTP'), '--smoke-test'],
            cwd=extracted / 'opt/fluxftp', env=environment, text=True,
            capture_output=True, timeout=90)
        if smoke.returncode:
            raise RuntimeError('Installed Debian package GUI smoke check failed:\n' + smoke.stdout + smoke.stderr)
        rclone = subprocess.run([str(extracted / 'opt/fluxftp/_internal/cloud_tools/rclone'), 'version'],
            check=True, text=True, capture_output=True)
        if 'v1.75.1-fluxftp.2' not in rclone.stdout:
            raise RuntimeError('Debian package contains an unexpected rclone backend.')
    print(deb)


if __name__ == '__main__':
    main()
