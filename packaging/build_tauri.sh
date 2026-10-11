#!/usr/bin/env bash
# Build the double-click desktop installer (spec §13 mandate) with Tauri.
#
# Pipeline:
#   1. sync_gui.py            — refresh prism/rpc/static from prism/gui
#   2. PyInstaller            — freeze the GUI launcher -> dist/prism-gui(.exe)
#                               (embedded CPython; console=False so no terminal)
#   3. sidecar staging        — copy the frozen binary to
#                               src-tauri/binaries/prism-gui-<target-triple>
#   4. placeholder icons      — generate PNG/ICO/ICNS if absent (real art can
#                               replace them later; tauri build requires them)
#   5. tauri build            — per-platform installers:
#                               Windows .msi/.exe, macOS .dmg/.app, Linux .deb/.AppImage
#
# Requirements on the build host: python3 + pyinstaller, node/npm, rust toolchain
# (cargo), and the usual Tauri system deps on Linux (webkit2gtk etc). None of
# these exist in the current dev sandbox — CI runners provide them.
set -euo pipefail
cd "$(dirname "$0")/.."

TAURI_DIR="packaging/tauri/prism-gui"
SRC_TAURI="$TAURI_DIR/src-tauri"
BIN_DIR="$SRC_TAURI/binaries"

echo "==> [1/5] Sync GUI static bundle"
python packaging/sync_gui.py

echo "==> [2/5] Freeze prism-gui desktop binary (PyInstaller, onefile)"
python -m pip install --quiet pyinstaller
pyinstaller packaging/prism_gui.spec --distpath dist --workpath build -y
GUI_BIN="dist/prism-gui"
[ -f "dist/prism-gui.exe" ] && GUI_BIN="dist/prism-gui.exe"
[ -f "$GUI_BIN" ] || { echo "ERROR: expected $GUI_BIN after PyInstaller"; exit 1; }

echo "==> [3/5] Stage sidecar for Tauri"
mkdir -p "$BIN_DIR"
if command -v rustc >/dev/null 2>&1; then
  TRIPLE="$(rustc -vV | sed -n 's/^host: //p')"
else
  # Fallback triple derivation when cargo isn't installed yet (build fails
  # later at step 5 with a clear message anyway).
  OS="$(uname -s | tr '[:upper:]' '[:lower:]')"; ARCH="$(uname -m)"
  case "$OS/$ARCH" in
    linux/x86_64)  TRIPLE="x86_64-unknown-linux-gnu" ;;
    linux/aarch64) TRIPLE="aarch64-unknown-linux-gnu" ;;
    darwin/arm64)  TRIPLE="aarch64-apple-darwin" ;;
    darwin/x86_64) TRIPLE="x86_64-apple-darwin" ;;
    mingw*|msys*)  TRIPLE="x86_64-pc-windows-msvc" ;;
    *) TRIPLE="${ARCH}-unknown-${OS}" ;;
  esac
fi
EXT=""
case "$TRIPLE" in *windows*) EXT=".exe" ;; esac
cp "$GUI_BIN" "$BIN_DIR/prism-gui-$TRIPLE$EXT"
chmod +x "$BIN_DIR/prism-gui-$TRIPLE$EXT" 2>/dev/null || true
echo "    sidecar: binaries/prism-gui-$TRIPLE$EXT"

echo "==> [4/5] Ensure app icons exist"
ICONS="$SRC_TAURI/icons"
mkdir -p "$ICONS"
if [ ! -f "$ICONS/icon.png" ]; then
  python - <<'PY'
# 256x256 solid-color placeholder PNG, stdlib-only (no Pillow dependency).
import struct, zlib
W = H = 256
def chunk(tag, data):
    c = struct.pack(">I", len(data)) + tag + data
    return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
ihdr = struct.pack(">IIBBBBB", W, H, 8, 2, 0, 0, 0)   # RGB8
raw = b"".join(b"\x00" + bytes((13, 17, 23)) * W for _ in range(H))  # #0d1117
png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
       + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))
open("packaging/tauri/prism-gui/src-tauri/icons/icon.png", "wb").write(png)
print("    wrote placeholder icons/icon.png")
PY
fi
# tauri-build consumes icon.ico / icon.icns / 32x32.png / 128x128.png.
# `tauri icon` regenerates all formats properly when npm is available.
# tauri v1 CLI is provided by the shell's devDependency (@tauri-apps/cli);
# invoke it via npm exec so we never pull a mismatched v2 CLI from the void.
( cd "$TAURI_DIR" && npm exec -- tauri icon "$ICONS/icon.png" >/dev/null 2>&1 ) \
  || { echo "ERROR: 'tauri icon' generation failed (icons are mandatory for bundling)" >&2; exit 1; }

echo "==> [5/5] Tauri build (installers)"
if ! command -v cargo >/dev/null 2>&1; then
  echo "ERROR: Rust toolchain (cargo) not found. Install it or run this on CI." >&2
  echo "       The rest of the pipeline (steps 1-4) completed successfully." >&2
  exit 127
fi
( cd "$TAURI_DIR" && npm install --no-audit --no-fund && npm run build )

echo "==> Installers under: $SRC_TAURI/target/release/bundle/"
