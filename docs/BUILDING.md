# Building and testing FluxFTP

## Portable Ubuntu/Debian Linux release

The x86_64 portable archive is built on Ubuntu 24.04 LTS and tested on Debian
13 stable. The packaged application recorded a `GLIBC_2.38` symbol floor; it is
intended for Ubuntu 24.04 or newer and Debian 13 or newer. Debian 12 and older
Ubuntu releases do not meet that floor. Keep the Fedora build separate because
its glibc baseline and native libraries differ.

Install the build prerequisites on Ubuntu 24.04:

```bash
sudo apt update
sudo apt install python3 python3-venv python3-pip python3-dev build-essential \
  pkg-config git golang-go binutils libxcb-cursor0 libxkbcommon-x11-0 \
  libxcb-xinerama0 libxcb-icccm4 libxcb-keysyms1 libxcb-image0 \
  libxcb-render-util0 libxcb-randr0 libxcb-shape0 libxcb-xfixes0 \
  libgl1 libegl1 libdbus-1-3
bash scripts/build_linux_release.sh
```

The script creates an isolated Python environment, builds the pinned rclone
source revision with its FluxFTP OAuth/OneDrive patch, runs the focused rclone
tests, packages the GUI with PyInstaller and writes a `.tar.gz` plus
`SHA256SUMS.txt` under `dist/`. Go bootstraps the Go version required by the
pinned rclone source, so the build needs network access. The archive contains
the native rclone backend, runtime provenance, dependency inventory, license
notices, documentation and per-file checksums. It does not contain saved
accounts or credentials.

To verify the archive on Debian 13:

```bash
sha256sum -c SHA256SUMS.txt
tar -xzf FluxFTP-*-linux-x86_64-ubuntu24.04.tar.gz
cd FluxFTP-*-linux-x86_64-ubuntu24.04
QT_QPA_PLATFORM=offscreen ./FluxFTP --smoke-test
./_internal/cloud_tools/rclone version
```

For a normal desktop session, provide the host's X11/XCB libraries, an active
user D-Bus session and a Secret Service implementation for the encrypted
credential vault. SSHFS mounts also require the host's FUSE/SSHFS packages.
The package is unsigned; publishing a production release requires the normal
release approval and asset review.

To build the native Debian installer from the Ubuntu-built bundle, use the
FluxFTP source checkout on Debian 13 and point `--bundle` to the transferred
`FluxFTP-0.133.0-linux-x86_64-debian` application folder:

```bash
python3 scripts/package_deb.py --bundle /path/to/FluxFTP-0.133.0-linux-x86_64-debian
```

The resulting `.deb` declares the Debian runtime dependencies, installs the
application under `/opt/fluxftp`, and registers a desktop entry and `fluxftp`
launcher. The package builder extracts the package and runs the packaged GUI
smoke check and bundled rclone version check before reporting success.

## Source environment

Use Windows x64 and Python 3.13 for the primary development path. The project
declares Python 3.11 or newer; the maintained release was validated with a custom
Python 3.13.15 / OpenSSL 3.5.8 runtime. That custom runtime is not downloaded or
installed by this repository.

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m fluxftp --smoke-test
```

`requirements-windows.txt` records the maintained release's Python dependency
versions. It is a reference snapshot, not a replacement for installing the project.
No user settings, binaries, runtime caches or credentials are included.

## Cloud backend

Cloud features expect `runtime/rclone/rclone.exe` on Windows (`rclone` elsewhere).
The maintained backend is rclone v1.75.1 with FluxFTP callback-port and OneDrive
default-drive fixes. The patch is included as `scripts/rclone-fluxftp.patch`.
Install Git and Go (the maintained backend used Go 1.26.10), then run:

```powershell
.\.venv\Scripts\python.exe scripts/build_cloud_backend.py
```

The script checks out pinned upstream commit
`687d264b689b8c49a67e2e52a8a5e0caa01c04ce`, verifies the included patch digest,
applies it to a new checkout, runs the patch's tests and builds the backend.
Git/Go need network access to fetch source and modules. It refuses to reuse an
existing source checkout; use `--source` with a fresh directory to rebuild.
Nothing is published. Backend files and source downloads are ignored by Git.

An upstream rclone binary may work for many operations, but does not contain the
OneDrive fixes or satisfy the backend-specific regression checks.

## Tests

Run the source-only checks used by the lightweight CI workflow:

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
.\.venv\Scripts\python.exe -m pytest tests/test_verification_reports.py tests/test_folder_sync.py tests/test_tools_menu.py tests/test_deployments.py tests/test_trust_service.py tests/test_queue_storage_service.py tests/test_protocol_adapter.py tests/test_job_model.py tests/test_queue_engine.py -q
```

For full regression testing, build the cloud backend first:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Tests use temporary data and loopback FTP/FTPS/SFTP servers. Local listening ports
must be permitted. A few Windows TLS integration checks require the maintained
runtime fixtures; absent fixtures are explicitly skipped in this public copy.
Cloud tests require the patched backend. The CI job runs a focused source suite,
not full provider or production-server certification.

## Development executable

This path uses PyInstaller and the active Python environment, without the custom
runtime-switching hook. Cloud support is optional:

```powershell
.\.venv\Scripts\python.exe scripts/build_development.py
# Or include the locally built backend:
.\.venv\Scripts\python.exe scripts/build_development.py --cloud
```

The output is `dist/FluxFTP-dev/FluxFTP.exe` on Windows. Keep the entire folder
together, including `_internal`; the executable alone is not a portable package.
The build includes the README, user documentation and license notices. No signing,
uploading or publication occurs. Review dependency notices before redistribution.

## Maintained release builder

`build_windows.py` is retained as the release recipe. It intentionally requires
Python 3.13.15 / OpenSSL 3.5.8, a verified patched rclone backend and a validated
CPython/OpenSSL 4 adapter under `runtime/ssl4-adapter`. Those binary inputs are not
present in this repository, so it is not the fresh-checkout build command.
`release_tools.py` contains separate native packaging/signing helpers. Signing
identities, certificates and private keys are never stored in this repository.

Before a public binary release, complete package smoke tests, dependency/license
review, artifact checksums and release notes. GitHub publishing is a separate step.

## Automated public Windows releases

Publishing a GitHub Release starts `.github/workflows/public-windows-release.yml`.
The tag must match the version in `pyproject.toml` (for example, `v0.133.0`).
The workflow builds the pinned, patched rclone backend, runs the release-focused
tests and source smoke check, builds and smoke-checks the Windows application,
then prepares the public ZIP with license notices, a dependency inventory, a
source-commit manifest and `SHA256SUMS.txt`. It uploads the ZIP and checksum file
to the published GitHub Release. The workflow can replace those two assets when
rerun; it does not replace the release notes or tag.

Before a public release, use **Actions → Public Windows release → Run workflow**
with the candidate tag and leave **publish_assets** disabled. This produces a
30-day workflow artifact without changing a GitHub Release. Review the package,
manifest and smoke results first. The published-release trigger attaches assets
automatically; a deliberate manual run can also attach them to an existing
release by enabling **publish_assets**.

The workflow currently produces an unsigned Windows package with the pinned
dependency set and the hosted Python 3.13.15 TLS runtime. It does not use the
separately maintained custom OpenSSL 3.5.8 / OpenSSL 4 adapter package path, and
should not replace a validated package until that runtime difference is reviewed.
It does not claim to sign or notarize Windows, macOS or Linux releases.
Code-signing identities and the required non-Windows build hosts remain separate
prerequisites.
