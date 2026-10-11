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

if getattr(sys, "frozen", False):
    # PyInstaller onefile: the GUI static bundle is unpacked into
    # sys._MEIPASS/prism/rpc/static by packaging/prism_gui.spec (datas entry).
    _CANDIDATES = [os.path.join(sys._MEIPASS, "prism", "rpc", "static"),
                   os.path.join(sys._MEIPASS, "prism", "gui")]
else:
    _here = os.path.dirname(os.path.abspath(__file__))
    _CANDIDATES = [os.path.join(_here, "static"),               # packaged/installed
                   os.path.join(os.path.dirname(_here), "gui")]  # source tree
GUI_DIR = next((p for p in _CANDIDATES if os.path.isfile(os.path.join(p, "index.html"))),
               _CANDIDATES[-1])

SERVICE = PrismService()             # one process-wide devnet instance

# method -> (callable(params-dict) or attr name on the PrismService instance)
def _dispatch(method: str, params: dict, svc: "PrismService | None" = None):
    S = svc if svc is not None else SERVICE
    if method == "health.ping":
        return {"status": "ok", "version": "0.1.0",
                "network": S.params.name,
                "tip_height": S.chain.tip.header.height}
    table = {
        "chain.info":            lambda p: S.chain_info(),
        "chain.mine":            lambda p: S.mine(int(p.get("count", 1))),
        "chain.blocks":          lambda p: S.chain_blocks(
                                           int(p.get("limit", 10))),
        "wallet.balance":        lambda p: S.balance(),
        "contacts.list":         lambda p: {"contacts": S.contacts_list()},
        "contacts.add":          lambda p: S.contact_add(
                                           str(p["name"]), str(p["address"])),
        "intent.compile":        lambda p: S.intent_compile(str(p["utterance"])),
        "intent.answer":         lambda p: S.intent_answer(
                                           str(p["intent_id"]), str(p["answer"])),
        "intent.simulate":       lambda p: S.intent_simulate(str(p["intent_id"])),
        "intent.review":         lambda p: S.intent_review(
                                           str(p["intent_id"]),
                                           acknowledged_risk=bool(
                                               p.get("acknowledged_risk"))),
        "intent.confirm":        lambda p: S.intent_confirm(
                                           str(p["intent_id"]),
                                           acknowledged_risk=bool(
                                               p.get("acknowledged_risk"))),
        "mpc.ceremony_start":    lambda p: S.ceremony_start(),
        "mpc.ceremony_status":   lambda p: S.ceremony_status(),
        "mpc.ceremony_reset":    lambda p: S.ceremony_reset(),
        "mpc.sign_preview":      lambda p: S.sign_preview(str(p["intent_id"])),
        "recovery.status":       lambda p: S.recovery_status(),
        "recovery.attach_social": lambda p: S.recovery_attach_social(),
        "recovery.initiate":     lambda p: S.recovery_initiate(
                                           [int(i) for i in p.get("collectors", [])],
                                           [int(i) for i in p.get("stale_legs", [])]),
        "recovery.cancel":       lambda p: S.recovery_cancel(
                                           int(p["share_index"])),
        "recovery.finalize":     lambda p: S.recovery_finalize(),
        "recovery.advance_window": lambda p: S.recovery_advance_window(
                                           float(p.get("hours", 73.0))),
        "agent.grant_create":    lambda p: S.grant_create(
                                           days=int(p.get("days", 30)),
                                           per_action_prsm=float(p.get(
                                               "per_action_prsm", 25.0)),
                                           monthly_prsm=float(p.get(
                                               "monthly_prsm", 400.0)),
                                           price_ceiling_usd=(
                                               float(p["price_ceiling_usd"])
                                               if p.get("price_ceiling_usd")
                                               is not None else None),
                                           comfort_prsm=(
                                               float(p["comfort_prsm"])
                                               if p.get("comfort_prsm")
                                               is not None else None),
                                           veto_hours=float(p.get(
                                               "veto_hours", 24.0))),
        "agent.propose":         lambda p: S.agent_propose(
                                           str(p["utterance"]),
                                           p.get("grant_id")),
        "agent.pending":         lambda p: S.agent_pending(),
        "agent.veto":            lambda p: S.agent_veto(
                                           str(p["action_id"])),
        "agent.revoke":          lambda p: S.agent_revoke(
                                           p.get("grant_id")),
        "agent.tick":            lambda p: S.agent_tick(
                                           hours_ahead=float(
                                               p.get("hours_ahead", 0.0))),
        "agent.report":          lambda p: S.agent_report(
                                           p.get("grant_id")),
    }
    fn = table.get(method)
    if fn is None:
        raise LookupError(f"method not found: {method}")
    return fn(params)


class RPCRequestHandler(BaseHTTPRequestHandler):
    server_version = "PrismRPC/0.1"

    # Per-server service injected by ThreadingHTTPServer subclasses; falls
    # back to the module-level singleton for the classic `prism-rpc` path.
    @property
    def service(self) -> PrismService:
        return getattr(self.server, "service", SERVICE)

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
            result = _dispatch(method, params, svc=self.service)
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


def serve(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = False):
    httpd = ThreadingHTTPServer((host, port), RPCRequestHandler)
    url = f"http://{host}:{port}/"
    print(f"Prism RPC devnet listening on {url} "
          f"(network={SERVICE.params.name}, tip={SERVICE.chain.tip.header.height})")
    if open_browser:
        # Standalone GUI launch: one command boots daemon + wallet UI.
        import webbrowser
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


class BoundHTTPServer(ThreadingHTTPServer):
    """ThreadingHTTPServer carrying its own PrismService instance so the
    `prism-gui` launcher can bind a fresh per-user wallet without touching
    the module-level singleton used by the classic `prism-rpc` path."""

    def __init__(self, host: str, port: int, service: "PrismService"):
        super().__init__((host, port), RPCRequestHandler)
        self.service = service


def serve_with_callback(host: str = "127.0.0.1", port: int = 8765,
                        on_ready=None, service: "PrismService | None" = None):
    """Programmatic serve (used by the zero-argument `prism-gui` launcher).

    `on_ready()` runs in a short-lived thread immediately after the socket is
    bound — the listener is already accepting before the callback fires, so
    anything the callback does (health probe + browser open) never races the
    server. Returns only after serve_forever() exits (Ctrl-C / KeyboardInterrupt).
    """
    httpd = BoundHTTPServer(host, port, service if service is not None
                            else SERVICE)
    if on_ready is not None:
        import threading
        threading.Thread(target=on_ready, daemon=True).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="prism-rpc",
        description="Prism GUI wallet daemon: serves the JSON-RPC API and the "
                    "zero-build wallet front end on loopback. This single "
                    "command is the standalone desktop entry point.")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--open", dest="open_browser", action="store_true",
                    help="open the wallet UI in the default browser once the "
                         "daemon is listening (standalone GUI mode)")
    ns = ap.parse_args(argv)
    serve(ns.host, ns.port, open_browser=ns.open_browser)


if __name__ == "__main__":
    main()
