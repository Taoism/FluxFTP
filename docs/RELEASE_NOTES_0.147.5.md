# FluxFTP 0.147.5

## Security and build updates

- Rebuild the Linux release with a local CPython 3.13.18 runtime and record its
  interpreter version in the release manifest. The build does not change the
  machine's system Python or PATH.
- Replace the Linux-bundled SQLite 3.45.1 library with verified SQLite 3.54.1;
  its Linux build leaves FTS3 and FTS5 disabled.
- Remove unused Qt PDF and VirtualKeyboard libraries from Linux packages.
- Use local CPython 3.13.18, OpenSSL 3.5.9 and SQLite 3.54.1 for the maintained
  Windows release build.
- Keep Qt Network CVE-2026-116337 under review; no direct FluxFTP call path to
  the affected HTTP response handling was identified.
