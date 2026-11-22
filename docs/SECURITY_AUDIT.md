# Security audit

Last reviewed: 2026-11-21

This review began with the published FluxFTP 0.147.4 Windows, Ubuntu and Debian
packages. FluxFTP 0.148.0 has since replaced them; its packages were checked
against the findings below, and the maintained Windows package was installed
locally. The review also covers the pinned Windows build environment, current Python advisories, Qt and
miniupnpc vendor advisories, the bundled rclone backend and its Go modules, the
updater, local control API, and FTP/FTPS/SFTP trust handling. It does not replace
continuous review of later releases or a complete version-level inventory of all
native libraries bundled from the Ubuntu build environment.

## Findings and actions

| Component | Reviewed version | Finding | Action/status |
| --- | --- | --- | --- |
| miniupnpc | 2.3.5 in 0.148.0 | CVE-2026-110655 and CVE-2026-110656 describe out-of-bounds reads on crafted UPnP gateway replies through 2.3.3. Enabled active FTP connections can parse LAN gateway responses. | Windows uses a locally built 2.3.5 wheel; Linux resolves to 2.3.5. The Windows build script verifies the official source archive SHA-256 and builds a statically linked CPython 3.13 x64 extension with MSVC. UPnP discovery remains blocked for manually installed versions below 2.3.4. |
| PySide6 / Qt | 6.11.3 in 0.148.0 | CVE-2026-116337 affects Qt Network HTTP response handling. Qt published two 6.11 backport patches. | FluxFTP source has no Qt Network call path. The release packagers exclude QtNetwork and associated network/TLS plugins as a mitigation. Both official patches apply to QtBase 6.11.3, and the adapted QtNetwork reply tests pass in a focused Linux source build. That build has not been validated as a drop-in replacement for the PySide6 Windows/Linux runtimes; keep the packaging exclusion until correctly configured production binaries are built and smoke-tested. The published 0.148.0 packages remain unchanged. Details and hashes: `scripts/qt-patches/6.11.3/README.md`. |
| setuptools | 84.0.0 in Windows build tools | GHSA-xmj3-9gf7-h52w affects versions before 84.1.0 on Windows when installing a crafted legacy wheel. | Pinned to 84.2.1; pip-audit reports no known vulnerabilities in the pinned Python requirements after the update. |
| rclone | 1.75.1-fluxftp.2 | GHSA-m97j-7x65-wh93 affects the Swift provider shipped by FluxFTP: redirects could forward credentials to another host or downgrade HTTPS to HTTP. GHSA-w33h-3qgq-8mv8 also affected stable self-update through 1.75.1. | Rebuilt as 1.76.0-fluxftp.3 from upstream commit `e982ff36668f5130d94b4f70409babb3d964df42`, retaining and adapting FluxFTP's OAuth/OneDrive patch. The release fixes both advisories. |
| Go standard library | 1.26.10 build toolchain | GO-2026-6859 affected `net/http` through Go 1.26.10 and could crash an HTTP/1 server handling oversized request bodies concurrently. | Rebuilt the backend with Go 1.26.11. `govulncheck ./...` then reported no reachable vulnerabilities. |
| OpenSSL 3.x / 4.x | 3.5.9 / 4.1.0 in the published Windows package | OpenSSL lists fixes for eight Low-severity advisories in 3.5.10 / 4.1.1, but those releases were not yet available on 2026-11-20: CVE-2026-107493, CVE-2026-124709, CVE-2026-88822, CVE-2026-95691, CVE-2026-35190, CVE-2026-88823, CVE-2026-95692 and CVE-2026-107496. | Keep the latest published versions pending upstream security releases. Source review found no direct FluxFTP calls to the affected PEM stack, CMS, QUIC, AES-GCM-SIV or Suite B server APIs. The shared TLS library remains bundled, so this is not marked fixed; upgrade both runtime branches promptly when vendor releases are available, then rerun TLS and updater coverage. |
| SQLite | 3.54.1 in 0.148.0 Windows, Ubuntu and Debian | The 0.147.4 Linux tarball and Debian package contained Ubuntu SQLite 3.45.1-1ubuntu2.9, which Canonical marks affected by CVE-2026-119371, CVE-2026-119372 and CVE-2026-119373. | FluxFTP 0.147.5 replaced those packages. Windows uses the official SQLite 3.54.1 x64 DLL after verifying archive SHA3-256 `c2d09c8babb9399277055c057c97a53f1ffd7c5f11d0c1f2118ffdfa39f2daa4`. Linux release builds compile 3.54.1 from the official autoconf archive, record source and library hashes in `sqlite-provenance.json`, and package the library under `_internal`. Ubuntu and Debian builds and smoke checks passed. |
| ICU | Not shipped in the published Windows package | The build staging tree can contain ICU DLLs, but `scripts/package_windows_release.py` removes `icu.dll` and `icuuc.dll` before creating the public archive. The installed 0.147.5 package also contains neither file. | No shipped ICU DLL finding for 0.147.5. Keep checking the final staged package after packaging changes. |
| Qt optional modules | Qt 6.11.3 | The 0.147.4 Linux tarball and Debian package included unused duplicate Qt PDF and VirtualKeyboard libraries. QML/Quick libraries also ship although FluxFTP source does not import QML/Quick. | FluxFTP 0.148.0 prunes the nested and root-level PDF/VirtualKeyboard copies in Linux packages. In an isolated copy of the 0.148.0 Windows package, removing QtQml, QtQmlMeta, QtQmlModels, QtQmlWorkerScript, QtQuick and QtNetwork DLLs/bindings plus Qt Network plugins preserved packaged `--smoke-test` and `--f6-smoke-test` (exit 0). This is focused Windows evidence only; validate the packager changes in full release candidates. QtNetwork import failure is caught by PySide6's Windows initializer; QtTest remains needed by the hidden F6 smoke hook. |
| Release notices and dependency inventory | 0.148.0 Windows and Linux packages | The Windows `dependency-inventory.json` is a build-environment license inventory, not an SBOM of files actually bundled by PyInstaller. The Linux inventory covers Python distributions only and explicitly omits native libraries and other bundled files. | Notices identify Qt 6.11.3 and link matching source archives. Windows runtime inventory records Python 3.13.18, OpenSSL 4.1.0/3.5.9, SQLite 3.54.1, rclone v1.76.0-fluxftp.3 / Go 1.26.11 and miniupnpc 2.3.5. Linux provenance records rclone and SQLite, and the archive contains Qt 6.11.3 plus bundled Ubuntu ELF libraries. A complete file-level shipped-component SBOM and version mapping for all bundled Linux native libraries remain future work. |

## Other reviewed controls

- The JSON API binds to `127.0.0.1`, requires a 256-bit random bearer token,
  compares it in constant time, limits request bodies, and does not expose the
  listener on a public interface.
- The bundled rclone control daemon also binds only to `127.0.0.1`, uses random
  Basic Auth credentials, and disables the rclone web UI. The separate advisory
  for oversized RC requests reproduces memory exhaustion against a listener
  without HTTP authentication; FluxFTP configures Basic Auth and does not expose
  this service on a public interface.
- The updater restricts source and redirect hosts to HTTPS GitHub endpoints,
  bounds downloads, checks release checksums, uses private Windows staging,
  and rechecks the digest before install. Debian updates use private staging
  and digest checks.
- FTPS defaults to CA and hostname validation. Explicit certificate pins are
  exact SHA-256 leaf pins and still require a current certificate. SSH rejects
  changed host keys and requires explicit first-use trust.
- No new confirmed application-level issue was found in this targeted review.

## Verification and remaining work

- `pip-audit -r requirements-windows.txt` reported no known vulnerabilities
  when rerun with the locally built CPython 3.11 miniupnpc 2.3.5 wheel supplied
  by direct file URL. The scan used the pinned requirements and current audit
  service; it does not cover Qt's native advisories.
- `tests/test_connection_controls.py`: 19 passed, including a regression test
  that confirms vulnerable parsers are never invoked and the patched version is
  accepted.
- Built and imported the CPython 3.13 x64 wheel on Windows; also built a CPython
  3.11 wheel for the local test environment. All 5 upstream UPnP response parser
  fixtures passed against miniupnpc 2.3.5.
- Patched rclone tests: 2 passed (`lib/oauthutil` callback binding and OneDrive
  default-drive selection); focused cloud regression batch: 20 passed.
- `govulncheck ./...` against the patched rclone v1.76.0 source built with Go
  1.26.11: no reachable vulnerabilities. Govulncheck reported one vulnerability
  in a required module but found no call path from the rclone code to it.
- The 0.147.5 Windows, Ubuntu, and Debian release builds were published;
  Windows and Linux packaged-app smoke checks and platform release workflows passed.
- Historical 0.147.2 through 0.147.5 builds are superseded by 0.148.0. The
  published 0.148.0 builds remain unchanged by the current packaging hardening.
- The published 0.148.0 Windows archive and local installation were inspected. Their
  Qt DLL inventory contains Core, Gui, Network, OpenGL, QML, Quick, Svg, and
  Widgets; it does not contain ICU, PDF, or VirtualKeyboard DLLs.
- The published 0.148.0 Linux tarball and Debian package were inspected. Both
  contain Qt 6.11.3, miniupnpc 2.3.5 and SQLite 3.54.1. The prior 0.147.4
  packages contained affected SQLite 3.45.1-1ubuntu2.9 and unused Qt PDF and
  VirtualKeyboard copies; the 0.147.5 release replaces them with patched packages.
- OpenSSL advisories are no longer deferred: source review found no direct
  FluxFTP calls to the affected APIs, but shared TLS libraries remain bundled.
  Upgrade the Python and Qt TLS runtime branches when OpenSSL publishes fixed
  releases. Continue validating the Qt Network mitigation/backport in release
  candidates and mapping bundled Linux native libraries to exact versions.
- The Linux SQLite 3.54.1 source build passed against extracted copies of both
  published 0.148.0 package formats. Source hash verification, runtime version, disabled
  FTS3/FTS5 compile options, library hash/provenance agreement, and packaged
  `--smoke-test` passed for both. The public 0.148.0 Linux packages carry the
  verified SQLite 3.54.1 library.
- The Linux bundle includes OpenSSL shared libraries, but their exact package
  versions and source mapping have not yet been reconciled against the Windows
  advisory review. A full native-library inventory and review of other bundled
  Ubuntu libraries are still open.

## Sources

- [CVE-2026-110655](https://osv.dev/vulnerability/CVE-2026-110655)
- [CVE-2026-110656](https://osv.dev/vulnerability/CVE-2026-110656)
- [Official miniupnpc 2.3.5 source archive](https://miniupnp.tuxfamily.org/files/miniupnpc-2.3.5.tar.gz)
- [Qt known vulnerabilities](https://wiki.qt.io/List_of_known_vulnerabilities_in_Qt_products)
- [Qt 6.11.3 release](https://www.qt.io/blog/qt-6.11.3-released)
- [OpenSSL downloads and current supported releases](https://www.openssl-library.org/source/)
- [OpenSSL 3.5 vulnerabilities](https://www.openssl-library.org/news/vulnerabilities-3.5/)
- [OpenSSL 4.1 vulnerabilities](https://www.openssl-library.org/news/vulnerabilities-4.1/)
- [GHSA-xmj3-9gf7-h52w](https://github.com/advisories/GHSA-xmj3-9gf7-h52w)
- [GHSA-m97j-7x65-wh93](https://github.com/rclone/rclone/security/advisories/GHSA-m97j-7x65-wh93)
- [GHSA-w33h-3qgq-8mv8](https://github.com/rclone/rclone/security/advisories/GHSA-w33h-3qgq-8mv8)
- [GO-2026-6859](https://pkg.go.dev/vuln/GO-2026-6859)
- [SQLite download page and official binary hashes](https://sqlite.org/download.html)
- [SQLite CVE list](https://sqlite.org/cves.html)
- [Ubuntu SQLite security notice USN-8480-1](https://ubuntu.com/security/notices/USN-8480-1)
- [Canonical OSV record for CVE-2026-119373](https://osv.dev/vulnerability/UBUNTU-CVE-2026-119373)
- [Qt vulnerability list](https://wiki.qt.io/List_of_known_vulnerabilities_in_Qt_products)
- [Qt advisory CVE-2026-116337](https://www.qt.io/blog/security-issue-cve-2026-116337)
- [PySide6 releases on PyPI](https://pypi.org/project/PySide6/)
- [GHSA-f8p4-8g4j-q3m2](https://github.com/rclone/rclone/security/advisories/GHSA-f8p4-8g4j-q3m2)
