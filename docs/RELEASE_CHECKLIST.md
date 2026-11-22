# Release checklist

FluxFTP releases target Windows, Ubuntu and Debian. Publish all three platform
artifacts together for the same source version and tag; do not treat the
Windows-only automation as a complete release.

## Required assets

- Windows x64 portable ZIP and its SHA-256 checksum.
- Ubuntu 24.04-built Linux x86_64 portable `.tar.gz` and its dedicated checksum.
  The supported runtime floor is Ubuntu 24.04 or newer and Debian 13 or newer.
- Debian 13 amd64 native `.deb` installer and its dedicated checksum.

## Before publishing

- Build every artifact from the same source tag after all product text/code
  changes are complete. Rebuild all affected artifacts if any source changes.
- Verify the version in each package matches the release tag.
- Run the packaged Windows startup check, Ubuntu/Linux package smoke check,
  Debian stable smoke check, and Debian installer dependency/install/remove
  checks.
- Verify every checksum against its exact artifact.
- Confirm the release asset list contains all three packages and all checksums.

The GitHub workflow builds the Ubuntu archive and Debian `.deb` from the same
release tag, runs their packaged checks, and attaches those packages and their
checksums. The maintained Windows builder requires separately validated OpenSSL
4.x and 3.5.9 runtime files. Build and smoke-check the Windows package locally,
then attach its ZIP and `SHA256SUMS.txt` to the draft release before publishing.
The release workflow verifies the attached Windows checksum and package tests.

## Latest coordinated release: v0.149.0

The v0.149.0 release contains the Windows x64 ZIP, Ubuntu 24.04-built portable
archive and Debian 13 amd64 installer. Validation run `44991042059` and
published-release run `44991853362` passed all Windows, Ubuntu, Debian and
asset-publishing jobs. All three packages were downloaded after publication;
their SHA-256 values matched the published checksum files. The Windows package
passed startup, credential-vault and F6 shortcut smoke checks before release.
Version 0.149.0 is installed at `D:\Program Files\FluxFTP`; the previous
0.148.0 installation is preserved at `D:\Program Files\FluxFTP-backup-0.148.0`.

| Published artifact | SHA-256 |
|---|---|
| `FluxFTP-0.149.0-windows-x64.zip` | `516c678aa4d41e0a7714facb99b701083ed702fba9eb27cf790657a913f9ff57` |
| `FluxFTP-0.149.0-linux-x86_64-ubuntu24.04.tar.gz` | `31a7d735a3cef763504ba972be25cd4a994933e44b7f2cf02e76a3aec1a9f65a` |
| `fluxftp_0.149.0-1_amd64.deb` | `92716116fdefd0c0381f4676357e6f91501bbf1fa2d9b20955acb70d12e7dfea` |

## Historical coordinated release: v0.148.0

The v0.148.0 release contains the Windows x64 ZIP, Ubuntu 24.04-built portable
archive and Debian 13 amd64 installer. The tagged GitHub Actions workflow
passed all Windows, Ubuntu, Debian and asset-publishing jobs. All three
published package checksums were verified after download. The Windows package
passed startup, credential-vault and F6 shortcut smoke checks before local
installation.

## Historical v0.132.0 release

The v0.132.0 release contains the Windows ZIP, Ubuntu/Debian portable archive,
and Debian `.deb`, with a dedicated checksum for each platform asset. The About
copy and provider-color assets were updated after those binaries were built;
v0.133.0 rebuilds all three packages from one source tag.
