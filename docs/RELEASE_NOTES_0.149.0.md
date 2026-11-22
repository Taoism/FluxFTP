# FluxFTP 0.149.0

## Deployment and workspaces

- Compare deployment revisions with changed-file lists and text diffs for
  configuration and website files.
- Save and switch named workspace presets with sites, folder pairs, tabs and
  layout. Presets exclude credentials and confirm before replacing open tabs or
  queued items.
- Import GitHub Actions and GitLab pipeline artifacts with one-time OAuth device
  sign-in. Tokens can be remembered in the encrypted credential vault, and
  expiring GitHub tokens refresh automatically.

## Automated trading

- Add rule-based folder discovery, a durable trade ledger, duplicate
  suppression, recovery controls, bounded retries, transfer verification and
  queue limits. Trading remains off until explicitly enabled.
- Support built-in TLS-first IRC and loopback bridges for HexChat and AdiIRC.
- Add configuration previews, manual trade submission and per-route controls.

## Packaging and maintenance

- Tighten Windows and Linux package contents by omitting unused Qt components.
- Update dependency and runtime audit records and retain the prior
  OpenSSL/SQLite release checks.

Focused regression batches and packaged release checks are recorded in
`tests.md` and `docs/RELEASE_CHECKLIST.md`.
