# TODO

- [ ] Build and publish an x86_64 Linux package for Ubuntu/Debian. Build on the
  Ubuntu LTS baseline as a portable `.tar.gz`, verify the resulting glibc/library
  floor, test on Debian stable, include the Linux rclone backend, and run packaged
  GUI smoke checks.
- [ ] Build and publish an x86_64 Fedora 45 portable `.tar.gz`. Fedora 45 WSL is
  installed with Python 3.15 and glibc 2.44; set up the tested Python 3.13/PySide6
  packaging environment, pip, Git and Go for the patched rclone backend, then run
  packaged GUI smoke checks. Its glibc baseline is separate from the Ubuntu/Debian
  package.

- [x] When both FXP site roles are Auto, try destination-passive first and safely
  reverse to source-passive if data-route setup fails before the transfer begins.
- [x] Add FTP Rush-style toolbar shortcuts for refreshing both panes, queue controls,
  log and transfer window visibility, and the FTP command terminal.
- [x] Give saved-site folders and each supported protocol/cloud provider distinct
  icons in Site Manager.

- [x] Before resuming, compare destination content with the source where supported. Local prefixes use SHA-256; full files use a shared native hash where available, with size/mtime as a local fallback. Unknown or changed content is shown in the conflict dialog; changed prefixes disable Resume. The dialog waits for a choice and says it has no automatic timeout.

- [x] Show folder icons in both the remote and local folder views.

## Desktop product backlog

- [x] Add a command palette for commands, sites, folders, deployment profiles, and workspaces. Use Ctrl+Shift+P; it also saves and applies workspace presets.
- [ ] Add remote search filters for filename, size, and date, with download and
  navigation actions.
- [ ] Add optional upload-on-save for remote editing, with conflict detection and
  backups.
- [ ] Add transfer preflight checks for available space, destination access, and
  path conflicts.
- [ ] Add reusable queue recipes with endpoints, filters, and verification policies.
- [ ] Add actionable Windows completion and failure notifications.
- [ ] Add a connection health dashboard for latency, throughput, retries, and failures.
- [ ] Add GitLab pipeline-artifact deployments with commit and pipeline provenance.
- [ ] Add deployment revision comparison and text diffs.
- [ ] Add a quick switcher for workspace presets.
- [ ] Continue real-network active FTP and production protected-FXP interoperability
  testing.

## Release and distribution backlog

- [x] Publish the GitHub repository with versioned downloadable releases and SHA-256
  checksums.
- [ ] Automate repeatable public release builds, maintain complete dependency notices,
  and sign production packages.

- Android companion initial client: Android app ID, encrypted bearer-token storage, adaptive phone/tablet queue controls, transfer submission against saved sites, and loopback-only HTTP policy are implemented in `android/`. Phone and tablet API 36.1 AVD profiles are installed. Remaining: build a mobile-designed 1:1 feature mirror of the desktop client. The Android app is a remote control and display client; all server connections, saved credentials, browsing, and transfer work must execute on the end user's desktop host. The phone must not connect directly to FTP/SFTP/cloud servers or receive their credentials. Add full site/file browsing and feature parity to the desktop API and companion, secure pairing/tunnel setup, and a release signing key.

- [ ] Secure remote connectivity roadmap (rough engineering estimates for one
  experienced engineer; Android full-feature parity is estimated separately):
  - [ ] Tailscale proof of concept — 2–4 days. Use the existing loopback JSON API,
    route it to the tailnet with Tailscale Serve, and confirm pairing and access over
    mobile data, including relay fallback. No router or port-forwarding setup for users.
  - [ ] Productized Tailscale setup — 1–3 weeks. Add guided host/device pairing,
    sign-in and connection status, reconnect/recovery, device revocation, safe token
    handling, and Windows/Android workflow checks. Keep the API off public interfaces.
  - [ ] Optional SSH tunnel inside the encrypted tailnet — about 2–5 additional days
    if Tailscale Serve is insufficient. Forward only to the desktop's loopback API and
    handle host-key verification, tunnel startup/reconnect, and shutdown.
  - [ ] Standalone WireGuard alternative — 4–8 weeks. Requires peer enrollment and key
    lifecycle, coordination/discovery, NAT traversal, relay fallback, recovery and
    revocation, plus Windows/Android packaging and mobile-data validation. This is an
    alternative to the Tailscale track, not an additional required phase.

- [x] Add an authenticated loopback JSON API for scripts and clients, with queue inspection, transfer submission, and queue controls. Its bearer token is stored in FluxFTP's encrypted credential vault; remote access requires an authenticated SSH/VPN tunnel. Supported operations are documented in README.md.
