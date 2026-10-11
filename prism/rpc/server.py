"""JSON-RPC 2.0 daemon over stdlib http.server (loopback-only, dev posture).

Run:  python -m prism.rpc.server [--port 8765] [--host 127.0.0.1]

Serves POST /jsonrpc and GET /health from a shared PrismService instance.
Also serves the GUI static bundle at GET / once prism/gui/index.html is
built, so `python -m prism.rpc.server` alone boots the whole dev stack.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

if __package__ in (None, ""):        # allow `python prism/rpc/server.py`
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from rpc.service import PrismService
else:
    from .service import PrismService

GUI_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "gui")

SERVICE = PrismService()             # one process-wide devnet instance

# method -> (callable(params-dict) or attr name on SERVICE)
def _dispatch(method: str, params: dict):
    if method == "health.ping":
        return {"status": "ok", "version": "0.1.0",
                "network": SERVICE.params.name,
                "tip_height": SERVICE.chain.tip.header.height}
    table = {
        "chain.info":            lambda p: SERVICE.chain_info(),
        "chain.mine":            lambda p: SERVICE.mine(int(p.get("count", 1))),
        "wallet.balance":        lambda p: SERVICE.balance(),
        "contacts.list":         lambda p: {"contacts": SERVICE.contacts_list()},
        "contacts.add":          lambda p: SERVICE.contact_add(
                                           str(p["name"]), str(p["address"])),
        "intent.compile":        lambda p: SERVICE.intent_compile(str(p["utterance"])),
        "intent.answer":         lambda p: SERVICE.intent_answer(
                                           str(p["intent_id"]), str(p["answer"])),
        "intent.simulate":       lambda p: SERVICE.intent_simulate(str(p["intent_id"])),
        "intent.confirm":        lambda p: SERVICE.intent_confirm(str(p["intent_id"])),
        "mpc.ceremony_start":    lambda p: SERVICE.ceremony_start(),
        "mpc.ceremony_status":   lambda p: SERVICE.ceremony_status(),
        "mpc.sign_preview":      lambda p: SERVICE.sign_preview(str(p["intent_id"])),
    }
    fn = table.get(method)
    if fn is None:
        raise LookupError(f"method not found: {method}")
    return fn(params)


class RPCRequestHandler(BaseHTTPRequestHandler):
    server_version = "PrismRPC/0.1"

    # ---- plumbing ----
    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        origin = self.headers.get("Origin", "")
        if origin.startswith("http://localhost") or origin.startswith("http://127.0.0.1"):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):               # quieter dev logs
        pass

    def do_OPTIONS(self):
        self._send(204, b"", "text/plain")

    # ---- routes ----
    def do_GET(self):
        if self.path == "/health":
            body = json.dumps({"status": "ok"}).encode()
            return self._send(200, body, "application/json")
        if self.path in ("/", "/index.html"):
            return self._serve_static("index.html")
        if self.path.startswith("/static/"):
            return self._serve_static(self.path[len("/static/"):])
        self._send(404, b'{"error":"not found"}', "application/json")

    def _serve_static(self, rel: str):
        safe = os.path.normpath(rel).lstrip("./")
        path = os.path.join(GUI_DIR, safe)
        if not path.startswith(GUI_DIR) or not os.path.isfile(path):
            hint = b"GUI bundle not built yet - see prism/gui/README.md"
            return self._send(404, hint, "text/plain")
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        with open(path, "rb") as fh:
            self._send(200, fh.read(), ctype)

    def do_POST(self):
        if self.path != "/jsonrpc":
            return self._send(404, b'{"error":"not found"}', "application/json")
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        try:
            req = json.loads(raw)
            rid = req.get("id")
            method = str(req.get("method", ""))
            params = req.get("params") or {}
            if not isinstance(params, dict):
                raise ValueError("params must be an object")
            result = _dispatch(method, params)
            resp = {"jsonrpc": "2.0", "result": result, "id": rid}
        except LookupError as e:
            resp = {"jsonrpc": "2.0", "id": locals().get("rid"),
                    "error": {"code": -32601, "message": str(e)}}
        except (ValueError, KeyError) as e:
            resp = {"jsonrpc": "2.0", "id": locals().get("rid"),
                    "error": {"code": -32000, "message": str(e)}}
        except Exception as e:                        # pragma: no cover
            resp = {"jsonrpc": "2.0", "id": locals().get("rid"),
                    "error": {"code": -32603, "message": f"internal error: {e}"}}
        self._send(200, json.dumps(resp).encode(), "application/json")


def serve(host: str = "127.0.0.1", port: int = 8765):
    httpd = ThreadingHTTPServer((host, port), RPCRequestHandler)
    print(f"Prism RPC devnet listening on http://{host}:{port} "
          f"(network={SERVICE.params.name}, tip={SERVICE.chain.tip.header.height})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="prism-rpc")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ns = ap.parse_args(argv)
    serve(ns.host, ns.port)


if __name__ == "__main__":
    main()
