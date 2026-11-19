# FluxFTP 0.147.4

## Security and release metadata

- Update the Qt/PySide6/Shiboken release notices and source links to 6.11.3.
- Label generated dependency inventories with their scope. The Windows file
  inventories the build environment; the Linux file inventories Python runtime
  dependencies and calls out that native libraries are not included.
- Refresh the Python dependency audit. `pip-audit` reported no known
  vulnerabilities with the locally built miniupnpc 2.3.5 wheel supplied.

OpenSSL remains on the current 3.5.9 and 4.1.0 releases. The newly published
low-severity advisories and reachability follow-up are tracked in
`docs/SECURITY_AUDIT.md` for a later review.
