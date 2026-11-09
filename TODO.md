# FluxFTP TODO

This is the consolidated FluxFTP backlog. The desktop backlog is reconciled
against the publish workspace's completion marks. Completed Android work and
remaining companion requirements are combined here as well.

## Open work

### Platform builds and release

- [x] Build an x86_64 Ubuntu/Debian Linux package as a portable `.tar.gz`.
  Built on Ubuntu 24.04 with the pinned Linux rclone backend, recorded its
  `GLIBC_2.38` floor, and passed the packaged checksum/GUI smoke checks on
  Debian 13.7. Artifact and build steps are in `publish/dist` and
  `publish/docs/BUILDING.md`.
- [x] Publish the Ubuntu/Debian Linux archive and dedicated checksum to the
  public `v0.132.0` GitHub release.
- [x] Build a native Debian 13 amd64 `.deb` installer, declare GUI/runtime
  dependencies, add the desktop entry and launcher, and verify APT dependency
  resolution plus isolated dpkg install, app launch and removal. The package is
  staged in `publish/dist/fluxftp_0.132.0-1_amd64.deb` with
  `publish/dist/SHA256SUMS-debian13.txt`.
- [x] Publish the Debian `.deb` installer and checksum to the public
  `v0.132.0` release.
- [ ] Build and publish an x86_64 Fedora 45 portable `.tar.gz`. Set up the
  tested Python 3.13/PySide6 packaging environment, pip, Git and Go for the
  patched rclone backend, then run packaged GUI smoke checks. Keep its glibc
  baseline separate from Ubuntu/Debian.
- [x] Extend the automated release process to coordinate Windows, Ubuntu and
  Debian packages from the same source tag, with per-platform checksums and
  smoke/install checks. Release requirements are in
  `publish/docs/RELEASE_CHECKLIST.md`.
- [ ] Sign production packages. Signing credentials and macOS/Linux build hosts
  are not currently available.
- [ ] Build an iOS version of FluxFTP and validate it on Apple hardware.
- [ ] Provide broader screen-reader coverage and macOS/Linux accessibility
  validation.
- [ ] Build a FluxFTP website with product overview, screenshots,
  downloads/releases, documentation and support links.
- [ ] Create a Flux capacitor inspired FluxFTP application icon and logo,
  reflecting the name's Back to the Future reference.

### Desktop features

- [x] Fix Backspace navigation so pressing Backspace in a remote directory view
  navigates to the parent directory (`..`).
- [x] Fix double-click activation for file and folder items in the browser panes.
- [x] Fix opening Options from the F6/Edit menu path; it now shares one action
  across Edit and Tools.
- [ ] Review and fix rendering of items in the left sidebar.
- [ ] Add FluxFTP CLI scripting compatibility for RushApp FTP Transfer
  (`rush_cmdline.exe`, `Script RushApp.FTP.Transfer(...)`), including its path
  placeholders and transfer/directory/logout flags (`RS_UP`, `RS_DIRDES`,
  `RS_DIRSRC`, `RS_LOGOUT`).
- [ ] Define the intended workflow and requirements for auto trading before
  implementation.
- [x] Validate SSHFS mounts end to end on Windows with WinFsp/SSHFS-Win and a
  trusted SFTP server. The opt-in integration test mounts a drive, reads a
  fixture file, and confirms unmount cleanup.
- [x] Add CLI mode for saved-site listing, remote directory listings, transfer
  submission and queue inspection/control, with JSON output and exit codes.
  Credentials and API bearer token come from FluxFTP's encrypted vault; remote
  operations are routed through the running desktop's loopback API and its
  established authentication and host-trust flow.
- [ ] Continue modularizing the application: separate UI, transfer/queue
  orchestration, protocol and cloud backends, configuration/persistence, and
  automation behind clear interfaces while preserving behavior. Separated
  components now include the saved-site editor (`site_dialog.py`), shared worker
  (`worker.py`), two-pane UI (`pane.py`), menus/toolbar/quick-connect
  (`window_chrome.py`), pane-level connection workflows (`window_connections.py`),
  background execution/trust handling (`window_workers.py`), queue
  submission/lifecycle (`transfer_queue.py`),
  durable queue storage and engine/controller, protocol/cloud connection
  construction (`connection_factory.py`), protocol handshakes
  (`protocol_connectors.py`), byte transfer/resume/ASCII operations
  (`connection_transfer.py`), saved-site/credential/data-folder persistence
  (`configuration_persistence.py`), configuration backup UI
  (`configuration_backup_ui.py`), automation service lifecycle
  (`automation_runtime.py`), startup widget/service composition
  (`window_initializer.py`), and native connection listing/file operations
  (`connection_operations.py`). `Window.__init__` now delegates to the
  initializer, while `core.Connection` composes protocol handshakes, transfer
  behavior, and listing/file operations as mixins. Preferences, cloud backend,
  automation engine/UI, and deployment/transfer services also have dedicated
  modules. Run the deferred regression suite before closing this item.
- [ ] Validate GitLab and GitHub Actions artifact deployments against live
  projects. Both pickers, encrypted token storage, bounded/safe artifact imports,
  deployment previews and rollback provenance are implemented. Focused mocked
  client/archive tests and deployment regression tests pass; live project checks
  remain.
- [x] Expose and verify the **Import GitHub Actions artifact** option in the
  0.132.0 Deployment Profile editor. The rebuilt package includes the GitHub
  client and dialog; profile-editor import and provenance tests pass. Live
  authentication and artifact selection against a real project remain open.
- [x] Expose and verify the **Import GitLab pipeline artifact** option in the
  0.132.0 Deployment Profile editor. The rebuilt package includes the GitLab
  client and dialog; profile-editor import and provenance tests pass. Live
  authentication and artifact selection against a real project remain open.
- [x] Use cloud-provider brand icons/logos in account setup, Cloud Accounts,
  and cloud site selection. Source the providers' official brand assets and
  follow their usage, color and attribution guidance; retain readable text labels
  and accessible names alongside the marks.
- [ ] Compare deployment revisions and show changed files, including text
  diffs for configuration and website files.
- [ ] Add a dedicated quick switcher for workspace presets (sites, folder
  pairs, tabs and layout), extending existing workspace persistence.

### Android and iOS companion

- [ ] Complete the Android companion as a mobile-designed 1:1 feature mirror
  of the desktop client. The initial Android remote-control client is in
  `C:\codex\fluxftp\publish\android`; it stores the bearer token encrypted
  with Android Keystore and supports adaptive queue controls and transfer
  submission.
- [ ] Extend the desktop API and companion with full site and file browsing
  and feature parity. All server connections, saved credentials, browsing and
  transfer work must run on the desktop host; phones must not connect directly
  to FTP/SFTP/cloud servers or receive their credentials.
- [ ] Add secure pairing and tunnel setup, explicit device authorization,
  connection recovery and revocation. Keep the desktop API off public
  interfaces.
- [ ] Obtain a release signing key for the Android app.
- [ ] Provide authenticated remote access/control from authorized Android
  and iOS phones/tablets, including transfer monitoring/submission and
  start/pause/resume/cancel and queue management.

#### Secure remote-connectivity sequence

- [ ] Tailscale proof of concept (rough estimate: 2–4 days): use the loopback
  JSON API through Tailscale Serve; verify pairing and access over mobile data,
  including relay fallback, with no router or port-forwarding setup for users.
- [ ] Productize Tailscale setup (rough estimate: 1–3 weeks): guided host/device
  pairing and sign-in, connection status, reconnect/recovery, device revocation,
  safe token handling, and Windows/Android workflow checks.
- [ ] If Tailscale Serve is insufficient, evaluate an optional SSH tunnel inside
  the encrypted tailnet (rough estimate: 2–5 additional days), forwarding only
  to the desktop loopback API with host-key verification and lifecycle handling.
- [ ] Keep standalone WireGuard as an alternative (rough estimate: 4–8 weeks),
  requiring peer enrollment/key lifecycle, coordination/discovery, NAT traversal,
  relay fallback, recovery/revocation, packaging and mobile-data validation.

### Network interoperability

- [ ] Continue real-network active FTP and production protected-FXP
  interoperability testing. External-internet UPnP active FTP remains
  unresolved: mapping readback, WAN/public-egress match and owned mapping
  cleanup passed, but public GNU active transfer and external SOCKS callback
  timed out. Router/upstream-path investigation is next; local filtering is
  not conclusively excluded. Evidence is in `logs/eset-ftp-capture-analysis.json`,
  `logs/upnp-external-20261025.json` and
  `logs/upnp-proxy-callback-20261025.json`.

## Test backlog

- [ ] Complete one clean full desktop regression invocation after the
  modularization fixes. `pytest --collect-only` reports 1,115 tests; all test
  ranges have now been exercised in chunks. Fixed missing imports in cloud and
  recursive transfer paths, headless scheduler wiring, resume fallback and
  local conflict fingerprints, worker compatibility, Qt row selection, and
  queue submission. Updated stale test hooks/expectations for extracted modules,
  the SSL preferences page, and completed-upload cleanup. All 1,115 collected
  cases ran in named chunks: 1,111 passed, three skipped, and the 100,001-item
  queue responsiveness check exceeded its 0.75-second UI-gap threshold once
  (1.813 seconds); isolated reruns passed. A clean single-invocation full run is
  still pending. The cached OpenSSL 4.x
  integration assets target Python 3.13, so their two incompatible-runtime
  checks skip under the Python 3.11 test venv; `tzdata` was installed in that
  venv for Windows DST coverage. Rerun the suite, then close modularization.
- [x] Add focused tests for the extracted startup composition in
  `window_initializer.py`, covering smoke-mode settings isolation, service
  initialization, and widget wiring.
- [x] Add focused connection-operation tests for `connection_operations.py`,
  covering Unix/DOS LIST parsing, MLSD/LIST/STAT selection, passive-to-active
  fallback, timezone formatting, and remote mutation methods.
- [x] Run focused tests for the other modularized boundaries: queue lifecycle
  and storage, connection construction and protocol handshakes, transfer/resume,
  configuration persistence/backup, and automation lifecycle. Reuse and extend
  the matching existing tests instead of duplicating coverage.
- [x] Exercise CLI/API workflows end to end: authentication, saved-site access,
  listing, transfer submission, queue controls, JSON output, and error exit
  codes.
- [ ] Validate GitLab and GitHub Actions artifact deployments against live test
  projects, including selection, safe import boundaries, preview, rollback
  provenance, and failure recovery. Mocked API/archive and deployment regression
  tests pass locally; project access and tokens are needed for live runs.
- [ ] Run packaged GUI smoke checks for each Linux release artifact and validate
  the automated Windows release workflow on a release tag. Rebuilt Windows
  0.132.0 startup/shutdown passed with OpenSSL 3.5.8; Linux release artifacts
  now include the Ubuntu 24.04/Debian 13 package, whose offscreen GUI smoke and
  rclone checks pass on Debian 13.7. Fedora 45 packaging and a tagged Windows
  release workflow remain pending.
- [ ] Continue real-network active FTP, protected-FXP, and UPnP interoperability
  checks; record server, proxy/router path, and cleanup results.

## Completed

- [x] Compare destination content with the source before resuming where
  supported. Local prefixes use SHA-256; full files use a shared native hash
  where available, with size/mtime as a local fallback. Unknown or changed
  content is shown in the conflict dialog; changed prefixes disable Resume.
- [x] Show folder icons in both remote and local folder views.
- [x] Add an authenticated loopback JSON API for queue inspection, transfer
  submission and queue controls. The bearer token is stored in FluxFTP's
  encrypted credential vault; remote use requires an authenticated SSH/VPN
  tunnel.
- [x] Add a command palette for commands, sites, folders, deployment profiles
  and workspaces (Ctrl+Shift+P); it also saves/applies workspace presets.
- [x] Add unified upload/download bandwidth limits shared across native/cloud
  transfer engines, including scheduled limits. Theme selection applies live,
  so no restart notice is needed.
- [x] Add recursive remote search filters for filename masks, size range and
  modification date, with open-folder and download-to-other-pane actions.
- [x] Add optional upload-on-save for remote editing, remote conflict checks
  and opt-in recoverable local backups.
- [x] Add transfer preflight checks for local free space, destination folder
  access and existing destination paths.
- [x] Add reusable queue recipes for endpoints, paths, filename masks, byte
  limits and readback verification policy, with preview before queueing.
- [x] Add actionable Windows completion/failure notifications linking to
  Transfer History.
- [x] Add a connection health dashboard showing recent setup latency,
  throughput, retries and failures from transfer history.
- [x] Publish the GitHub repository with versioned downloadable releases and
  SHA-256 checksums.
- [x] Complete the initial Android companion client: encrypted bearer-token
  storage, adaptive phone/tablet queue controls, transfer submission against
  saved sites, and loopback-only HTTP policy. Emulator phone/tablet API 36.1
  profiles are installed.
- [x] Improve queue and active-transfer controls, add the certification
  checker, and add SCP support.
- [x] Complete production protected CPSV FXP testing through the saved proxy:
  fresh/resumed transfers, downloaded SHA-256 verification and cleanup passed.
