# TODO

- [x] When both FXP site roles are Auto, try destination-passive first and safely
  reverse to source-passive if data-route setup fails before the transfer begins.
- [x] Add FTP Rush-style toolbar shortcuts for refreshing both panes, queue controls,
  log and transfer window visibility, and the FTP command terminal.
- [x] Give saved-site folders and each supported protocol/cloud provider distinct
  icons in Site Manager.

- [x] Before resuming, compare destination content with the source where supported. Local prefixes use SHA-256; full files use a shared native hash where available, with size/mtime as a local fallback. Unknown or changed content is shown in the conflict dialog; changed prefixes disable Resume. The dialog waits for a choice and says it has no automatic timeout.

- [x] Show folder icons in both the remote and local folder views.

- Add an Android/iOS app for full remote control of FluxFTP running on a home machine. The phone app should expose FluxFTP's full functionality, not just transfer monitoring and basic controls. Design a secure connection and pairing flow, with options such as WireGuard or Tailscale (and/or SSH or another authenticated encrypted tunnel), explicit authentication, and no unauthenticated public access.

- [x] Add an authenticated loopback JSON API for scripts and clients, with queue inspection, transfer submission, and queue controls. Its bearer token is stored in FluxFTP's encrypted credential vault; remote access requires an authenticated SSH/VPN tunnel. Supported operations are documented in README.md.
