#!/usr/bin/env bash
# macOS framework shim for PyInstaller (used by .github/workflows/release.yml)
#
# Problem: actions/setup-python installs a *standalone* CPython whose layout is
#   <prefix>/lib/libpython3.12.dylib  +  <prefix>/lib/python3.12/...
# PyInstaller's Darwin phase instead looks for a *framework* install:
#   <dir>/Python.framework/Versions/<x.y>/Python            (executable)
#   <dir>/Python.framework/Versions/<x.y>/Python.framework/Python  (dylib)
# and aborts with "Unable to find ... Python.framework/Versions/x.y/Python"
# (observed in the v0.1.3 release failure).
#
# Fix: build exactly that symlink tree next to the interpreter's lib dir,
# pointing at the real files. No downloads, no sudo, idempotent.

set -euo pipefail

if [[ "$(uname)" != "Darwin" ]]; then
  echo "not macOS — nothing to do"; exit 0
fi

PY="${pythonLocation:-$(dirname "$(dirname "$(command -v python3)")")}/bin/python3"
[[ -x "$PY" ]] || PY="$(command -v python3)"
echo "Using interpreter: $PY"

VER="$("$PY" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
PREFIX="$("$PY" -c 'import sys; print(sys.base_prefix)')"
LIBDIR="$PREFIX/lib"

# Locate the real shared library of this interpreter.
DYLAP="$(find "$LIBDIR" -maxdepth 1 \( -name "libpython${VER}.dylib" -o -name "libpython${VER}*.a" \) | head -n1 || true)"
if [[ -z "${DYLAP:-}" ]]; then
  # standalone builds sometimes keep it only under lib/ as libpythonX.Y.a or .so-like dylib
  DYLAP="$(find "$LIBDIR" -maxdepth 1 -name "libpython*" | head -n1)"
fi
[[ -n "$DYLAP" ]] || { echo "::error::could not find libpython for $VER under $LIBDIR"; ls -la "$LIBDIR"; exit 1; }
echo "Found shared/static python lib: $DYLAP"

FWROOT="$LIBDIR"                     # .../Python.framework lives beside lib/
FRAMEWORK="$FWROOT/Python.framework"
VERSIONS="$FRAMEWORK/Versions"
VDIR="$VERSIONS/$VER"

mkdir -p "$VDIR/Python.framework" "$VDIR/include" "$VDIR/Resources"

# executable alias (PyInstaller checks os.path.exists on .../Versions/x.y/Python)
ln -sf "$PREFIX/bin/python${VER}" "$VDIR/Python" 2>/dev/null || ln -sf "$PY" "$VDIR/Python"
[[ -e "$VDIR/Python" ]] || ln -sf "$PY" "$VDIR/Python"

# dylib alias inside the inner framework dir
case "$DYLAP" in
  *.dylib) ln -sf "$DYLAP" "$VDIR/Python.framework/Python" ;;
  *)       ln -sf "$DYLAP" "$VDIR/Python.framework/Python" ;;  # .a accepted too by recent PyInstaller
esac
ln -sf "$VDIR" "$VERSIONS/Current" 2>/dev/null || true
ln -sf "Versions/Current/Python" "$FRAMEWORK/Python" 2>/dev/null || true

echo "Framework shim created:"
ls -lR "$FRAMEWORK" | head -40

"$PY" - <<EOF
import os, sys
p = sys.base_prefix
fw = os.path.join(p, "lib", "Python.framework", "Versions", "%d.%d" % sys.version_info[:2], "Python")
print("expected by PyInstaller:", fw, "exists:", os.path.exists(fw))
assert os.path.exists(fw), "shim failed"
EOF
echo "OK: macOS framework layout now resolvable for PyInstaller."
