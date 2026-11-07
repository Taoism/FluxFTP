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

The automated GitHub workflow builds the Windows ZIP, Ubuntu portable archive,
and Debian `.deb` from the same release tag, runs the packaged checks, produces
platform-specific SHA-256 files, and attaches all assets to that GitHub Release.

## Previous v0.132.0 release

The v0.132.0 release contains the Windows ZIP, Ubuntu/Debian portable archive,
and Debian `.deb`, with a dedicated checksum for each platform asset. The About
copy and provider-color assets were updated after those binaries were built;
v0.133.0 rebuilds all three packages from one source tag.
