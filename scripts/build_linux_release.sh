#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -r /etc/os-release ]] || ! grep -q '^ID=ubuntu$' /etc/os-release || ! grep -q '^VERSION_ID="24.04"$' /etc/os-release; then
  echo "Build the Linux release on Ubuntu 24.04 LTS." >&2
  exit 1
fi
if [[ "$(uname -m)" != x86_64 ]]; then
  echo "This release target is x86_64." >&2
  exit 1
fi

python3 -m venv .venv-linux-build
PYTHON="$ROOT/.venv-linux-build/bin/python"
"$PYTHON" -m pip install --upgrade pip
"$PYTHON" -m pip install -e .
"$PYTHON" -m pip install 'PyInstaller>=6'

"$PYTHON" scripts/build_cloud_backend.py
BACKEND="$ROOT/runtime/rclone/rclone"
BACKEND_SHA256="$(sha256sum "$BACKEND" | cut -d ' ' -f 1)"
"$PYTHON" release_tools.py package-native --backend "$BACKEND" --sha256 "$BACKEND_SHA256"
VERSION="$("$PYTHON" -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')"
"$PYTHON" scripts/package_linux_release.py --bundle \
  "$ROOT/dist/FluxFTP-$VERSION-linux-x86_64"
mv "$ROOT/dist/SHA256SUMS.txt" "$ROOT/dist/SHA256SUMS-linux-ubuntu24.04.txt"
