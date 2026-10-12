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
import re
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


class TestGuiBundleFreshness(unittest.TestCase):
    """Gate 1 (docs/ATTESTATION.md §6): prism/rpc/static must be byte-identical
    to prism/gui.  The v0.1.6 regression shipped the OLD captcha UI because a
    stale static/ copy was frozen into the binary while gui/ held the fix —
    these pins make that class of bug fail CI before packaging."""

    FILES = ("index.html", "app.js", "theme.css")

    def _dir(self, *parts):
        return str(ROOT.joinpath(*parts))

    def test_static_bundle_matches_source_of_truth(self):
        src = self._dir("prism", "gui")
        dst = self._dir("prism", "rpc", "static")
        for name in self.FILES:
            with open(os.path.join(src, name), "rb") as fh:
                want = fh.read()
            path = os.path.join(dst, name)
            self.assertTrue(os.path.isfile(path),
                            f"packaging bundle missing {name} — run "
                            f"`python packaging/sync_gui.py`")
            with open(path, "rb") as fh:
                got = fh.read()
            self.assertEqual(got, want,
                             f"prism/rpc/static/{name} is STALE vs prism/gui/"
                             f"{name} — run `python packaging/sync_gui.py` "
                             f"and commit the result")

    def test_served_captcha_is_vector_first(self):
        # The Windows bug: an <img> PNG plus an unstyled overlay circle read
        # as "a square and a big black dot".  The served bundle must render
        # ONE inline SVG whose tiles are themselves the tap targets.
        html = open(self._dir("prism", "rpc", "static", "index.html"),
                    encoding="utf-8").read()
        js = open(self._dir("prism", "rpc", "static", "app.js"),
                  encoding="utf-8").read()
        self.assertIn('id="cap-svg"', html)
        self.assertNotIn('<img id="captcha-img"', html)   # no second picture
        self.assertIn("renderCapSvg", js)
        self.assertIn("createElementNS", js)              # vector-first
        self.assertNotIn('getElementById("captcha-img")', js)

    def test_health_reports_bundle_fingerprint(self):
        from prism.rpc import server as rpc_server
        fp = rpc_server.gui_bundle_fingerprint()
        self.assertRegex(fp, r"^[0-9a-f]{16}$")


class TestGuiClassContract(unittest.TestCase):
    """Gate 2 (v0.1.9 post-mortem): index.html/app.js reference a rich CSS
    class vocabulary (glass, aurora, stepper, toast, confetti, hero...).
    theme.css silently fell behind — ~50 classes rendered as raw unstyled
    markup, which is exactly what made the shipped UI look "nineties" even
    though the markup was modern.  Every class used by the GUI must have at
    least one rule in theme.css; this pin fails CI the moment they drift."""

    _CLASS_ATTR = re.compile(r'class="([^"]+)"')
    _JS_CLASS = re.compile(r"classList\.(?:add|toggle|remove)\('([^']+)'\)")

    def _used_classes(self):
        gui = ROOT.joinpath("prism", "gui")
        html = open(gui / "index.html", encoding="utf-8").read()
        js = open(gui / "app.js", encoding="utf-8").read()
        used = set()
        for m in self._CLASS_ATTR.finditer(html):
            used.update(m.group(1).split())
        for m in self._JS_CLASS.finditer(js):
            used.add(m.group(1))
        # JS also toggles these state classes via string literals
        used.update({"screen-enter", "show", "weak", "done", "active",
                     "picked", "calm", "caution", "pause"})
        return used

    def test_gui_class_contract(self):
        css = open(ROOT.joinpath("prism", "gui", "theme.css"),
                   encoding="utf-8").read()
        missing = sorted(c for c in self._used_classes()
                         if not re.search(r"\." + re.escape(c) + r"\b", css))
        self.assertFalse(
            missing,
            "prism/gui/theme.css has NO rule for these classes referenced by "
            "index.html/app.js — they would render unstyled (the v0.1.9 "
            f"'nineties look' bug): {missing}")

    def test_captcha_wire_ships_visible_tiles_only(self):
        # v0.1.8 lesson: shipping an `answer` shortcut on the wire is an
        # answer-key leak by another name (client could auto-solve without
        # looking).  The GUI must derive picks from `tiles` alone.
        from prism.rpc.service import PrismService
        svc = PrismService()
        svc.auth_create("gate password 123!")
        svc.mine(3)
        it = svc.intent_compile("send 1 PRSM to Alex")
        svc.intent_confirm(it["intent_id"])
        ch = svc.auth_challenge(it["intent_id"])
        for leaked in ("target", "answer"):
            self.assertNotIn(leaked, ch,
                             f"'{leaked}' must NEVER cross the captcha wire")
        tiles = ch["tiles"]
        shape = max(set(tiles), key=tiles.count)
        solved = svc.auth_solve(ch["challenge_id"],
                                [i for i, s in enumerate(tiles) if s == shape],
                                ch["digest_hex"])
        self.assertTrue(solved["ok"])

    def test_captcha_svg_carries_wire_stamp(self):
        # v0.1.9 post-mortem: the shipped GUI bundle can lag the server build
        # inside a frozen binary (stale static/ copy).  The tile list is now
        # ALSO stamped inside the SVG picture itself (`<!--prism-tiles:...-->`)
        # so app.js can always recover it; if the stamp ever disappears the
        # client shows a clear version-mismatch error instead of an endless
        # "not quite — tap every matching shape" loop.
        from prism.auth.captcha import new_image_grid, render_grid_svg
        g = new_image_grid()
        svg = render_grid_svg(g["tiles"], g["target"])
        import re
        m = re.search(r"<!--prism-tiles:([a-z,]+)-->", svg)
        self.assertIsNotNone(m, "SVG must carry the prism-tiles wire stamp")
        self.assertEqual(m.group(1).split(","), g["tiles"])
        # stamp contains every tile and nothing more: exactly 9 shapes
        self.assertEqual(len(m.group(1).split(",")), 9)
