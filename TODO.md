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

- Android companion initial client: Android app ID, encrypted bearer-token storage, adaptive phone/tablet queue controls, transfer submission against saved sites, and loopback-only HTTP policy are implemented in `android/`. Phone and tablet API 36.1 AVD profiles are installed. Remaining: build a mobile-designed 1:1 feature mirror of the desktop client. The Android app is a remote control and display client; all server connections, saved credentials, browsing, and transfer work must execute on the end user's desktop host. The phone must not connect directly to FTP/SFTP/cloud servers or receive their credentials. Add full site/file browsing and feature parity to the desktop API and companion, secure pairing/tunnel setup, and a release signing key.

- [ ] Make the desktop backend reachable securely from anywhere, including over mobile
  data, without requiring end users to configure port forwarding, firewall rules, or
  network routes. Provide simple device pairing and automatic connection setup using
  Tailscale or WireGuard, with NAT traversal/relay as needed. An SSH tunnel inside the
  encrypted network is acceptable for forwarding the loopback-only JSON API. Keep the
  API off public interfaces; make sign-in, recovery/revocation, and bearer-token
  handling understandable to nontechnical users.

- [x] Add an authenticated loopback JSON API for scripts and clients, with queue inspection, transfer submission, and queue controls. Its bearer token is stored in FluxFTP's encrypted credential vault; remote access requires an authenticated SSH/VPN tunnel. Supported operations are documented in README.md.
