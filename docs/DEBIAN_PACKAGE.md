# Debian installer

`fluxftp_0.133.0-1_amd64.deb` is the native Debian package for FluxFTP 0.133.0.
It targets Debian 13 or newer on amd64. The packaged application was built on
Ubuntu 24.04 and declares its runtime library dependencies, including the
`GLIBC_2.38` floor.

Install from the directory containing the downloaded package:

```bash
sudo apt install ./fluxftp_0.133.0-1_amd64.deb
```

APT installs any missing runtime libraries. Start FluxFTP from the desktop
application menu or run `fluxftp` in a terminal. The app is installed under
`/opt/fluxftp`; the launcher is `/usr/bin/fluxftp`. Settings and credentials
remain in the signed-in user's home directory and are not removed when the
package is uninstalled.

Remove FluxFTP with:

```bash
sudo apt remove fluxftp
```

The package suggests `gnome-keyring` for Secret Service credential storage and
`sshfs` for SSHFS mounts. Those features depend on a user D-Bus session and
host FUSE setup. See [BUILDING.md](BUILDING.md) for the Ubuntu build and Debian
validation procedure.
