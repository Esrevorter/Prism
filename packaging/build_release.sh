#!/usr/bin/env bash
# Build a self-contained `prism` binary with PyInstaller and package it as
# prism-<version>-<os>-<arch>.zip — the artifact naming GitHub Releases expects.
# Runs identically on Linux/macOS/Windows(Git-Bash); PyInstaller always builds
# for the *host* platform, so each OS gets its own CI job.
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION="$(sed -n 's/^version *= *"\([^"]*\)".*/\1/p' pyproject.toml | head -1)"
OS="$(uname -s | tr '[:upper:]' '[:lower:]')"            # linux | darwin | mingw64_nt-*
ARCH="$(uname -m)"                                        # x86_64 | arm64
case "$OS" in
  mingw*|msys*) OSNAME="windows"; EXT=".exe" ;;
  darwin)       OSNAME="macos";   EXT="" ;;
  *)            OSNAME="linux";   EXT="" ;;
esac
[ "$ARCH" = "amd64" ] && ARCH="x86_64"

ARTIFACT="prism-${VERSION}-${OSNAME}-${ARCH}"
echo "==> Building ${ARTIFACT}"

# The GUI static bundle is generated from prism/gui/ and must exist before
# PyInstaller freezes it into the binaries (see packaging/prism.spec datas).
python packaging/sync_gui.py

python -m pip install --quiet pyinstaller
pyinstaller packaging/prism.spec --distpath dist --workpath build -y

# smoke test the binaries before shipping them (onefile: dist/<name> is the exe)
./dist/prism params >/dev/null
./dist/prism genesis --network refraction >/dev/null
./dist/prism gui --help >/dev/null    # subcommand must be wired up

rm -f "${ARTIFACT}.zip"; rm -rf "${ARTIFACT}"
mkdir -p "${ARTIFACT}"
cp "dist/prism${EXT}" "${ARTIFACT}/prism${EXT}"
cp "dist/prism-gui${EXT}" "${ARTIFACT}/prism-gui${EXT}"
cp LICENSE README.md spec.md "${ARTIFACT}/"
if command -v sha256sum >/dev/null; then
  ( cd "${ARTIFACT}" && sha256sum prism${EXT} prism-gui${EXT} > SHA256SUMS )
else
  ( cd "${ARTIFACT}" && shasum -a 256 prism${EXT} prism-gui${EXT} > SHA256SUMS )
fi
if command -v zip >/dev/null; then
  zip -r "${ARTIFACT}.zip" "${ARTIFACT}"
else
  python -m zipfile -c "${ARTIFACT}.zip" "${ARTIFACT}"
fi
echo "==> Artifact: ${ARTIFACT}.zip"
