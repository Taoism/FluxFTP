#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="3.13.18"
BUILD="20261110"
ARCHIVE="cpython-$VERSION+$BUILD-x86_64-unknown-linux-gnu-install_only.tar.gz"
SOURCE_URL="https://github.com/astral-sh/python-build-standalone/releases/download/$BUILD/$ARCHIVE"
SOURCE_SHA256="50c774f6674740739f791c05a9177ed03e2fe02abebda9bca3512eb7309b37cc"
PREFIX="$ROOT/runtime/linux-python-$VERSION-$BUILD"

if [[ -x "$PREFIX/bin/python3.13" ]]; then
  if "$PREFIX/bin/python3.13" -c 'import sys; raise SystemExit(sys.version_info[:3] != (3, 13, 18))'; then
    printf '%s\n' "$PREFIX"
    exit 0
  fi
  echo "Found an invalid local Python runtime at $PREFIX; remove it before rebuilding." >&2
  exit 1
fi
if [[ -e "$PREFIX" ]]; then
  echo "Found an incomplete local Python runtime at $PREFIX; remove it before rebuilding." >&2
  exit 1
fi

WORK="$(mktemp -d "${TMPDIR:-/tmp}/fluxftp-python-$VERSION.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT
curl --fail --location --silent --show-error "$SOURCE_URL" --output "$WORK/$ARCHIVE"
printf '%s  %s\n' "$SOURCE_SHA256" "$WORK/$ARCHIVE" | sha256sum --check --status
mkdir -p "$PREFIX"
tar -xzf "$WORK/$ARCHIVE" -C "$PREFIX" --strip-components=1

PYTHON="$PREFIX/bin/python3.13"
"$PYTHON" -c 'import ctypes, sqlite3, ssl, sys; assert sys.version_info[:3] == (3, 13, 18); print(sys.version); print("OpenSSL", ssl.OPENSSL_VERSION); print("SQLite", sqlite3.sqlite_version)' >&2
LICENSE="$(find "$PREFIX" -maxdepth 4 -type f \( -name LICENSE -o -name LICENSE.txt \) -print -quit)"
if [[ -z "$LICENSE" ]]; then
  echo 'Standalone Python distribution is missing its license file.' >&2
  exit 1
fi
cp "$LICENSE" "$PREFIX/LICENSE.txt"
printf '%s\n' "$PREFIX"
