# FluxFTP 0.140.0

## Fixes

- Bundled keyring's dynamically loaded OS credential backends and package metadata
  in frozen builds so encrypted password storage can initialize its system vault.
- Added a packaged Windows credential-vault smoke check to the release workflow.
- Disabled FTP NAT/Masq IP handling by default, including for existing saved sites.
- Added a Parent directory row to non-root listings and show server-reported
  directory sizes immediately, using whole-number size units.
- Save and restore dock-panel and floating-panel positions across launches.
- Let the title-bar close button close FluxFTP while requesting cancellation of
  active transfers.

See the release assets for Windows, Ubuntu and Debian packages and SHA-256
checksums.
