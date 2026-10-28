# Building and testing FluxFTP

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
