# FluxFTP 0.141.0

## Fixes

- Validate FTPS connections with hostname and certificate checks, or with an
  explicitly saved certificate pin. Rejected certificates are retrieved without
  sending credentials so users can review them before deciding to trust a pin.
- Simplify FTP feature and credential-command parsing and improve handling of
  private network addresses.
- Use the protocol-required MD5 checksum for Google Drive uploads without
  treating it as a security hash.
- Correct folder-sync preview pagination and remove the sample local bind address.

## SonarQube

- Project: `codex_fluxftp_publish`
- Quality Gate: **OK**
- Open issues: **0**; new findings: **0**
- Security hotspots awaiting review: **0**

See the release assets for Windows, Ubuntu and Debian packages and SHA-256
checksums.
