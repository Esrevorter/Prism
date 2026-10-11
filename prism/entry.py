"""Console entry point for the ``prism`` binary (PyInstaller / pip scripts).

Thin wrapper around ``chain.cli.main`` so both ``python3 -m chain.cli ...``
(from inside the ``prism/`` tree) and the packaged ``prism ...`` command
resolve to the same argument parser.
"""
from __future__ import annotations

import sys


def main(argv=None):
    from chain.cli import main as _cli_main

    return _cli_main(argv)


def gui(argv=None):
    """Console entry point for the ``prism-gui`` binary (PyInstaller).

    Same zero-argument launcher as ``rpc.launcher.main`` (mirrors
    prism/entry_gui.py), exposed here so packaging/prism.spec can freeze a
    second executable from this already-bundled script instead of pulling
    in the separate entry_gui.py target.
    """
    try:
        import prism.rpc.launcher as L          # packaged/frozen layout
    except ImportError:                          # repo-tree / pip-script layout
        import rpc.launcher as L

    return L.main(argv)


if __name__ == "__main__":
    sys.exit(main())
