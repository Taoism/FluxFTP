# Qt 6.11.3 CVE-2026-116337 backport

Qt's official 6.11 backport consists of two QtBase patches. They bound memory
while reading an HTTP response header block and status line. The release archive
is `qtbase-everywhere-src-6.11.3.tar.xz` from the [official Qt 6.11.3 source
index](https://download.qt.io/official_releases/qt/6.11/6.11.3/submodules/).

## Pinned source and patches

- Qt's published MD5 for the source archive: `fc9557061c74902195b9a87d13dae66c`
- SHA-256 of the downloaded source archive: `46DE0710B594945A5D446AE44354DAA386141C6903C53781B2CA547BC62F8F8F`
- [Header-block-size patch](https://download.qt.io/official_releases/qt/6.11/CVE-2026-116337-qtbase-6.11-0001.diff), SHA-256 `2A5CC5A016BD41B8487555D68CCBBD808A9A418BC457486E9C779D217CB7E31A`
- [Status-line-size patch](https://download.qt.io/official_releases/qt/6.11/CVE-2026-116337-qtbase-6.11-0002.diff), SHA-256 `EA58081D0999D6E3EA0E40277631FEC26589C3E436922BFCFEF7F771C626A4F3`
- [Qt 6.11.3 test adaptation](CVE-2026-116337-qtbase-6.11.3-tests.patch), SHA-256 `C19C450C7167A38490A7235038B5839AB8161BB3AE789F88BB38DCE33A274F92`

The official implementation hunks apply cleanly to QtBase 6.11.3. The upstream
test hunks use an older test-file context, so the test adaptation adds the same
upstream cases to 6.11.3's existing `TestHeaderSocket` and `TestHeaderReply`
helpers. It changes tests only.

From the FluxFTP repository root, set `QTBASE` to the extracted QtBase 6.11.3
source tree and apply the patches:

```sh
git -C "$QTBASE" apply --exclude=tests/auto/network/access/qhttpnetworkreply/tst_qhttpnetworkreply.cpp \
  scripts/qt-patches/6.11.3/CVE-2026-116337-0001.diff \
  scripts/qt-patches/6.11.3/CVE-2026-116337-0002.diff
git -C "$QTBASE" apply scripts/qt-patches/6.11.3/CVE-2026-116337-qtbase-6.11.3-tests.patch
```

## Focused build and test record

Source was built on Ubuntu 24.04 with GCC 13.3, CMake 3.28.3 and Ninja 1.11.1.
Configuration used `-developer-build -nomake examples -no-openssl -no-opengl
-no-gui -no-widgets -no-dbus`; `ninja -j 4 tst_qhttpnetworkreply` built 522
steps successfully.

The Qt test target passed in these named batches (37 data/test cases total):

| Batch | Cases | Result |
| --- | ---: | --- |
| Header and status-line bounds: `headerSizeLimit`, `statusLineSizeLimit` | 8 | 8 passed |
| Header verification and terminators: `parseHeaderVerification`, `parseEndOfHeader` | 20 | 20 passed |
| Header parsing: `parseHeader` | 9 | 9 passed |

The build disabled OpenSSL and GUI modules, and used a debug Qt build. It proves
the patched source compiles and the affected HTTP reader tests pass on Linux. It
is not a drop-in release library for the PySide6 packages. Build compatible
Windows and Linux runtime artifacts and validate packaged TLS/startup workflows
before including the patched QtNetwork library. Keep the QtNetwork packaging
exclusion until that validation is complete; FluxFTP 0.148.0 is unchanged.
