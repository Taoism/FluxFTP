"""Build pinned rclone with FluxFTP's callback and OneDrive fixes."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
COMMIT = 'e982ff36668f5130d94b4f70409babb3d964df42'
PATCH_SHA256 = '779c4bdfe66b6e5f122a57985a122eca96c724b4a63f4656103c9e5ef7ffe0cd'
VERSION = 'v1.76.0-fluxftp.3'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ROOT / 'build/rclone-source',
                        help='Fresh directory for the upstream checkout')
    args = parser.parse_args()
    source = args.source.resolve()
    if source.exists(): parser.error('Source directory already exists; choose a fresh --source directory.')
    go, git = shutil.which('go'), shutil.which('git')
    if not go or not git: parser.error('Install Git and Go, and add them to PATH.')
    patch = ROOT / 'scripts/rclone-fluxftp.patch'
    if hashlib.sha256(patch.read_bytes()).hexdigest() != PATCH_SHA256:
        parser.error('Included patch checksum mismatch.')
    source.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([git, 'clone', '--no-checkout', 'https://github.com/rclone/rclone.git', str(source)], check=True)
    subprocess.run([git, 'checkout', '--detach', COMMIT], cwd=source, check=True)
    actual = subprocess.check_output([git, 'rev-parse', 'HEAD'], cwd=source, text=True).strip()
    if actual != COMMIT: raise RuntimeError('Unexpected upstream revision')
    subprocess.run([git, 'apply', '--check', str(patch)], cwd=source, check=True)
    subprocess.run([git, 'apply', str(patch)], cwd=source, check=True)
    # rclone's go.mod pins its minimum Go toolchain. Let Go bootstrap that
    # pinned toolchain when the distro compiler is older than that baseline.
    environment = dict(os.environ, CGO_ENABLED='0', GOTOOLCHAIN='auto')
    for package, pattern in (('./lib/oauthutil', '.'), ('./backend/onedrive', 'TestFluxFTP')):
        subprocess.run([go, 'test', package, '-run', pattern, '-count=1'], cwd=source, env=environment, check=True)
    destination = ROOT / 'runtime/rclone'
    destination.mkdir(parents=True, exist_ok=True)
    output = destination / ('rclone.exe' if sys.platform == 'win32' else 'rclone')
    subprocess.run([go, 'build', '-trimpath', '-ldflags',
        '-s -w -X github.com/rclone/rclone/fs.Version=' + VERSION,
        '-o', str(output), '.'], cwd=source, env=environment, check=True)
    subprocess.run([str(output), 'version'], check=True)
    shutil.copyfile(patch, destination / 'fluxftp-oauth-port.patch')
    provenance = dict(version=VERSION, upstream_version='v1.76.0',
        source_url='https://github.com/rclone/rclone',
        source_commit=COMMIT, patch_sha256=PATCH_SHA256,
        executable_sha256=hashlib.sha256(output.read_bytes()).hexdigest(),
        onedrive_callback='http://localhost:18342/',
        go=subprocess.check_output([go, 'env', 'GOVERSION'], cwd=source,
            env=environment, text=True).strip())
    (destination / 'provenance.json').write_text(json.dumps(provenance, indent=2) + '\n', encoding='utf-8')
    print('Built patched cloud backend; no files were published.')


if __name__ == '__main__':
    main()
