"""Console entry point for the ``prism-gui`` desktop binary (PyInstaller).

Mirrors entry.py for the CLI: the frozen GUI wallet executable resolves to
the same zero-argument launcher used by the pip-installed ``prism-gui``
script (rpc.launcher.main) — auto port, private data dir, single-instance
guard, in-process daemon, health-gated browser open. This file exists so
packaging/prism_gui.spec has a stable script target and so the double-click
installer mandate (spec §13 "Double-click installer mandate") freezes the
exact same code path that tests cover.

No command-line knowledge required: run the binary with zero arguments.
"""
from __future__ import annotations

import os
import sys

# Import strategy: load packages under their ``prism.*`` names (exactly what
# prism/tests/test_gui_launcher.py imports), because rpc.service uses
# ``from ..ai...`` relative imports that require a parent package. Repo root
# = parent of this file's directory; inside the frozen onefile build
# PyInstaller collects the ``prism`` package from there automatically.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def main(argv=None):
    from prism.rpc.launcher import main as _gui_main

    return _gui_main(argv)


if __name__ == "__main__":
    sys.exit(main())
