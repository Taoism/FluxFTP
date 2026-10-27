# Third-party components

The root MIT license covers FluxFTP's original code and documentation. It does not
replace dependency licenses. No third-party executable, DLL, Python environment,
private key or runtime adapter is committed here.

Direct dependencies are declared in `pyproject.toml`; the maintained Windows
environment snapshot is `requirements-windows.txt`. They include PySide6/Qt,
Paramiko, cryptography, keyring and miniupnpc. Test/build dependencies include
pytest, pyftpdlib, pyOpenSSL and PyInstaller. Consult the license metadata and
license files shipped with the exact installed distributions, including transitive
dependencies, when building a distributable package.

- **rclone:** copyright Nick Craig-Wood and contributors, MIT. Upstream source:
  <https://github.com/rclone/rclone>. The notice is preserved in
  `licenses/rclone.txt`; the FluxFTP patch and pinned source revision are documented
  in `docs/BUILDING.md`.
- **OpenSSL:** the maintained release uses OpenSSL 3.x and an optional 4.x adapter.
  The Apache-2.0 license text is preserved in `licenses/OpenSSL.txt`. Runtime
  binaries and adapter assets are not included in this source tree.
- **Python and Qt runtime files:** supplied by the chosen build environment.
  Retain their accompanying notices and satisfy their distribution requirements
  when publishing binary packages. In particular, the root MIT license does not
  relicense Qt/PySide6.

Screenshots in `docs/screenshots` were rendered from FluxFTP with synthetic demo
data. No FTP Rush screenshots, archives or reference assets are included.
