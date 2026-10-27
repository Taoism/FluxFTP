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

## Windows binary release

The downloadable Windows package uses Qt/PySide6/Shiboken 6.11.2 under LGPLv3.
Copyright The Qt Company Ltd. and contributors. Copies of LGPLv3 and GPLv3 are
included under `licenses/`. The shared libraries remain separate under `_internal`;
users may replace compatible libraries or rebuild FluxFTP with modified libraries.
Modification and reverse engineering for debugging library modifications are
permitted. The original FluxFTP application remains MIT-licensed.

Corresponding unmodified library source is available at no charge:

- Qt 6.11.2: https://download.qt.io/archive/qt/6.11/6.11.2/single/qt-everywhere-src-6.11.2.tar.xz
- PySide6/Shiboken 6.11.2: https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.11.2-src/pyside-setup-everywhere-src-6.11.2.tar.xz
- Build instructions: https://doc.qt.io/qtforpython-6/building_from_source/index.html

The unused PDF image plugin and virtual-keyboard plugin/libraries are excluded.
Python's license and third-party component notices, installed Python distribution
notices, and Qt third-party acknowledgments accompany the ZIP. The dependency
inventory records the build environment and can include build/test-only packages.
Windows system ICU libraries are not redistributed; the app uses the system copies.
