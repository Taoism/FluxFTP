# FluxFTP 0.146.0

## Layout and workspaces

- Persist the customized panel arrangement, window positions, and resized
  columns across launches.
- Give each workspace its own transfer queue and log panels.
- Add toolbar visibility controls for workspace tabs and connect them to the
  current docked layout.
- Keep layout commands and popup menu designers under Tools → Layout.

## Transfers and queue

- Order files within each queued folder by size and directory while preserving
  the user's manual order of top-level queue items.
- Preserve multi-part sort ranks in the disk-backed queue and keep cloud folder
  planning, filtering, and recovery in sync with those ranks.
- Report aggregate transfer totals in consistent size, duration, and bandwidth
  fields, while retaining per-file progress.
- Keep a manually hidden transfer queue hidden when a transfer starts.

## Security and reliability

- Stage Windows self-updates in a randomly named, access-controlled directory
  and recheck the verified archive digest before installation.
- Harden Debian update staging and checksum verification.
- Fix queue ordering and layout visibility regressions found in focused release
  checks.

See the release assets for Windows, Ubuntu, and Debian packages and SHA-256
checksums.
