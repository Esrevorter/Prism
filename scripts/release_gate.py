#!/usr/bin/env python3
"""Release gate — prevents the v0.1.7 regressions from recurring.

Checks (exit non-zero + plain-language report on any failure):
  1. No build artifacts tracked in git (v0.1.7 shipped 28 committed
     PyInstaller files: build/, buildtest/, dist/, disttest/, prism-*/).
  2. Version consistency: every version location must equal the release
     tag (v0.1.7 disaster: a release page said 0.1.0 while the tree was
     0.1.7 — bump all five places or none).
  3. Captcha wire contract: auth.challenge payload fields the GUI needs
     (`tiles`, `svg`, `prompt`) are present and the answer key (`target`)
     is NOT leaked to the wire (the broken-captcha post-mortem).

Usage: python scripts/release_gate.py [expected_version]   (tag vX.Y.Z -> X.Y.Z)
Run it locally before tagging, and it also runs in ci.yml on every push/PR.
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARTIFACT_PREFIXES = ("build/", "buildtest/", "dist/", "disttest/")
ARTIFACT_RE = re.compile(r"^prism-\d+\.\d+\.\d+-[a-z0-9_-]+/")


def _git(*args):
    return subprocess.run(["git", "-C", ROOT, *args], capture_output=True,
                          text=True).stdout.splitlines()


def check_artifacts(fail):
    tracked = [f for f in _git("ls-files")
               if f.startswith(ARTIFACT_PREFIXES) or ARTIFACT_RE.match(f)]
    if tracked:
        fail.append("BUILD ARTIFACTS TRACKED IN GIT (never commit these; "
                    "add to .gitignore and `git rm -r --cached`):")
        fail.extend("  " + f for f in tracked)


def check_versions(tag, fail):
    locs = {
        "pyproject.toml": r'^version = "([^"]+)"',
        "prism/chain/__init__.py": r'__version__ = "([^"]+)"',
        "packaging/tauri/prism-gui/src-tauri/tauri.conf.json": r'"version":\s*"([^"]+)"',
        "packaging/tauri/prism-gui/src-tauri/Cargo.toml": r'^version = "([^"]+)"',
    }
    found = {}
    for path, rx in locs.items():
        full = os.path.join(ROOT, path)
        if not os.path.isfile(full):
            continue  # location absent from this checkout (e.g. desktop/package.json)
        m = re.search(rx, open(full).read(), re.M)
        found[path] = m.group(1) if m else "<missing>"
    values = set(found.values())
    if len(values) != 1:
        fail.append("VERSION MISMATCH across release locations:")
        for p, v in sorted(found.items()):
            fail.append(f"  {p} = {v}")
    if tag and found and values != {tag}:
        fail.append(f"release tag v{tag} but tree versions are {sorted(values)} "
                    "(bump ALL locations in the same commit as the tag)")
    check_visible_version(found, fail)
    return found


def check_visible_version(found, fail):
    """Rules v1.3 §5: the version must be VISIBLE IN THE RUNNING APP.

    Root cause of the 'nothing looks fixed' complaints: installers carried no
    user-visible build stamp, so a stale front end inside a new-tagged binary
    was indistinguishable from the fix.  The gate now requires:
      1. index.html has a #build-version element (footer stamp),
      2. app.js populates it from /health (version + gui_bundle fingerprint),
      3. server.py's /health exposes both fields,
      4. README release badge equals the version in the tree (no stale v0.1.5).
    """
    idx = os.path.join(ROOT, "prism", "gui", "index.html")
    js = os.path.join(ROOT, "prism", "gui", "app.js")
    srv = os.path.join(ROOT, "prism", "rpc", "server.py")
    for path, needle, what in (
        (idx, 'id="build-version"', "user-visible version stamp element"),
        (js, "#build-version", "app.js wiring that paints the version stamp"),
        (js, "/health", "app.js fetching /health for the running build's stamp"),
        (srv, '"version": __version__', "/health exposing backend version"),
        (srv, '"gui_bundle"', "/health exposing the served-bundle fingerprint"),
    ):
        text = ""
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        if needle not in text:
            fail.append(f"MISSING {what}: '{needle}' not found in {path} "
                        "(rules v1.3 §5 — releases without a visible stamp "
                        "cannot prove to users which build they run)")
    readme = os.path.join(ROOT, "README.md")
    if os.path.isfile(readme):
        m = re.search(r'\[!\[Release\]\(https://img\.shields\.io/badge/release-v([0-9.]+)',
                      open(readme, encoding="utf-8").read())
        badge = m.group(1) if m else "<missing>"
        tree = {v for v in found.values() if v != "<missing>"}
        if tree and badge not in tree:
            fail.append(f"STALE README BADGE: badge says v{badge} but tree is "
                        f"{sorted(tree)} — update the badge in the version-bump commit")


def check_captcha_wire(fail):
    sys.path.insert(0, ROOT)
    try:
        from prism.rpc.service import PrismService
    except Exception as e:  # pragma: no cover
        fail.append(f"cannot import PrismService for wire check: {e!r}")
        return
    svc = PrismService()
    svc.auth_create("gate password 123!")
    svc.mine(3)
    it = svc.intent_compile("send 1 PRSM to Alex")
    svc.intent_confirm(it["intent_id"])
    ch = svc.auth_challenge(it["intent_id"])
    for field in ("challenge_id", "prompt", "svg", "tiles", "digest_hex"):
        if field not in ch:
            fail.append(f"captcha wire contract: auth.challenge missing '{field}' "
                        "(GUI cannot render/answer without it — v0.1.7 outage)")
    if "target" in ch:
        fail.append("captcha wire contract: 'target' ANSWER KEY LEAKED to client")
    if "tiles" in ch:
        tiles = ch["tiles"]
        from collections import Counter
        c = Counter(tiles)
        top, n = c.most_common(1)[0]
        second = c.most_common(2)[1][1] if len(c) > 1 else 0
        if n < 3 or second >= n:
            fail.append(f"captcha grid ambiguous ({dict(c)}): prompt target is not "
                        "the strict visual majority — honest users will fail")
    # v0.1.9 post-mortem: frozen binaries can ship a STALE GUI bundle that
    # predates server fixes. The tile list must therefore also be stamped
    # inside the picture (SVG comment) so app.js can always recover it, and
    # app.js must actually parse that stamp. Pin both halves here.
    import re as _re
    m = _re.search(r"<!--prism-tiles:([a-z]+(?:,[a-z]+){8})-->", ch.get("svg", ""))
    if not m:
        fail.append("captcha wire contract: SVG missing prism-tiles stamp "
                    "(stale bundled GUI cannot recover tiles -> unsolvable puzzle)")
    elif m.group(1).split(",") != ch.get("tiles"):
        fail.append("captcha wire contract: SVG tile stamp disagrees with wire 'tiles'")
    appjs = os.path.join(ROOT, "prism", "rpc", "static", "app.js")
    try:
        with open(appjs, encoding="utf-8") as f:
            src = f.read()
        if "prism-tiles" not in src:
            fail.append("GUI regression: app.js does not parse the prism-tiles "
                        "stamp — old bundled clients break on any field drift")
    except OSError as e:  # pragma: no cover
        fail.append(f"cannot read {appjs}: {e}")


def check_brand_icon(fail):
    """v0.1.12 post-mortem: the Windows taskbar icon shipped as a solid black
    square — every icon file in the repo was a uniform dark tile with no
    glyph at all.  The brand (golden P) is generated by scripts/make_icon.py;
    this gate refuses releases where any icon regressed to a blank tile."""
    icons_dir = os.path.join(ROOT, "packaging", "tauri", "prism-gui",
                             "src-tauri", "icons")
    try:
        from PIL import Image
        import numpy as np
    except ImportError as e:  # pragma: no cover
        fail.append(f"brand-icon gate: cannot import PIL/numpy: {e}")
        return
    for rel in ("icon.png", "icon.ico"):
        path = os.path.join(icons_dir, rel)
        if not os.path.exists(path):
            fail.append(f"brand-icon gate: {rel} missing")
            continue
        arr = np.array(Image.open(path).convert("RGBA"))[..., :3]
        bright = float((arr.max(axis=2) > 120).mean())
        if bright < 0.05:
            fail.append(f"brand-icon gate: {rel} is a blank/black tile "
                        f"({bright:.1%} bright pixels) — run "
                        "`python scripts/make_icon.py` and commit the output")


def main():
    tag = None
    if len(sys.argv) > 1:
        tag = sys.argv[1].lstrip("vV")
    elif os.environ.get("GITHUB_REF_NAME", "").startswith("v"):
        tag = os.environ["GITHUB_REF_NAME"].lstrip("v")
    fail = []
    check_artifacts(fail)
    versions = check_versions(tag, fail)
    check_captcha_wire(fail)
    check_brand_icon(fail)
    if fail:
        print("RELEASE GATE FAILED:", file=sys.stderr)
        print("\n".join(fail), file=sys.stderr)
        return 1
    print(f"release gate OK — no tracked artifacts, versions {sorted(set(versions.values())) or 'n/a'}, "
          "captcha wire contract intact")
    return 0


if __name__ == "__main__":
    sys.exit(main())
