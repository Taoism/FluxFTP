# FluxFTP 0.148.0

## Desktop reliability and organization

- Split window diagnostics, transfer services, cloud account and job handling,
  deployment rollback, automation scripts, and preferences pages into focused
  modules while preserving the existing workflows.
- Improve debug logging setup and exception reporting, and add an F6 packaged
  smoke check for the Options shortcut across the queue, logs, and both panes.
- Expand regression coverage for diagnostics, themes, menus, priorities, and
  skip rules.
- Update Debian packaging to preserve the release runtime and package metadata.

The release was regression checked in named batches of 25 tests. See
`tests.md` for per-test results and the saved JUnit reports.
