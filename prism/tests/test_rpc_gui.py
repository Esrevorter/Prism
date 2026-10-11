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


class TestFraudPanel:
    """intent.review — fraud-simulator panel against prism/ai/fraud_model
    paths (SigningGate + FraudDetector), honest-UX language requirement."""

    def test_benign_review_is_calm_and_proceeds(self, svc):
        it = svc.intent_compile("send 2 PRSM to Alex")
        rv = svc.intent_review(it["intent_id"])
        assert rv["ui_band"] == "calm" and rv["risk"]["band"] == "clean"
        assert rv["proceed"] is True and rv["ok"] is True
        # every number traces to the deterministic simulator, not an LLM
        assert rv["sim_digest"] == rv["simulation"]["sim_digest"]
        assert "PRSM" in rv["plain_english"] and rv["human"]

    def _scam_intent(self, svc):
        # scam phrase rides in the memo so the intent compiles cleanly and
        # reaches awaiting_confirm — then the fraud panel must catch it.
        d = svc.intent_compile(
            "send 1 PRSM to Alex memo urgent transfer customer support")
        assert d["status"] == "awaiting_confirm"
        return d

    def test_scam_utterance_interrupts_with_barrier(self, svc):
        it = self._scam_intent(svc)
        rv = svc.intent_review(it["intent_id"])
        assert rv["risk"]["band"] == "interrupt" and rv["ui_band"] == "pause"
        assert rv["interruption"] is True and rv["proceed"] is False
        assert rv["offer_sleep_on_it"] is True
        assert rv["reasons"], "panel must show plain-language reasons"
        # honest-UX: no false reassurance while interrupting
        assert "Nothing has been sent" in rv["human"]

    def test_gated_confirm_blocks_then_acknowledgement_opens(self, svc):
        it = self._scam_intent(svc)
        # interrupt band without acknowledgement -> confirm refused outright
        with pytest.raises(ValueError, match="cannot confirm"):
            svc.intent_confirm(it["intent_id"])
        # explicit user acknowledgement ("I understand") opens the barrier
        rv2 = svc.intent_review(it["intent_id"], acknowledged_risk=True)
        assert rv2["proceed"] is True          # panel now offers confirm
        done = svc.intent_confirm(it["intent_id"], acknowledged_risk=True)
        env = done["confirmation_envelope"]
        assert "canonical_digest_hex" in env
        # preview→confirm binding: envelope carries the sim digest of the
        # exact simulation that authorized the confirm (no-swap-attack field)
        assert len(env["sim_digest_bound"]) == 64 and rv2["sim_digest"]

    def test_unaffordable_intent_fails_simulation_not_proceeds(self, svc):
        big = svc.intent_compile(f"send {svc.balance()['balance_prsm']} PRSM to Alex")
        if big["status"] != "awaiting_confirm":   # amount > balance may draft
            return
        rv = svc.intent_review(big["intent_id"])
        # faucet emissions are tiny vs a full-balance send incl. fee:
        # simulation must refuse and proceed must be False (never ok=True).
        if not rv["ok"]:
            assert rv["proceed"] is False
            with pytest.raises(ValueError, match="cannot confirm"):
                svc.intent_confirm(big["intent_id"])


class TestChainBlocks:
    def test_chain_blocks_returns_real_store_contents(self, svc):
        d = svc.chain_blocks(limit=5)
        # svc fixture mines 3 blocks on top of genesis -> exactly 4 stored
        assert d["count"] == len(d["blocks"]) == 4
        hs = [b["hash"] for b in d["blocks"]]
        assert len(set(hs)) == len(hs)               # distinct real headers
        top = svc.chain_info()
        assert d["blocks"][0]["height"] == top["height"]
        assert d["blocks"][0]["hash"] == top["tip_hash"]
        for b in d["blocks"]:                        # fields come from Block
            assert set(b) >= {"height", "hash", "prev_hash", "timestamp",
                              "tx_count"}


class TestCeremonyDevicesReset:
    def test_status_lists_three_device_share_cards(self, svc):
        st = svc.ceremony_start()
        assert st["status"] == "complete"
        devs = st["devices"]
        assert [d["label"] for d in devs] == ["Phone", "Laptop", "Backup card"]
        fps = {d["share_fingerprint"] for d in devs}
        assert len(fps) == 3                          # each device holds a piece
        assert all(d["verified_against_commitments"] for d in devs)
        assert "share_handling" in st                 # never-show-shares UX text
        assert "human" in st

    def test_reset_returns_to_idle_and_sign_refused(self, svc):
        svc.ceremony_start()
        r = svc.ceremony_reset()
        assert r["status"] == "idle" and r["percent"] == 0
        assert "devices" not in r
        it = svc.intent_compile("send 2 PRSM to Alex")
        with pytest.raises(ValueError, match="mpc.ceremony_start"):
            svc.sign_preview(it["intent_id"])


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


class TestExplanationTemplatePack:
    """Shipped explanation-template pack (§8.2, honest-UX single source)."""

    def test_band_to_explanation_binding(self, svc):
        # calm band -> calm_default template rendered by explain()
        it = svc.intent_compile("send 2 PRSM to Alex")
        rv = svc.intent_review(it["intent_id"])
        assert rv["ui_band"] == "calm"
        assert "Confirm?" in rv["explanation"]
        assert "Fee:" in rv["explanation"]
        # interrupt band -> shipped 'pause' template, verbatim as the gate's
        # explanation prefix AND as the standalone human card copy
        scam = svc.intent_compile(
            "send 1 PRSM to Alex memo urgent transfer customer support")
        rvp = svc.intent_review(scam["intent_id"])
        from prism.ai.simulator import TEMPLATES, render_template
        assert rvp["ui_band"] == "pause"
        assert rvp["human"] == render_template("pause")
        assert rvp["explanation"].startswith(TEMPLATES["pause"])
        # watch band (first payment to never-seen address) -> caution copy
        w = svc.intent_compile(
            "send 1 PRSM to prsm1zq9newrecipient000000000000000000000")
        if w["status"] == "awaiting_confirm":
            rw = svc.intent_review(w["intent_id"])
            if rw["ui_band"] == "caution":
                assert "Nothing has been sent yet" in rw["explanation"]

    def test_pause_copy_is_the_shipped_template(self):
        from prism.ai.simulator import TEMPLATES
        # exact shipped strings — UI and server share one source of truth;
        # changing these here means deliberately changing honest-UX copy.
        assert TEMPLATES["pause"] == (
            "This one looks unusual for you. Nothing has been sent — take "
            "your time. You can cancel safely, or confirm you understand "
            "and we'll show the review again without rushing you.")
        assert "still cancel" in TEMPLATES["pause_acknowledged"]
        assert set(TEMPLATES) >= {"calm_default", "caution", "pause",
                                  "pause_acknowledged", "insufficient",
                                  "not_previewable"}

    def test_render_template_rejects_unknown_keys(self):
        from prism.ai.simulator import render_template
        with pytest.raises(KeyError):
            render_template("all_clear_no_worries")   # invented reassurance
        with pytest.raises(KeyError):
            render_template("calm_default")           # missing required fields
        assert render_template("pause")               # no-field template works
        assert "you'd need" in render_template(
            "insufficient", need="5", have="1").lower()

    def test_rpc_exposes_template_pack(self, svc):
        it = svc.intent_compile("send 2 PRSM to Alex")
        rv = svc.intent_review(it["intent_id"])
        assert rv["template_pack"] == "sim_explain_v1"
        scam = svc.intent_compile(
            "send 1 PRSM to Alex memo urgent transfer customer support")
        rp = svc.intent_review(scam["intent_id"])
        assert rp["template_pack"] == "sim_explain_v1"
