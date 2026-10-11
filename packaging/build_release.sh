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
  mingw*|msys*) OSNAME="windows"; EXT=".exe"; ZIPNAME="prism.exe" ;;
  darwin)       OSNAME="macos";   EXT="";      ZIPNAME="prism" ;;
  *)            OSNAME="linux";   EXT="";      ZIPNAME="prism" ;;
esac
[ "$ARCH" = "amd64" ] && ARCH="x86_64"

ARTIFACT="prism-${VERSION}-${OSNAME}-${ARCH}"
echo "==> Building ${ARTIFACT}"

python -m pip install --quiet pyinstaller
pyinstaller packaging/prism.spec --distpath dist --workpath build -y

# smoke test the binary before shipping it (onefile layout: dist/prism is the executable)
./dist/prism params >/dev/null
./dist/prism genesis --network refraction >/dev/null

rm -f "${ARTIFACT}.zip"; rm -rf "${ARTIFACT}"
mkdir -p "${ARTIFACT}"
cp "dist/prism${EXT}" "${ARTIFACT}/prism${EXT}"
cp LICENSE README.md spec.md "${ARTIFACT}/"
if command -v sha256sum >/dev/null; then
  ( cd "${ARTIFACT}" && sha256sum prism${EXT} > SHA256SUMS )
else
  ( cd "${ARTIFACT}" && shasum -a 256 prism${EXT} > SHA256SUMS )
fi
if command -v zip >/dev/null; then
  zip -r "${ARTIFACT}.zip" "${ARTIFACT}"
else
  python -m zipfile -c "${ARTIFACT}.zip" "${ARTIFACT}"
fi
echo "==> Artifact: ${ARTIFACT}.zip"
