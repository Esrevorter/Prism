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
    return found


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
    if fail:
        print("RELEASE GATE FAILED:", file=sys.stderr)
        print("\n".join(fail), file=sys.stderr)
        return 1
    print(f"release gate OK — no tracked artifacts, versions {sorted(set(versions.values())) or 'n/a'}, "
          "captcha wire contract intact")
    return 0


if __name__ == "__main__":
    sys.exit(main())
