# FluxFTP TODO

This is the consolidated FluxFTP backlog. The desktop backlog is reconciled
against the publish workspace's completion marks. Completed Android work and
remaining companion requirements are combined here as well.

## Open work

### Security and vulnerability review

- [ ] Audit FluxFTP and its shipped dependencies for known CVEs and other
  security vulnerabilities; fix confirmed issues and keep the dependency
  versions and review results current. The 0.146.0 updater hardens Windows
  staging with a random access-controlled directory and repeats the package
  digest check before installation; Debian staging also uses a private directory
  and digest checks. The 2026-11-18 audit updates PySide6 and setuptools, adds a
  pip-audit CI check, builds patched miniupnpc 2.3.5 Windows wheels, and updates
  bundled rclone to v1.76.0 built with Go 1.26.11; govulncheck is clean for
  reachable vulnerabilities. The Python dependency scan found no known
  vulnerabilities. The 0.147.4 package audit found unused Qt PDF/VirtualKeyboard
  copies in Linux packages and Qt Network CVE-2026-116337 with no direct FluxFTP
  call path; source packagers now prune the unused copies. The Linux release
  build now compiles SQLite 3.54.1 from the official source archive, verifies
  its hash, and records SQLite provenance; extracted portable and Debian package
  smoke checks passed. The published 0.147.4 Linux packages remain affected
  until replaced. Windows and Linux release builds now use locally extracted,
  checksum-pinned CPython 3.13.18 runtimes without changing system Python or
  PATH. The Windows runtime is verified with OpenSSL 3.5.9 and SQLite 3.54.1.
  Complete the native-library inventory, track the fixed Qt
  release, and review Qt optional module necessity. OpenSSL advisory follow-up
  remains deferred. See `docs/SECURITY_AUDIT.md`.

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
- [x] Create an original Flux Capacitor inspired FluxFTP icon and wordmark;
  wire the icon into the desktop window and Windows packaging.
- [ ] Verify the installed Windows executable at `D:\Program Files\FluxFTP\fluxftp.exe`
  displays the official icon and fix the package icon if it does not.

### Desktop features

- [x] Make the F6 key open Options from anywhere in the main window; show F6 on
  the Options toolbar tooltip and share the same action with Edit and Tools.
- [x] Double-click any column divider to size that column to its contents;
  keep workspace tabs beneath the Workspaces heading and mark duplicated tab
  pairs, and start Quick Connect with blank fields.
- [x] Put workspace tabs and a new-workspace button beside the single
  Workspaces heading, name tabs after connected sites (both site names for
  FXP), and show a close button on the active tab only when multiple workspaces are open.
- [x] Add a Files progress column to the transfer queue for folder transfers.
- [x] Add right-click Hide panel actions and saved-site Edit site actions to
  the Workspaces site menus.

- [x] Fix Backspace navigation so pressing Backspace in a remote directory view
  navigates to the parent directory (`..`).
- [x] Fix double-click activation for file and folder items in the browser panes.
- [x] Fix opening Options from the F6/Edit menu path; it now shares one action
  across Edit and Tools.
- [x] Allow users to select and copy text displayed in read-only text boxes
  and other non-editable text fields throughout the interface.
- [x] Add a show/hide control to the password field in the saved-site editor;
  keep the password masked by default and let the user reveal it on demand.
- [x] Add a hotkey manager modeled on the supplied FTPRush reference. Show a
  searchable or scrollable action table with Name, Hotkey, and Apply to columns;
  include actions even when no shortcut is assigned. Support Global, Remote,
  Local, and Queue scopes, including actions available in more than one scope.
  Cover the reference action groups: window and panel navigation, site and
  connection actions, local/remote file and directory commands, selection and
  search, queue submission and control, transfer modes, logs, layouts, and
  scripting. Let users assign and clear shortcuts, save changes across
  launches, and restore the shipped factory-default shortcut map at any time,
  independent of their saved custom assignments. Detect duplicate shortcuts
  where their scopes overlap, and explain conflicts before applying a change.
- [x] Review and fix rendering of items in the left sidebar. The workspace
  overview now stretches its last visible column across the dock, including
  when optional columns are hidden.
- [x] Remember the positions and arrangement of panels and windows across
  launches. Main and dialog geometry, dock positions/visibility/floating
  geometry, panel sizes, and pane split ratios are persisted and restored.
- [x] Add FluxFTP CLI scripting compatibility for RushApp FTP Transfer
  (`rush_cmdline.exe`, `Script RushApp.FTP.Transfer(...)`), with `{~N}` path
  parameters, saved-site bookmark paths, and upload/download/FXP, file/folder,
  and logout flags. The shim queues work through the authenticated loopback API;
  unsupported RushScript statements and transfer options are rejected.
- [x] Add direct View → Dock panels → Move to Bottom commands for Queue,
  Workspaces, and each log panel.
- [x] Preserve fractional high-DPI scaling and refresh FluxFTP's custom font and
  toolbar sizing when moving the window between monitors or changing DPI.
- [x] Check GitHub releases in the background, automatically download and verify
  compatible updates, and install on exit so the next launch uses the new version.
  Windows may request administrator approval when the install folder requires it.
- [x] Add an FTP Rush-style FluxFTP layout preset with the two-pane workspace
  above a bottom tab area for the transfer queue and logs. Keep panels movable
  and persist the user's customized dock layout when FluxFTP exits. Make the
  Tools panel and each pane's icons, address bar, and filter independently movable;
  double-clicking the pane divider centers the two workspaces.
- [ ] Add a Debian tester debug mode with a clear way to enable diagnostics and
  collect useful application and runtime logs for troubleshooting.
- [ ] Fix and verify the F6 shortcut on Debian so it opens Options from the
  main window; identify any platform-specific shortcut handling and add coverage.
  The hotkey manager now gives Global actions application shortcut scope, with
  focused regression coverage. Verify the packaged app on Debian; check whether
  the original failure involved monitor detection on a single-monitor setup.
- [ ] Define and implement the auto-trading workflow before enabling trades.
  Requirements captured so far:
  - FTP Method 2: poll each configured source site every 10 seconds and compare
    directory listings to detect newly created source folders; determine a
    reliable comparison method (the initial idea was to export two `ls`
    snapshots and compare them). Record each detected trade in local
    `trade.DB`, then FXP the content from source to destination. Clarify the
    unspecified "extra feature" described as "same as previous."
  - IRC-triggered trades: investigate whether FluxFTP can receive matching
    messages from HexChat, then record and process them through `trade.DB`.
    Also assess a built-in IRC mode that connects and idles in configured
    channels under a trading-instance nickname, watches configured matching
    expressions, and submits matching trades through `trade.DB`.
  - `trade.DB`: local ledger of file and folder locations; support population
    through both "cowboy" and "rational" methods (define these terms), and
    decide whether to encrypt it and/or offer automatic wiping and its
    retention trigger.
  - `.fluxftprc`: define hierarchical trading rules for each configured
    source and destination, including sections, whitelists, and blacklists.
    Proposed rule shape: `bookmark:(source or destination):path:whitelist:blacklist`.
    A bookmark names the entry (example: `siteA`); `source` marks a path for
    automatic sending, and `destination` marks a path for automatic receiving.
    Complete the remaining syntax and semantics before implementation.
  - Define trade detection precisely: make the 10-second polling interval
    configurable; determine how listings are compared; detect when a source
    folder is stable and fully uploaded before trading; and specify behavior
    for renamed, deleted, or partially uploaded folders.
  - Define a durable trade lifecycle and stable trade ID, with explicit states
    such as detected, waiting for stability, eligible, transferring, verified,
    complete, failed, paused, and cancelled. Persist transitions and recover
    queued or in-progress trades safely after restart.
  - Prevent duplicate submissions caused by repeated polls, duplicate IRC
    messages, or restarts. Specify whether source changes update an existing
    trade or create a new one.
  - Define transfer conflict and completion behavior: skip, replace, or rename
    existing destination content; verify transferred files before completion;
    handle partial transfers; and set retry limits, backoff, and terminal
    failure behavior.
  - Complete `.fluxftprc` syntax and semantics with examples for source and
    destination pairs, nested paths, multiple rules, whitelist/blacklist
    precedence, conflicting rules, and invalid configuration. Evaluate a
    readable validated format and provide a dry-run preview of matched trades.
  - Specify `trade.DB` fields, schema migration and backup behavior, retention,
    and any wipe policy or trigger. Keep credentials out of the ledger unless
    a reviewed design requires otherwise. Define the "cowboy" and "rational"
    population methods in concrete terms.
  - Define IRC input format and matching expressions, allowed channels and
    senders, and how matches map to configured source/destination rules. Decide
    whether HexChat integration is required or optional, define reconnect and
    disconnect behavior, and validate all IRC input as untrusted before it can
    create a trade.
  - Define operational limits and offline behavior, including concurrent
    connections, queue priority, bandwidth limits, and source-site outages.
  - Add controls and visibility: preview rules before enabling them, allow
    per-rule pause/disable and a global trading-off switch, and log why each
    candidate trade matched or was rejected.
  - Write acceptance scenarios for polling detection, incomplete-folder
    exclusion, duplicate suppression, IRC-triggered trades, transfer
    verification and retry, and restart recovery. Include end-to-end examples
    for a polled folder, an IRC-triggered trade, and a failed transfer/retry.
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

- [ ] Map queue capacity with incremental stress runs: start at 10,000 files,
  then add 10,000 files per run while recording the largest passing queue and
  first failing size. After two failures, tighten the increment to 5,000; after
  three failures, tighten it to 2,500 to locate the failure threshold. Keep the
  100,000-file stress run separate until this progression identifies the limit.
- [ ] Run a clean full desktop regression only at a major version transition or
  a language rewrite. For ordinary releases, reconcile `tests.md` with prior
  batch history, then run changed and never-run tests in manageable batches.
  Earlier batched runs exercised the modularization fixes; the archived
  `nodeids.txt` contains 1,120 IDs while the older aggregate summary reports
  1,115, so `tests.md` records per-ID evidence and the discrepancy. Keep the
  incremental queue stress plan above separate from a full-suite run.
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
- [x] Run packaged GUI smoke checks for each Linux release artifact and validate
  the automated Windows release workflow on a release tag. The 0.147.3
  release workflow built and smoke-checked the Ubuntu package, built and
  install-checked the Debian package, and passed its tagged Windows release
  checks. Fedora 45 packaging remains pending.
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
