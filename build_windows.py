"""Build the local Windows release with the validated FTP/FTPS TLS runtime.

Run with .venv-openssl358/Scripts/python.exe. See README.md (Windows runtime).
"""
import hashlib
import json
from pathlib import Path
import platform
import ssl
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def main():
    if sys.platform != "win32" or platform.architecture()[0] != "64bit":
        raise SystemExit("This release builder requires 64-bit Windows Python.")
    if platform.python_version() != "3.13.15" or not ssl.OPENSSL_VERSION.startswith("OpenSSL 3.5.8 "):
        raise SystemExit(f"Expected Python 3.13.15 / OpenSSL 3.5.8; got {sys.version} / {ssl.OPENSSL_VERSION}")
    sys.path.insert(0, str(ROOT))
    from fluxftp import __version__
    cloud_provenance = json.loads((ROOT / 'runtime' / 'rclone' / 'provenance.json').read_text('utf-8'))
    cloud_hash = hashlib.sha256((ROOT / 'runtime' / 'rclone' / 'rclone.exe').read_bytes()).hexdigest()
    if cloud_hash != cloud_provenance['executable_sha256']:
        raise SystemExit('Cloud backend checksum does not match its verified provenance.')
    destination = ROOT / "dist" / f"FluxFTP-{__version__}"
    if destination.exists():
        raise SystemExit(f"Release already exists: {destination}")
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--windowed", "--name", "FluxFTP", "--distpath", f"dist-{__version__}",
        "--runtime-hook", str(ROOT / 'openssl_runtime_hook.py'),
        "--workpath", f"build-{__version__}", "--specpath", f"build-{__version__}",
        "--add-data", f"{ROOT / 'README.md'}:.",
        *[arg for name in ('_ssl.pyd', '_hashlib.pyd', 'libcrypto-4-x64.dll', 'libssl-4-x64.dll', 'adapter.json', 'manifest.json')
          for arg in ('--add-data', f"{ROOT / 'runtime' / 'ssl4-adapter' / name}:ssl4-adapter")],
        "--add-data", f"{ROOT / 'licenses' / 'OpenSSL.txt'}:licenses",
        "--add-binary", f"{ROOT / 'runtime' / 'rclone' / 'rclone.exe'}:cloud_tools",
        "--add-data", f"{ROOT / 'licenses' / 'rclone.txt'}:licenses",
        "--add-data", f"{ROOT / 'runtime' / 'rclone' / 'provenance.json'}:cloud_tools",
        "--add-data", f"{ROOT / 'runtime' / 'rclone' / 'fluxftp-oauth-port.patch'}:cloud_tools",
        str(ROOT / "launch.py"),
    ], cwd=ROOT, check=True)
    staged = ROOT / f"dist-{__version__}" / "FluxFTP"
    # Qt uses Windows' unversioned ICU exports. A PATH-provided ICU build can
    # have only version-suffixed exports and prevent QtCore from loading.
    import os
    import shutil
    system32 = Path(os.environ['WINDIR']) / 'System32'
    for name in ('icuuc.dll', 'icu.dll'):
        shutil.copy2(system32 / name, staged / '_internal' / name)
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
        "ftp_ftps_openssl": bundled,
        "cloud_backend": cloud_provenance,
        "dll_sha256": {name: hashlib.sha256((staged / "_internal" / name).read_bytes()).hexdigest()
                       for name in ("libssl-3.dll", "libcrypto-3.dll")},
    }, indent=2) + "\n", encoding="utf-8")
    # Copy rather than move: the inspected DLL may remain loaded on Windows.
    import shutil
    shutil.copytree(staged, destination)
    print(f"Packaged {destination}; {bundled}")
    print('After verification, TLS selection and shortcut updates, run ./cleanup_builds.ps1 -Apply. Keep only the verified current release.')


if __name__ == "__main__":
    main()
