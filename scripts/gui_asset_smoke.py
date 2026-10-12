#!/usr/bin/env python3
"""Gate 2 (docs/ATTESTATION.md §6): smoke-test the GUI ASSETS, not just RPC.

History: v0.1.5/v0.1.6 shipped a broken captcha screen on Windows because
every automated test drove JSON-RPC directly — none ever fetched the served
HTML/CSS/JS or asserted what the user actually sees.  This script closes
that gap and runs in CI (ci.yml) and after every installer build
(desktop.yml).  Exit non-zero = do not publish.

Checks (all against a live server, exactly as the webview would load them):
  A. GET / serves index.html; every referenced local asset resolves 200 with
     a sane content type (a missing stylesheet is precisely how the old
     "square + black circle" bug appeared: unstyled overlay markup).
  B. theme.css defines every class the captcha stage uses (cap-tile,
     cap-check, picked, ring/ring-fg/ring-bg, cap-svg...).
  C. The captcha markup is vector-first: #cap-svg container exists, no
     <img id="captcha-img"> second picture anywhere.
  D. auth.challenge response carries the fields app.js needs to paint one
     coherent SVG (tiles/target/expires_at/digest_hex/challenge_id/prompt).
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prism.rpc import server as rpc_server          # noqa: E402
from prism.rpc.service import PrismService          # noqa: E402

PW = "smoke test passphrase 42!"


def fail(msg: str) -> None:
    print(f"GUI-ASSET-SMOKE FAIL: {msg}")
    sys.exit(1)


def main() -> int:
    rpc_server.SERVICE = PrismService()
    httpd = rpc_server.ThreadingHTTPServer(("127.0.0.1", 0),
                                           rpc_server.RPCRequestHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def get(path):
        with urllib.request.urlopen(base + path, timeout=5) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read()

    def call(method, params=None):
        req = urllib.request.Request(
            base + "/jsonrpc",
            data=json.dumps({"jsonrpc": "2.0", "method": method,
                             "params": params or {}, "id": 1}).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as r:
            out = json.loads(r.read())
        if "error" in out:
            raise RuntimeError(f"{method}: {out['error']}")
        return out["result"]

    # --- health reports the bundle fingerprint -----------------------------
    st, _, body = get("/health")
    hp = json.loads(body)
    if st != 200 or "gui_bundle" not in hp:
        fail("/health must expose gui_bundle fingerprint (got %r)" % hp)

    # --- A. index + every referenced local asset resolves ------------------
    st, ctype, htmlb = get("/")
    html = htmlb.decode("utf-8")
    if st != 200 or "text/html" not in ctype:
        fail("GET / did not serve the GUI bundle")
    refs = set(re.findall(r'(?:href|src)="(/static/[^"]+)"', html))
    for name in ("theme.css", "app.js"):
        if f"/static/{name}" not in html:
            fail(f"index.html does not reference /static/{name} — the GUI "
                 f"cannot render styled without it (this caused the v0.1.6 "
                 f"'square + black circle' regression)")
        refs.add(f"/static/{name}")
    for ref in sorted(refs):
        s2, ct2, b2 = get(ref)
        if s2 != 200 or len(b2) < 50:
            fail(f"referenced asset {ref} -> HTTP {s2}, {len(b2)} bytes")
        if "octet-stream" in ct2:
            fail(f"asset {ref} served as octet-stream — browser will not "
                 f"apply it as {'stylesheet' if ref.endswith('css') else 'script'}")

    css = get("/static/theme.css")[2].decode("utf-8")
    js = get("/static/app.js")[2].decode("utf-8")

    # --- B. captcha-critical classes exist IN THE SERVED CSS ---------------
    # Gate B: every class the served HTML puts on the captcha stage must be
    # styled by the served CSS.  A missing stylesheet/class is precisely how
    # the "square + big black circle" regression appeared on Windows: the
    # overlay markup rendered unstyled.  Derived from the markup itself, so
    # it cannot drift out of sync with the test.
    cap_html = html[html.index('id="captcha-stage"'):]
    cap_html = cap_html[:cap_html.index("</section>")]
    used = set()
    for attr in re.findall(r'class="([^"]+)"', cap_html):
        used.update(attr.split())
    for cls in sorted(used):
        if cls == "glass":
            continue          # documented optional decoration hook
        if "." + cls not in css:
            fail(f"theme.css lacks .{cls} used by the captcha stage — it "
                 f"would render unstyled (the exact failure mode users "
                 f"reported)")

    # --- C. vector-first single-picture guarantee --------------------------
    if '<img id="captcha-img"' in html:
        fail("legacy <img> captcha markup present — must be one inline SVG")
    if 'id="cap-svg"' not in html or "renderCapSvg" not in js:
        fail("vector-first captcha renderer missing from served bundle")

    # --- D. live challenge payload drives that renderer --------------------
    call("auth.create", {"password": PW})
    call("chain.mine", {"count": 3})
    it = call("intent.compile", {"utterance": "send 1 PRSM to Alex"})
    call("intent.confirm", {"intent_id": it["intent_id"]})
    ch = call("auth.challenge", {"intent_id": it["intent_id"]})
    for field in ("challenge_id", "prompt", "digest_hex", "expires_at",
                  "tiles", "target"):
        if field not in ch:
            fail(f"auth.challenge response missing '{field}' the GUI needs")
    if len(ch["tiles"]) != 9:
        fail(f"expected 9 tiles, got {len(ch['tiles'])}")

    print(f"GUI-ASSET-SMOKE OK  (bundle {hp['gui_bundle']}, "
          f"{len(refs)} assets, captcha vector-first, wire fields pinned)")
    httpd.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
