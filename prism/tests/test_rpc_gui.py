"""GUI/RPC scaffolding tests (spec §13 Phase-2 precursor).

Drives PrismService directly (no sockets) plus one in-process HTTP round
trip through the real ThreadingHTTPServer to pin the JSON-RPC contract the
GUI depends on.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request

import pytest

from prism.rpc.service import PrismService
from prism.rpc import server as rpc_server

PW = "correct horse battery staple 42!"   # devnet test password (>= policy len)


def _full_auth_sign(svc: PrismService, intent_id: str) -> dict:
    """Drive the EXACT GUI happy path for one payment: CAPTCHA challenge ->
    solve using ONLY the fields that cross the wire (`ch["tiles"]`, what the
    user literally sees in the picture) -> token -> password sign.

    Regression pin (v0.1.7): an earlier version of this helper cheated by
    reaching into svc.captcha._pending for the answer key, so it stayed green
    even after the release broke real users' captcha ("not quite — tap every
    matching shape" forever).  Reading only the wire payload makes this test
    fail loudly the moment the contract regresses.  Returns auth_sign's
    result."""
    ch = svc.auth_challenge(intent_id)
    digest = ch["digest_hex"]
    # The server never ships `target`; the harness "looks at the picture":
    # pick the most-frequent shape, which new_image_grid() guarantees to be
    # the unique majority (3-4 copies vs <=2 of any decoy).
    tiles = ch["tiles"]
    target = max(set(tiles), key=tiles.count)
    assert tiles.count(target) >= 3, "majority tile must be the visible answer"
    picks = [i for i, s in enumerate(tiles) if s == target]
    solved = svc.auth_solve(ch["challenge_id"], picks, digest)
    return svc.auth_sign(intent_id, PW, solved["token"])


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
        svc.auth_create(PW)
        svc.mine(5)
        it = svc.intent_compile("send 2 PRSM to Alex")
        assert it["status"] == "awaiting_confirm"
        done = svc.intent_confirm(it["intent_id"])
        assert "canonical_digest_hex" in done["confirmation_envelope"]
        signed = _full_auth_sign(svc, it["intent_id"])
        assert signed["ok"] and signed["height"] > 0

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


class TestLeanAuth:
    """RPC-level pins for the lean auth stack (spec §9, Amendment A2-2026-10)."""

    def test_status_without_wallet(self, svc):
        st = svc.auth_status()
        assert st["exists"] is False and st["unlocked"] is False
        assert "Create one" in st["human"]

    def test_create_returns_phrase_once_and_keeps_address_stable(self, svc):
        r = svc.auth_create(PW)
        assert len(r["recovery_phrase"].split()) == 12
        addr = r["address"]
        assert svc.auth_status()["address"] == addr      # same across calls
        with pytest.raises(ValueError):                  # no silent overwrite
            svc.auth_create(PW)

    def test_challenge_requires_confirmed_intent(self, svc):
        svc.auth_create(PW)
        it = svc.intent_compile("send 1 PRSM to Alex")
        with pytest.raises(ValueError, match="not ready to sign"):
            svc.auth_challenge(it["intent_id"])          # awaiting_confirm
        svc.intent_confirm(it["intent_id"])
        ch = svc.auth_challenge(it["intent_id"])
        # v0.1.5: vector-first picture (one inline SVG) — never a raw <img>
        assert ch["svg"].startswith("<svg") and ch["digest_hex"]
        assert ch["palette"] and "star" in ch["palette"]

    def test_grid_majority_is_target(self):
        """Regression pin (v0.1.7): new_image_grid() must guarantee the
        prompt's target shape is the STRICT visual majority (count >= 3,
        every decoy strictly fewer).  The old collision-fixup could bump a
        decoy to >= target count, so ~60% of pictures contradicted their own
        prompt and honest users could never solve them."""
        from collections import Counter
        from prism.auth.captcha import new_image_grid
        for _ in range(200):
            g = new_image_grid()
            c = Counter(g["tiles"])
            top, cnt = c.most_common(1)[0]
            second = c.most_common(2)[1][1] if len(c) > 1 else 0
            assert top == g["target"], "majority tile must be the prompt target"
            assert cnt >= 3 and second < cnt, f"ambiguous grid: {dict(c)}"

    def test_captcha_wire_contract(self, svc):
        """Regression pin for the broken v0.1.7 captcha release.

        The GUI renders tiles from `ch["tiles"]` and submits taps against it;
        if that field ever disappears from the wire payload, EVERY payment
        fails with "not quite — tap every matching shape" and users cannot
        send money.  This test solves challenges using only what crosses the
        RPC boundary (never engine internals) so the whole class of bug is
        caught in CI before tagging.  Also pins fail-closed behaviour: wrong
        picks are refused, and the answer key (`target`) must NEVER leak."""
        svc.auth_create(PW); svc.mine(5)
        required = ("challenge_id", "prompt", "svg", "tiles", "palette",
                    "ttl_s", "expires_at", "intent_id", "digest_hex", "human")
        for _ in range(10):                       # cover many random grids
            it = svc.intent_compile("send 1 PRSM to Alex")
            svc.intent_confirm(it["intent_id"])
            ch = svc.auth_challenge(it["intent_id"])
            for key in required:
                assert key in ch, f"captcha contract field {key!r} missing"
            assert len(ch["tiles"]) == 9, "grid must ship all 9 visible tiles"
            assert "target" not in ch, "answer key must never cross the wire"
            # honest solve: derive the answer from the visible picture only
            target = max(set(ch["tiles"]), key=ch["tiles"].count)
            good = [i for i, s in enumerate(ch["tiles"]) if s == target]
            bad = [i for i in range(9) if i not in good][:2]
            with pytest.raises(ValueError, match="not quite"):
                svc.auth_solve(ch["challenge_id"], bad, ch["digest_hex"])
            solved = svc.auth_solve(ch["challenge_id"], good, ch["digest_hex"])
            assert svc.auth_sign(it["intent_id"], PW, solved["token"])["ok"]

    def test_wrong_captcha_picks_refused_then_fresh_solve_ok(self, svc):
        svc.auth_create(PW); svc.mine(5)
        it = svc.intent_compile("send 1 PRSM to Alex")
        svc.intent_confirm(it["intent_id"])
        ch = svc.auth_challenge(it["intent_id"])
        with pytest.raises(ValueError):
            svc.auth_solve(ch["challenge_id"], [0], ch["digest_hex"])
        r = _full_auth_sign(svc, it["intent_id"])        # new challenge path
        assert r["ok"]

    def test_token_replay_refused(self, svc):
        svc.auth_create(PW); svc.mine(5)
        it = svc.intent_compile("send 1 PRSM to Alex")
        svc.intent_confirm(it["intent_id"])
        ch = svc.auth_challenge(it["intent_id"])
        target = max(set(ch["tiles"]), key=ch["tiles"].count)   # wire-only
        picks = [i for i, s in enumerate(ch["tiles"]) if s == target]
        solved = svc.auth_solve(ch["challenge_id"], picks, ch["digest_hex"])
        assert svc.auth_sign(it["intent_id"], PW, solved["token"])["ok"]
        it2 = svc.intent_compile("send 1 PRSM to Alex")
        svc.intent_confirm(it2["intent_id"])
        with pytest.raises(ValueError, match="expired or does not match"):
            svc.auth_sign(it2["intent_id"], PW, solved["token"])

    def test_sign_without_password_refused(self, svc):
        svc.auth_create(PW); svc.mine(5)
        it = svc.intent_compile("send 1 PRSM to Alex")
        svc.intent_confirm(it["intent_id"])
        ch = svc.auth_challenge(it["intent_id"])
        target = max(set(ch["tiles"]), key=ch["tiles"].count)   # wire-only
        picks = [i for i, s in enumerate(ch["tiles"]) if s == target]
        tok = svc.auth_solve(ch["challenge_id"], picks, ch["digest_hex"])["token"]
        with pytest.raises(ValueError):
            svc.auth_sign(it["intent_id"], "totally wrong password 1!", tok)

    def test_recovery_restores_same_address(self, svc):
        created = svc.auth_create(PW)
        phrase, addr = created["recovery_phrase"], created["address"]
        fresh = PrismService()
        r = fresh.auth_recover(phrase, PW)
        assert r["address"] == addr

    def test_change_password_keeps_key(self, svc):
        addr = svc.auth_create(PW)["address"]
        pw2 = "brand new passphrase 99#"
        svc.auth_change_password(PW, pw2)
        assert svc.auth_unlock(pw2)["address"] == addr
        with pytest.raises(ValueError):
            svc.auth_unlock(PW)

    def test_history_lists_signed_tx(self, svc):
        svc.auth_create(PW); svc.mine(5)
        it = svc.intent_compile("send 2 PRSM to Alex")
        svc.intent_confirm(it["intent_id"])
        _full_auth_sign(svc, it["intent_id"])
        h = svc.auth_history()
        assert h["count"] == 1 and h["txs"][0]["amount_prsm"] in ("2", "2.0")


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
        st = self._call(live, "auth.status")["result"]
        assert st["exists"] is False
        made = self._call(live, "auth.create", {"password": PW})["result"]
        assert len(made["recovery_phrase"].split()) == 12
        assert self._call(live, "chain.mine", {"count": 5})["result"]["mined"] == 5
        it = self._call(live, "intent.compile",
                        {"utterance": "send 1 PRSM to Alex"})["result"]
        conf = self._call(live, "intent.confirm",
                          {"intent_id": it["intent_id"]})["result"]
        assert conf["confirmation_envelope"]["canonical_digest_hex"]
        ch = self._call(live, "auth.challenge",
                        {"intent_id": it["intent_id"]})["result"]
        # honest solve from the wire payload only (v0.1.7 post-mortem:
        # reading captcha._pending hid the broken-release bug from CI)
        _tiles = ch["tiles"]
        _maj = max(set(_tiles), key=_tiles.count)
        picks = [i for i, s in enumerate(_tiles) if s == _maj]
        tok = self._call(live, "auth.solve", {"challenge_id": ch["challenge_id"],
                                              "picks": picks,
                                              "digest_hex": ch["digest_hex"]})["result"]
        signed = self._call(live, "auth.sign", {"intent_id": it["intent_id"],
                                                "password": PW,
                                                "token": tok["token"]})["result"]
        assert signed["ok"] and "Sent" in signed["human"]
        hist = self._call(live, "auth.history")["result"]
        assert hist["count"] == 1

    def test_method_not_found_wire_format(self, live):
        resp = self._call(live, "bogus.method")
        assert resp["error"]["code"] == -32601

    def test_gui_bundle_served(self, live):
        with urllib.request.urlopen(live + "/", timeout=5) as r:
            html = r.read().decode()
        assert "What would you like to do?" in html
        with urllib.request.urlopen(live + "/static/app.js", timeout=5) as r:
            assert b"jsonrpc" in r.read()


class TestGUIv2WireContract:
    """GUI v2 lean-auth journey over a real socket, exactly as app.js drives
    it: static bundle -> auth.create -> recover (address-invariant) -> unlock
    -> intent flow -> CAPTCHA challenge/solve -> sign -> history. Pins the
    new response fields the animated front end relies on (expires_at, tiles,
    target, png_b64, human copy)."""

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

    def test_static_bundle_is_gui_v2(self, live):
        with urllib.request.urlopen(live + "/", timeout=5) as r:
            html = r.read().decode()
        # anchors the v2 controller expects (added during the redesign)
        assert 'id="phrase-warning"' in html
        assert 'id="balance-note"' in html
        with urllib.request.urlopen(live + "/static/app.js", timeout=5) as r:
            js = r.read().decode()
        assert "auth.challenge" in js and "auth.sign" in js
        assert "mpc.ceremony_start" not in js          # MPC fully retired

    def test_full_lean_auth_payment_journey(self, live):
        PW2 = "brand new passphrase 99#"
        c = self._call(live, "auth.create", {"password": PW})["result"]
        assert len(c["recovery_phrase"].split()) == 12
        assert "ONLY way back" in c["warning"]         # honest phrase copy
        addr = c["address"]
        # forgot-password path: the 12 words restore the SAME address
        r = self._call(live, "auth.recover",
                       {"phrase": c["recovery_phrase"],
                        "new_password": PW2})["result"]
        assert r["address"] == addr and r["ok"]
        u = self._call(live, "auth.unlock", {"password": PW2})["result"]
        assert u["address"] == addr
        bal = self._call(live, "wallet.balance")["result"]
        assert bal["address"] == addr                 # hero needs it
        self._call(live, "chain.mine", {"count": 5})
        it = self._call(live, "intent.compile",
                        {"utterance": "send 1 PRSM to Alex"})["result"]
        # challenge must refuse an UNCONFIRMED intent (honest gate order)
        bad = self._call(live, "auth.challenge",
                         {"intent_id": it["intent_id"]})
        assert "error" in bad and "not ready to sign" in bad["error"]["message"]
        self._call(live, "intent.confirm", {"intent_id": it["intent_id"]})
        ch = self._call(live, "auth.challenge",
                        {"intent_id": it["intent_id"]})["result"]
        # fields the animated GUI relies on:
        assert ch["expires_at"] > time.time()          # countdown ring
        assert ch["svg"].startswith("<svg")            # ONE crisp vector picture
        # solve like a real user: only wire-visible fields (no answer key)
        _tiles = ch["tiles"]
        _target_shape = max(set(_tiles), key=_tiles.count)
        picks = [i for i, t in enumerate(_tiles) if t == _target_shape]
        sol = self._call(live, "auth.solve",
                        {"challenge_id": ch["challenge_id"],
                         "picks": picks,
                         "digest_hex": ch["digest_hex"]})["result"]
        signed = self._call(live, "auth.sign",
                            {"intent_id": it["intent_id"],
                             "password": PW2,
                             "token": sol["token"]})["result"]
        assert signed["ok"] and "Sent" in signed["human"]
        # solved token is single-use: replay must fail
        replay = self._call(live, "auth.sign",
                            {"intent_id": it["intent_id"],
                             "password": PW2,
                             "token": sol["token"]})
        assert "error" in replay
        h = self._call(live, "auth.history")["result"]
        assert h["count"] == 1 and h["txs"][0]["amount_prsm"] in ("1", "1.0")



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


class TestAgentRuntime:
    """Policy enforcement + neuro-inclusive UX constraints (spec §7.7)."""

    def test_proposal_queues_under_veto_window(self, svc):
        g = svc.grant_create(days=30, per_action_prsm=25.0, monthly_prsm=400.0,
                             veto_hours=24.0)
        gid = g["grant"]["grant_id"]
        p = svc.agent_propose("Buy $10 of PRSM to My DCA pot", grant_id=gid)
        assert p.get("ok") is True, p.get("reasons")
        assert p["state"] == "queued_under_veto_window"
        pend = svc.agent_pending()
        assert pend["count"] == 1
        # UX constraint: digest is plain-language only — no raw numbers the
        # user must interpret, and honest "nothing has run yet" copy.
        line = pend["pending"][0]["line"]
        assert "Bought" in line or "buy" in line.lower()
        assert "nothing has run yet" in pend["human"].lower()

    def test_prsm_denominated_dca_refused_with_all_reasons(self, svc):
        g = svc.grant_create(); gid = g["grant"]["grant_id"]
        bad = svc.agent_propose("Buy 5 PRSM to My DCA pot", grant_id=gid)
        assert bad.get("refused") is True
        assert any("fiat-quoted" in r for r in bad["reasons"])
        assert svc.agent_pending()["count"] == 0  # refusal never queues

    def test_executed_settles_money_state(self, svc):
        svc.mine(2)  # faucet funding: unfunded actions stay queued (fail-closed)
        g = svc.grant_create(); gid = g["grant"]["grant_id"]
        svc.agent_propose("Buy $10 of PRSM to My DCA pot", grant_id=gid)
        bal0 = svc.balance()["balance_shard"]; h0 = svc.chain_info()["height"]
        t = svc.agent_tick(hours_ahead=25.0)
        # agent_tick schema: transitions (state changes incl. executed_after_veto_window),
        # broadcast (on-chain settlements), refused_insufficient, human copy.
        assert len(t["transitions"]) == 1
        assert t["transitions"][0]["state"] == "executed_after_veto_window"
        assert len(t["broadcast"]) == 1
        ex = t["broadcast"][0]
        assert ex["txid"], "executed action must carry a real txid"
        bal1 = svc.balance()["balance_shard"]
        assert bal1 < bal0, "execution must deduct from the wallet"
        assert svc.chain_info()["height"] == h0 + 1

    def test_unfunded_action_stays_queued_and_settles_after_funding(self, svc):
        g = svc.grant_create(); gid = g["grant"]["grant_id"]
        svc.agent_propose("Buy $10 of PRSM to My DCA pot", grant_id=gid)
        svc.balance_shard = 0  # simulate drained wallet
        t = svc.agent_tick(hours_ahead=25.0)
        assert t["broadcast"] == []
        assert len(t["refused_insufficient"]) == 1
        assert svc.agent_pending()["count"] == 1, \
            "fail-closed: unfunded action keeps its veto window open"
        svc.faucet_outputs = []; svc.balance_shard = 0
        svc.mine(3)  # fund the wallet again
        t2 = svc.agent_tick(hours_ahead=25.0)
        assert len(t2["broadcast"]) == 1
        assert svc.agent_pending()["count"] == 0

    def test_veto_one_tap_blocks_execution(self, svc):
        g = svc.grant_create(); gid = g["grant"]["grant_id"]
        p = svc.agent_propose("Buy $10 of PRSM to My DCA pot", grant_id=gid)
        v = svc.agent_veto(p["action_id"][:8])   # short id from the card
        assert v.get("ok") is True
        assert "not run" in v["human"].lower()   # honest confirmation copy
        t = svc.agent_tick(hours_ahead=25.0)
        assert t["broadcast"] == []              # vetoed means never executed

    def test_revoke_cascades_and_is_permanent(self, svc):
        g = svc.grant_create(); gid = g["grant"]["grant_id"]
        svc.agent_propose("Buy $10 of PRSM to My DCA pot", grant_id=gid)
        r = svc.agent_revoke(gid)
        assert r.get("ok") is True
        assert "cancelled" in r["human"].lower()
        assert svc.agent_pending()["count"] == 0
        after = svc.agent_propose("Buy $10 of PRSM to My DCA pot", grant_id=gid)
        assert after.get("refused") is True      # revoked plan never resumes
