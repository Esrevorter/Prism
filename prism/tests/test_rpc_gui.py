"""GUI/RPC scaffolding tests (spec §13 Phase-2 precursor).

Drives PrismService directly (no sockets) plus one in-process HTTP round
trip through the real ThreadingHTTPServer to pin the JSON-RPC contract the
GUI depends on.
"""
from __future__ import annotations

import json
import threading
import urllib.request

import pytest

from prism.rpc.service import PrismService
from prism.rpc import server as rpc_server


@pytest.fixture()
def svc():
    s = PrismService()
    s.mine(3)
    return s


class TestChainFaucet:
    def test_mine_credits_balance(self, svc):
        bal = svc.balance()
        assert bal["balance_shard"] > 0
        assert bal["balance_prsm"].count(".") == 1

    def test_mine_budget_guard(self, svc):
        with pytest.raises(ValueError):
            svc.mine(51)

    def test_chain_info_honest_virtual_mode(self, svc):
        info = svc.chain_info()
        assert info["virtual_mode"] is True
        assert info["network"] == "refraction"


class TestIntentFlow:
    def test_clear_utterance_reaches_confirm_and_signs(self, svc):
        svc.ceremony_start()
        it = svc.intent_compile("send 2 PRSM to Alex")
        assert it["status"] == "awaiting_confirm"
        done = svc.intent_confirm(it["intent_id"])
        assert "canonical_digest_hex" in done["confirmation_envelope"]
        signed = svc.sign_preview(it["intent_id"])
        assert all(q["verifies"] for q in signed["quorums"].values())
        assert len(signed["quorums"]) == 3      # every 2-of-3 quorum works

    def test_vague_utterance_gets_chips_then_resolves(self, svc):
        d = svc.intent_compile("pay mom")
        assert d["status"] == "draft" and d["clarify"]
        r = svc.intent_answer(d["intent_id"], "$10")
        assert r["status"] == "awaiting_confirm"

    def test_unknown_intent_raises(self, svc):
        with pytest.raises(ValueError):
            svc.intent_simulate("nope")

    def test_confirm_requires_ready_state(self, svc):
        d = svc.intent_compile("pay mom")     # still draft
        with pytest.raises(ValueError):
            svc.intent_confirm(d["intent_id"])


class TestCeremony:
    def test_staged_progress_and_pk(self, svc):
        st = svc.ceremony_start()
        assert st["status"] == "complete" and st["percent"] == 100
        assert len(bytes.fromhex(st["joint_pk_hex"])) == 32

    def test_sign_without_ceremony_refused(self, svc):
        it = svc.intent_compile("send 1 PRSM to Alex")
        svc.intent_confirm(it["intent_id"])
        with pytest.raises(ValueError):
            svc.sign_preview(it["intent_id"])


class TestContacts:
    def test_add_and_list(self, svc):
        before = len(svc.contacts_list())
        svc.contact_add("Dana", "prsm1dana0000000000000000000000000000000")
        assert len(svc.contacts_list()) == before + 1
        with pytest.raises(ValueError):
            svc.contact_add("Eve", "btc1something")


class TestRPCOverHTTP:
    """One real socket round trip pins the wire contract."""

    @pytest.fixture()
    def live(self, monkeypatch):
        monkeypatch.setattr(rpc_server, "SERVICE", PrismService())
        httpd = rpc_server.ThreadingHTTPServer(("127.0.0.1", 0),
                                               rpc_server.RPCRequestHandler)
        th = threading.Thread(target=httpd.serve_forever, daemon=True)
        th.start()
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
        httpd.shutdown()
        th.join(timeout=2)

    def _call(self, base, method, params=None):
        req = urllib.request.Request(
            base + "/jsonrpc",
            data=json.dumps({"jsonrpc": "2.0", "method": method,
                             "params": params or {}, "id": 1}).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read())

    def test_health_and_full_flow_over_http(self, live):
        with urllib.request.urlopen(live + "/health", timeout=5) as r:
            assert json.loads(r.read())["status"] == "ok"
        assert self._call(live, "chain.mine", {"count": 2})["result"]["mined"] == 2
        assert self._call(live, "mpc.ceremony_start")["result"]["status"] == "complete"
        it = self._call(live, "intent.compile",
                        {"utterance": "send 1 PRSM to Alex"})["result"]
        conf = self._call(live, "intent.confirm",
                          {"intent_id": it["intent_id"]})["result"]
        assert conf["confirmation_envelope"]["canonical_digest_hex"]
        signed = self._call(live, "mpc.sign_preview",
                            {"intent_id": it["intent_id"]})["result"]
        assert "2 of your 3 devices" in signed["human"]

    def test_method_not_found_wire_format(self, live):
        resp = self._call(live, "bogus.method")
        assert resp["error"]["code"] == -32601

    def test_gui_bundle_served(self, live):
        with urllib.request.urlopen(live + "/", timeout=5) as r:
            html = r.read().decode()
        assert "What would you like to do?" in html
        with urllib.request.urlopen(live + "/static/app.js", timeout=5) as r:
            assert b"jsonrpc" in r.read()
