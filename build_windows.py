"""Build Windows packages using Python 3.13.15 / OpenSSL 3.5.8 and the OpenSSL 4.1 adapter.

Run with the maintained x64 builder environment. Use --test-build to replace the
reusable dist/FluxFTP-Test package after a successful build.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import ssl
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
RUNTIME_ROOT = Path(os.environ.get('FLUXFTP_RUNTIME_ROOT', ROOT / 'runtime')).resolve()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--test-build', action='store_true',
                        help='Replace the reusable dist/FluxFTP-Test package after a successful build.')
    args = parser.parse_args()
    if sys.platform != "win32" or platform.architecture()[0] != "64bit":
        raise SystemExit("This release builder requires 64-bit Windows Python.")
    if platform.python_version() != "3.13.15" or not ssl.OPENSSL_VERSION.startswith("OpenSSL 3.5.8 "):
        raise SystemExit(f"Expected Python 3.13.15 / OpenSSL 3.5.8; got {sys.version} / {ssl.OPENSSL_VERSION}")
    sys.path.insert(0, str(ROOT))
    from fluxftp import __version__
    from fluxftp.openssl_runtime import probe, validate_pair
    adapter = validate_pair(RUNTIME_ROOT / 'ssl4-adapter')
    if not adapter['version'].startswith('4.1.'):
        raise SystemExit(f"Expected OpenSSL 4.1.x adapter; got {adapter['version']}")
    probe(RUNTIME_ROOT / 'ssl4-adapter')
    cloud_provenance = json.loads((RUNTIME_ROOT / 'rclone' / 'provenance.json').read_text('utf-8'))
    cloud_hash = hashlib.sha256((RUNTIME_ROOT / 'rclone' / 'rclone.exe').read_bytes()).hexdigest()
    if cloud_hash != cloud_provenance['executable_sha256']:
        raise SystemExit('Cloud backend checksum does not match its verified provenance.')
    output_name = 'FluxFTP-Test' if args.test_build else f'FluxFTP-{__version__}'
    build_id = f'{__version__}-test' if args.test_build else __version__
    destination = ROOT / 'dist' / output_name
    pyinstaller_dist = ROOT / f'dist-{build_id}'
    work_path = ROOT / f'build-{build_id}'
    if not args.test_build and destination.exists():
        raise SystemExit(f"Release already exists: {destination}")
    if args.test_build:
        for path in (pyinstaller_dist, work_path):
            if path.exists(): shutil.rmtree(path)
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--windowed", "--name", "FluxFTP", "--icon", str(ROOT / 'fluxftp' / 'assets' / 'fluxftp.ico'),
        "--distpath", str(pyinstaller_dist),
        # keyring selects OS vaults through entry-point metadata at runtime.
        "--collect-submodules", "keyring.backends", "--copy-metadata", "keyring",
        "--runtime-hook", str(ROOT / 'openssl_runtime_hook.py'),
        "--workpath", str(work_path), "--specpath", str(work_path),
        "--add-data", f"{ROOT / 'README.md'}{os.pathsep}.",
        "--add-data", f"{ROOT / 'fluxftp' / 'assets' / 'cloud-providers'}{os.pathsep}fluxftp/assets/cloud-providers",
        "--add-data", f"{ROOT / 'fluxftp' / 'assets' / 'fluxftp-icon.svg'}{os.pathsep}fluxftp/assets",
        "--add-data", f"{ROOT / 'fluxftp' / 'assets' / 'fluxftp-icon-dark.svg'}{os.pathsep}fluxftp/assets",
        "--add-data", f"{ROOT / 'fluxftp' / 'assets' / 'connection-states'}{os.pathsep}fluxftp/assets/connection-states",
        *[arg for name in ('_ssl.pyd', '_hashlib.pyd', 'libcrypto-4-x64.dll', 'libssl-4-x64.dll', 'adapter.json', 'manifest.json')
          for arg in ('--add-data', f"{RUNTIME_ROOT / 'ssl4-adapter' / name}{os.pathsep}ssl4-adapter")],
        "--add-binary", f"{RUNTIME_ROOT / 'rclone' / 'rclone.exe'}{os.pathsep}cloud_tools",
        "--add-data", f"{RUNTIME_ROOT / 'rclone' / 'provenance.json'}{os.pathsep}cloud_tools",
        "--add-data", f"{RUNTIME_ROOT / 'rclone' / 'fluxftp-oauth-port.patch'}{os.pathsep}cloud_tools",
        str(ROOT / "launch.py"),
    ], cwd=ROOT, check=True)
    staged = pyinstaller_dist / "FluxFTP"
    for name in ('README.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md', 'requirements-windows.txt'):
        shutil.copy2(ROOT / name, staged / name)
    shutil.copytree(ROOT / 'licenses', staged / 'licenses', dirs_exist_ok=True)
    shutil.copy2(RUNTIME_ROOT / 'python-3.13.15' / 'LICENSE.txt', staged / 'licenses' / 'Python-LICENSE.txt')
    site_packages = Path(sys.prefix) / 'Lib' / 'site-packages'
    distribution_licenses = staged / 'licenses' / 'python-distributions'
    for metadata in site_packages.glob('*.dist-info'):
        source_licenses = metadata / 'licenses'
        if source_licenses.is_dir():
            shutil.copytree(source_licenses, distribution_licenses / metadata.name, dirs_exist_ok=True)
    # Qt uses Windows' unversioned ICU exports. A PATH-provided ICU build can
    # have only version-suffixed exports and prevent QtCore from loading.
    system32 = Path(os.environ['WINDIR']) / 'System32'
    for name in ('icuuc.dll', 'icu.dll'):
        # System DLLs can be read-only; do not copy that flag into generated
        # staging files, where it prevents PyInstaller from rebuilding.
        shutil.copyfile(system32 / name, staged / '_internal' / name)
    # Check the actual bundled DLLs, independently of the Python used above.
    import ctypes
    crypto = ctypes.CDLL(str(staged / "_internal" / "libcrypto-3.dll"))
    crypto.OpenSSL_version.argtypes = [ctypes.c_int]
    crypto.OpenSSL_version.restype = ctypes.c_char_p
    bundled = crypto.OpenSSL_version(0).decode("ascii")
    if hashlib.sha256((staged / '_internal' / 'cloud_tools' / 'rclone.exe').read_bytes()).hexdigest() != cloud_hash:
        raise SystemExit('Packaged cloud backend checksum mismatch.')
    if not bundled.startswith("OpenSSL 3.5.8 "):
        raise SystemExit(f"Wrong packaged OpenSSL: {bundled}")
    (staged / "runtime-version.json").write_text(json.dumps({
        "fluxftp": __version__, "python": platform.python_version(),
        "ftp_ftps_openssl": f"OpenSSL {adapter['version']}",
        "ftp_ftps_openssl_default": f"OpenSSL {adapter['version']}",
        "ftp_ftps_openssl_legacy": bundled,
        "cloud_backend": cloud_provenance,
        "dll_sha256": {name: hashlib.sha256((staged / "_internal" / name).read_bytes()).hexdigest()
                       for name in ("libssl-3.dll", "libcrypto-3.dll")},
    }, indent=2) + "\n", encoding="utf-8")
    # Copy rather than move: the inspected DLL may remain loaded on Windows.
    if args.test_build:
        package_staging = ROOT / 'dist' / '.FluxFTP-Test-staging'
        if package_staging.exists(): shutil.rmtree(package_staging)
        shutil.copytree(staged, package_staging)
        if destination.exists(): shutil.rmtree(destination)
        package_staging.replace(destination)
    else:
        shutil.copytree(staged, destination)
    print(f"Packaged {destination}; {bundled}")
    print('After verification, TLS selection and shortcut updates, run ./cleanup_builds.ps1 -Apply. Keep only the verified current release.')


if __name__ == "__main__":
    main()
