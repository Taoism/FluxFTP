# Security audit

Last reviewed: 2026-11-18

This is an initial dependency and application security review for the published
FluxFTP 0.147.3 Windows package. It covers the pinned Windows build environment,
current Python advisories, Qt and miniupnpc vendor advisories, the bundled
rclone backend and its Go modules, the updater, local control API, and FTP/FTPS/SFTP trust
handling. It does not replace continuous review of later releases or a full
audit of every native library.

## Findings and actions

| Component | Reviewed version | Finding | Action/status |
| --- | --- | --- | --- |
| miniupnpc | 2.3.3 in Windows release | CVE-2026-110655 and CVE-2026-110656 describe out-of-bounds reads on crafted UPnP gateway replies through 2.3.3. Enabled active FTP connections can parse LAN gateway responses. | Windows now requires a locally built 2.3.5 wheel. The build script downloads the official source archive, verifies its pinned SHA-256, and builds a statically linked CPython 3.13 x64 extension with MSVC. UPnP discovery remains blocked for manually installed versions below 2.3.4. |
| PySide6 / Qt | 6.11.2 in 0.147.2 | Qt lists several fixes in 6.11.3, including CVE-2026-95620 and other Qt Core/Network issues. Qt 6.12.0 was excluded because it remains affected by later advisories. | Windows build pins all PySide6 components and shiboken6 to 6.11.3. The app does not use QWebSocket or QNetworkAccessManager directly, and its packaged Windows inventory contains no Qt WebSockets DLL. Recheck Qt advisories and the PySide6 release channel before each release. |
| setuptools | 84.0.0 in Windows build tools | GHSA-xmj3-9gf7-h52w affects versions before 84.1.0 on Windows when installing a crafted legacy wheel. | Pinned to 84.2.1; pip-audit reports no known vulnerabilities in the pinned Python requirements after the update. |
| rclone | 1.75.1-fluxftp.2 | GHSA-m97j-7x65-wh93 affects the Swift provider shipped by FluxFTP: redirects could forward credentials to another host or downgrade HTTPS to HTTP. GHSA-w33h-3qgq-8mv8 also affected stable self-update through 1.75.1. | Rebuilt as 1.76.0-fluxftp.3 from upstream commit `e982ff36668f5130d94b4f70409babb3d964df42`, retaining and adapting FluxFTP's OAuth/OneDrive patch. The release fixes both advisories. |
| Go standard library | 1.26.10 build toolchain | GO-2026-6859 affected `net/http` through Go 1.26.10 and could crash an HTTP/1 server handling oversized request bodies concurrently. | Rebuilt the backend with Go 1.26.11. `govulncheck ./...` then reported no reachable vulnerabilities. |
| OpenSSL 3.x / 4.x | 3.5.9 / 4.1.0 in the published Windows package | Both remain the latest releases listed by OpenSSL. Eight Low-severity advisories published on 2026-11-10 and 2026-11-17 affect these versions: CVE-2026-107493 (PEM stack use-after-free), CVE-2026-124709, CVE-2026-88822, CVE-2026-95691 (QUIC issues), CVE-2026-35190 (Suite B TLS server key-share processing), CVE-2026-88823 and CVE-2026-95692 (AES-GCM-SIV edge cases), and CVE-2026-107496 (CMS encryption with a malformed EC group). Fixes are listed for 3.5.10 / 4.1.1, which are not yet listed as released. FluxFTP uses OpenSSL for client-side FTP/FTPS; source search found no direct use of the affected PEM or CMS APIs, QUIC APIs, AES-GCM-SIV, or Suite B server configuration. This reduces apparent reachability but does not establish that every bundled consumer avoids the affected calls. | Keep 3.5.9 and 4.1.0 as the latest published builds, track these advisories as open upstream exposure, and update both channels promptly when fixed releases are published. Recheck the official release page before packaging. |
| SQLite | 3.50.4 in the previous Windows package | SQLite 3.50.4 predates fixes for multiple vulnerabilities listed in the SQLite CVE page, including issues fixed in 3.54.0. The app uses SQLite for transfer history and queue storage. | Updated the Windows build runtime to the official SQLite 3.54.1 x64 DLL after verifying archive SHA3-256 `c2d09c8babb9399277055c057c97a53f1ffd7c5f11d0c1f2118ffdfa39f2daa4` against SQLite's download page. The build now rejects older SQLite runtimes and records version and DLL SHA-256 in `runtime-version.json`. The upstream Windows DLL is unsigned. |
| ICU | Not shipped in the published Windows package | The build staging tree can contain ICU DLLs, but `scripts/package_windows_release.py` removes `icu.dll` and `icuuc.dll` before creating the public archive. The installed 0.147.3 package also contains neither file. | No shipped ICU DLL finding for 0.147.3. Keep checking the final staged package after packaging changes. |
| Qt optional modules | Qt 6.11.3 | The public Windows package strips Qt PDF and VirtualKeyboard DLLs/plugins. QML/Quick and other Qt modules remain in the package; this audit has not yet confirmed which are required at runtime. | Confirm the remaining Qt module set and remove unused modules in a separate packaging change with focused smoke coverage. |
| Release notices and dependency inventory | Published Windows package 0.147.3 | The published `THIRD_PARTY_NOTICES.md` describes Qt 6.11.2 while the package contains Qt 6.11.3. Its `dependency-inventory.json` lists every installed builder-environment distribution, including test and build tools; it is a license inventory, not an SBOM of modules actually bundled by PyInstaller. | Source notices now identify Qt 6.11.3 and link its matching source archives. Newly built Windows inventories will identify their broad build-environment scope; Linux inventories will identify their Python runtime dependency scope and native-library limitation. These source fixes reach users with the next package. A complete shipped-component SBOM remains future work. |

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
- The 0.147.3 Windows, Ubuntu, and Debian release builds have been published;
  the Windows packaged-app smoke check and platform release workflows passed.
- Historical 0.147.2 remains vulnerable to the findings recorded for that
  release; it is superseded by 0.147.3 and is not the current installed build.
- The published Windows archive and local installation were inspected. Their
  Qt DLL inventory contains Core, Gui, Network, OpenGL, QML, Quick, Svg, and
  Widgets; it does not contain ICU, PDF, or VirtualKeyboard DLLs.
- A temporary Windows package was generated from the release staging tree and
  inspected: its inventory scope label and Qt 6.11.3 source notices are correct.
  The temporary archive was removed after validation. The already published
  0.147.3 archive retains its original notice and inventory until a later release.
- OpenSSL advisory reachability review is deferred for a later pass. Continue
  auditing remaining native dependencies and other platform packages.

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
- [GHSA-f8p4-8g4j-q3m2](https://github.com/rclone/rclone/security/advisories/GHSA-f8p4-8g4j-q3m2)
