"""Release hygiene pins (bug v0.1.5, Windows installer).

Reported symptom: double-clicking the Prism wallet on Windows showed
"daemon error: invalid utf-8 sequence of 1 bytes from index 32".

Root cause: the Tauri shell (packaging/tauri/prism-gui/src-tauri/src/main.rs)
reads the sidecar daemon's stdout as strict UTF-8, but CPython opened that
pipe with the process ANSI code page (e.g. cp1252 on en-US Windows). The
em-dash in the launcher line "Prism wallet is already running — opening it
at ..." encoded as a lone 0x97 byte -> Rust String::from_utf8 failed before
any JSON status line could arrive.

Three independent layers now prevent regression; each is pinned here:

  L1  all user-facing print() strings in the frozen-daemon modules are pure
      ASCII (this test), so even a hostile legacy code page cannot corrupt
      the pipe protocol;
  L2  rpc.launcher._force_utf8_stdio() reconfigures stdout/stderr to UTF-8
      (errors="replace") at import time and sets PYTHONUTF8=1 for children;
  L3  the GUI static bundle served over HTTP declares <meta charset="utf-8">
      and is itself valid UTF-8.
"""
import ast
import io
import os
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]

# Modules that end up inside the frozen prism-gui daemon whose stdout the
# desktop shell parses. Keep this list in sync with packaging/prism_gui.spec
# hiddenimports entry points.
DAEMON_MODULES = [
    "prism/entry_gui.py",
    "prism/rpc/launcher.py",
    "prism/rpc/server.py",
    "prism/rpc/service.py",
    "prism/auth/keystore.py",
    "prism/auth/captcha.py",
]


def _print_call_constants(tree):
    """Yield (lineno, string) for every str literal inside a print(...) call."""
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "print"):
            for sub in ast.walk(node):
                if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                    yield node.lineno, sub.value


class TestAsciiStdoutProtocol(unittest.TestCase):
    """L1: no non-ASCII literals may ever reach the sidecar's stdout."""

    def test_print_calls_are_ascii_only(self):
        offenders = []
        for rel in DAEMON_MODULES:
            path = ROOT / rel
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for lineno, lit in _print_call_constants(tree):
                if any(ord(c) > 127 for c in lit):
                    offenders.append(f"{rel}:{lineno}: {lit[:60]!r}")
        self.assertEqual(
            offenders, [],
            "non-ASCII text in a print() call can corrupt the Tauri sidecar "
            "stdout protocol on Windows ANSI code pages (v0.1.5 bug). Move "
            "pretty typography into the GUI/HTTP layer; keep console lines "
            f"pure ASCII. Offenders:\n" + "\n".join(offenders))

    def test_known_bad_line_is_gone(self):
        # Exact regression pin for the reported failing message: check the
        # print CALL itself, not raw file text (the docstring legitimately
        # quotes the old bug).
        path = ROOT / "prism/rpc/launcher.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        msgs = [lit for _, lit in _print_call_constants(tree)
                if "already running" in lit]
        self.assertTrue(msgs, "regression target line vanished entirely")
        for m in msgs:
            self.assertNotIn("\u2014", m)
        self.assertIn("Prism wallet is already running. Opening it at ", msgs[0])


class TestForceUtf8Stdio(unittest.TestCase):
    """L2: the launcher hardens its own streams at import time."""

    def test_reconfigure_and_env(self):
        from prism.rpc import launcher as mod
        old_out, old_err, old_env = sys.stdout, sys.stderr, os.environ.get("PYTHONUTF8")
        try:
            buf = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
            sys.stdout = sys.stderr = buf
            os.environ.pop("PYTHONUTF8", None)
            mod._force_utf8_stdio()
            self.assertEqual(buf.encoding.lower().replace("-", ""), "utf8")
            self.assertEqual(os.environ.get("PYTHONUTF8"), "1")
        finally:
            sys.stdout, sys.stderr = old_out, old_err
            if old_env is None:
                os.environ.pop("PYTHONUTF8", None)
            else:
                os.environ["PYTHONUTF8"] = old_env

    def test_survives_none_streams(self):
        # Frozen windowed mode (console=False): sys.stdout is None.
        from prism.rpc import launcher as mod
        old_out, old_err = sys.stdout, sys.stderr
        try:
            sys.stdout = sys.stderr = None
            mod._force_utf8_stdio()          # must not raise
        finally:
            sys.stdout, sys.stderr = old_out, old_err


class TestGuiBundleEncoding(unittest.TestCase):
    """L3: everything served over HTTP is valid UTF-8 and says so."""

    def test_static_bundle_is_valid_utf8_with_charset_meta(self):
        base = ROOT / "prism" / "rpc" / "static"
        files = [f for f in base.glob("*") if f.is_file()]
        self.assertTrue(files, "GUI static bundle missing (run packaging/sync_gui.py)")
        for f in files:
            raw = f.read_bytes()
            raw.decode("utf-8")              # raises on mojibake
            if f.suffix == ".html":
                text = raw.decode("utf-8").lower()
                self.assertIn('charset="utf-8"', text.replace("'", '"'),
                              f"{f.name} lacks an explicit UTF-8 declaration")


if __name__ == "__main__":
    unittest.main()
