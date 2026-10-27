"""Native release packaging and explicit signing. Never publishes artifacts."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def run(*arguments):
    subprocess.run([str(a) for a in arguments], check=True)


def package_native(backend, expected_hash, identity=None):
    if sys.platform == 'win32':
        raise ValueError('Use build_windows.py with the validated Windows runtime.')
    backend = Path(backend).resolve()
    actual = hashlib.sha256(backend.read_bytes()).hexdigest()
    if actual != expected_hash.lower():
        raise ValueError('Cloud backend checksum does not match the supplied verified digest.')
    if backend.name != 'rclone' or not os.access(backend, os.X_OK):
        raise ValueError('Supply an executable native rclone binary named rclone.')
    from fluxftp import __version__
    destination = ROOT / 'dist' / f'FluxFTP-{__version__}-{sys.platform}-{platform.machine()}'
    if destination.exists():
        raise ValueError(f'Release already exists: {destination}')
    arguments = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
        '--windowed', '--name', 'FluxFTP', '--distpath', destination,
        '--workpath', ROOT / 'build-native', '--specpath', ROOT / 'build-native',
        '--add-binary', f'{backend}:cloud_tools',
        '--add-data', f'{ROOT / "licenses" / "rclone.txt"}:licenses',
        '--add-data', f'{ROOT / "README.md"}:.']
    if sys.platform == 'darwin':
        arguments.extend(['--osx-bundle-identifier', 'com.fluxftp.desktop'])
        if identity:
            arguments.extend(['--codesign-identity', identity])
    elif identity:
        raise ValueError('A packaging signing identity is only supported on macOS.')
    run(*arguments, ROOT / 'launch.py')
    (destination / 'build-provenance.json').write_text(json.dumps({
        'version': __version__, 'platform': platform.platform(),
        'python': platform.python_version(), 'backend_sha256': actual,
        'signing_identity_requested': bool(identity),
        'release_signature_verified': False}, indent=2) + '\n')
    return destination


def notarize_macos(bundle, profile):
    import tempfile
    if sys.platform != 'darwin':
        raise ValueError('Notarization requires a signed macOS app and a macOS build host.')
    bundle = Path(bundle).resolve()
    if bundle.suffix != '.app' or not bundle.is_dir():
        raise ValueError('Supply the signed .app bundle.')
    run('codesign', '--verify', '--deep', '--strict', '--verbose=2', bundle)
    with tempfile.TemporaryDirectory(prefix='fluxftp-notarize-') as directory:
        archive = Path(directory) / 'FluxFTP.zip'
        run('ditto', '-c', '-k', '--keepParent', bundle, archive)
        run('xcrun', 'notarytool', 'submit', archive, '--keychain-profile', profile, '--wait')
    run('xcrun', 'stapler', 'staple', bundle)
    run('xcrun', 'stapler', 'validate', bundle)
    run('spctl', '--assess', '--type', 'execute', '--verbose=2', bundle)


def sign_windows(executable, thumbprint, timestamp):
    if sys.platform != 'win32' or not timestamp.startswith('https://'):
        raise ValueError('Windows signing requires Windows and an HTTPS timestamp service.')
    if len(thumbprint) != 40 or any(c not in '0123456789abcdefABCDEF' for c in thumbprint):
        raise ValueError('Supply the SHA-1 thumbprint of your code-signing certificate in the user certificate store.')
    run('signtool', 'sign', '/sha1', thumbprint, '/fd', 'SHA256', '/tr', timestamp,
        '/td', 'SHA256', Path(executable).resolve())
    run('signtool', 'verify', '/pa', '/all', '/v', Path(executable).resolve())


def sign_linux(archive, identity):
    if not sys.platform.startswith('linux'):
        raise ValueError('Linux archive signing requires a Linux build host.')
    archive = Path(archive).resolve()
    signature = Path(str(archive) + '.asc')
    if signature.exists():
        raise ValueError('Signature already exists; verify it before replacing anything.')
    run('gpg', '--armor', '--local-user', identity, '--detach-sign', archive)
    run('gpg', '--verify', signature, archive)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    native = commands.add_parser('package-native')
    native.add_argument('--backend', required=True)
    native.add_argument('--sha256', required=True)
    native.add_argument('--identity', help='macOS Developer ID Application identity')
    macos = commands.add_parser('notarize-macos')
    macos.add_argument('bundle')
    macos.add_argument('--profile', required=True, help='Existing notarytool Keychain profile')
    windows = commands.add_parser('sign-windows')
    windows.add_argument('executable')
    windows.add_argument('--thumbprint', required=True)
    windows.add_argument('--timestamp', required=True)
    linux = commands.add_parser('sign-linux')
    linux.add_argument('archive')
    linux.add_argument('--identity', required=True)
    args = parser.parse_args()
    if args.command == 'package-native':
        print(package_native(args.backend, args.sha256, args.identity))
    elif args.command == 'sign-windows':
        sign_windows(args.executable, args.thumbprint, args.timestamp)
    elif args.command == 'sign-linux':
        sign_linux(args.archive, args.identity)
    else:
        notarize_macos(args.bundle, args.profile)


if __name__ == '__main__':
    main()
