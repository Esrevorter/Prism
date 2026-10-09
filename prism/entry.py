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


if __name__ == "__main__":
    sys.exit(main())
