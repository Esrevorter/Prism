"""Sync the GUI static bundle from prism/gui/ (single source of truth) into
prism/rpc/static/ so it is a real package-data directory — required for both
pip installs (package_data) and PyInstaller frozen builds (datas).

Run before packaging:  python packaging/sync_gui.py   (from repo root)
Idempotent; refuses to sync if index.html is missing upstream.
"""
from __future__ import annotations

import os
import shutil
import sys

FILES = ("index.html", "app.js", "theme.css")


def main() -> int:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = os.path.join(root, "prism", "gui")
    dst = os.path.join(root, "prism", "rpc", "static")
    if not os.path.isfile(os.path.join(src, "index.html")):
        print(f"ERROR: {src}/index.html missing — refusing to sync", file=sys.stderr)
        return 1
    os.makedirs(dst, exist_ok=True)
    for name in FILES:
        s = os.path.join(src, name)
        if not os.path.isfile(s):
            print(f"ERROR: upstream bundle incomplete: {s}", file=sys.stderr)
            return 1
        shutil.copy2(s, os.path.join(dst, name))
    print("synced:", ", ".join(FILES), "->", os.path.relpath(dst, root))
    return 0


if __name__ == "__main__":
    sys.exit(main())
