# FluxFTP 0.147.3

## Security and native dependencies

- Update the Windows OpenSSL 3.x runtime to 3.5.9 and retain OpenSSL 4.1.0 as
  the current 4.x release channel.
- Update Qt/PySide6 to 6.11.3, miniupnpc to 2.3.5, and the patched rclone
  backend to 1.76.0 built with Go 1.26.11.
- Update the Windows SQLite runtime to 3.54.1 and record its version and hash
  in the package manifest.
- Record the current Windows native dependency audit and the remaining ICU and
  Qt module inventory follow-up.

## Windows and desktop improvements

- Harden Windows UPnP discovery against vulnerable miniupnpc releases.
- Improve keyboard shortcuts, workspace layouts, accessibility, and transfer
  queue visibility.
- Add RushApp FTP Transfer command-line compatibility and maintain the Windows
  compatibility entry point in the release package.

See the release assets for Windows, Ubuntu, and Debian packages and SHA-256
checksums.
