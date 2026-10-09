"""Tests for prism.ai.simulator — tx simulator + signing-gate wiring
(spec §8.2, §7.3 steps 3–5).

Covers: deterministic execution vs local snapshot (coin selection, change,
fees), insufficient-funds path, stale-quote re-lock (E5), template preview
wording, sim_digest binding, and the SigningGate composition rules:
simulation failure ⇒ no proceed; risk 'interrupt' ⇒ explicit acknowledgement
required before the gate opens (§8 hard rule in both directions).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest  # noqa: E402

from prism.ai import fraud as FD          # noqa: E402
from prism.ai import simulator as SIM     # noqa: E402
from prism.wallet import intent as W      # noqa: E402

NOW = 1_893_456_000


def book():
    return W.AddressBook([
        W.Contact("alex", "Alex (mastering)", "prsm1alexaaaaaa", ["alex"],
                  verified=True, verification_method="qr_in_person"),
    ])


def compiler():
    return W.IntentCompiler(book(), W.StubOracle())


def snap(*amounts):
    return SIM.ChainSnapshot([SIM.SnapshotOutput(f"tx{i}", i, a * 10**8)
                              for i, a in enumerate(amounts)])


# ---------------------------------------------------------------------------
# Deterministic execution
# ---------------------------------------------------------------------------


class TestSimulate:
    def test_exact_prsm_send(self):
        it = compiler().compile("Send 2 PRSM to alex", now_unix=NOW)
        s = SIM.simulate_intent(it, snap(5), now_unix=NOW, recipient_name="Alex")
        assert s.ok
        assert s.amount_shard == 2 * 10**8
        assert s.fee_shard == W.BASE_FEE_SHARD            # base_only
        assert s.change_shard == 3 * 10**8 - W.BASE_FEE_SHARD
        assert s.balance_after_shard == s.balance_before_shard - s.total_out_shard

    def test_insufficient_funds_plain_language(self):
        it = compiler().compile("Send 9 PRSM to alex", now_unix=NOW)
        s = SIM.simulate_intent(it, snap(5), now_unix=NOW)
        assert not s.ok
        assert "insufficient funds" in s.errors[0]
        assert "enough" in s.plain_english               # calm wording, no codes

    def test_priority_fee_capped(self):
        it = compiler().compile("Send 1 PRSM to alex", now_unix=NOW)
        it.compiled.fee = "priority_max"
        s = SIM.simulate_intent(it, snap(10), now_unix=NOW)
        assert s.fee_shard == W.MAX_PRIORITY_MULTIPLIER * W.BASE_FEE_SHARD

    def test_stale_quote_relocks_and_flags(self):
        it = compiler().compile("Send $50 to alex", now_unix=NOW)
        old = NOW + 10                                  # still inside window
        late = NOW + 3600                               # window long gone
        s1 = SIM.simulate_intent(it, snap(50), now_unix=old)
        assert not s1.quote_refreshed
        s2 = SIM.simulate_intent(it, snap(50), now_unix=late,
                                 oracle=W.StubOracle())
        assert s2.quote_refreshed
        assert "re-locked" in s2.plain_english
        assert s2.ok

    def test_stale_quote_without_oracle_errors_cleanly(self):
        it = compiler().compile("Send $50 to alex", now_unix=NOW)
        # strip the quote window so the executor cannot self-heal via re-quote
        it.compiled.amount["fiat_quote"]["locked_until_unix"] = NOW - 1
        s = SIM.simulate_intent(it, snap(50), now_unix=NOW + 3600, oracle=None)
        assert not s.ok and "re-quote" in s.errors[0]

    def test_draft_intent_not_previewable(self):
        it = compiler().compile("Send $50 to nobodyhere", now_unix=NOW)
        assert it.status == "draft"
        # harden the draft: recipient chip AND an already-expired quote that
        # no re-lock can rescue — the preview must refuse, not repair.
        it.compiled.amount["fiat_quote"]["locked_until_unix"] = NOW - 1
        s = SIM.simulate_intent(it, snap(50), now_unix=NOW)
        assert not s.ok
        assert "can't preview" in s.plain_english

    def test_coin_select_deterministic_largest_first(self):
        sn = SIM.ChainSnapshot([SIM.SnapshotOutput("b", 0, 10**8),
                                SIM.SnapshotOutput("a", 0, 40 * 10**8),
                                SIM.SnapshotOutput("c", 0, 20 * 10**8)])
        picked = sn.coin_select(45 * 10**8)
        assert [p.txid for p in picked] == ["a", "c"]

    def test_locked_outputs_excluded(self):
        sn = SIM.ChainSnapshot([SIM.SnapshotOutput("x", 0, 100 * 10**8,
                                                   maturity="locked")])
        assert sn.balance_shard == 0
        assert sn.coin_select(1) == []

    def test_sim_digest_binds_preview(self):
        it = compiler().compile("Send 2 PRSM to alex", now_unix=NOW)
        s1 = SIM.simulate_intent(it, snap(5), now_unix=NOW)
        s2 = SIM.simulate_intent(it, snap(5), now_unix=NOW + 1)
        assert s1.sim_digest != s2.sim_digest           # freshness-bound

    def test_privacy_mode_wording(self):
        it = compiler().compile("Send 1 PRSM to alex with a public receipt",
                                now_unix=NOW)
        assert it.compiled.privacy_mode == "public_audit"
        s = SIM.simulate_intent(it, snap(5), now_unix=NOW, recipient_name="Alex")
        assert "WARNING" in s.plain_english


# ---------------------------------------------------------------------------
# Signing gate wiring (§7.3 steps 3–5)
# ---------------------------------------------------------------------------


class TestSigningGate:
    def setup_method(self):
        self.gate = SIM.SigningGate()

    def test_happy_path_proceeds_with_preview(self):
        it = compiler().compile("Send $50 to Alex for the mastering session",
                                now_unix=NOW)
        rev = self.gate.review(it, snap(50), now_unix=NOW,
                               recipient_name="Alex", contact_verified=True)
        assert rev.sim.ok and rev.proceed
        assert "≈12.34567901 PRSM" in rev.sim.plain_english
        # 0.0001 PRSM base fee priced at the stub oracle ($4.05/PRSM)
        assert "$0.0004" in rev.sim.plain_english       # fee from executor

    def test_failed_simulation_never_proceeds(self):
        it = compiler().compile("Send 999 PRSM to alex", now_unix=NOW)
        rev = self.gate.review(it, snap(5), now_unix=NOW)
        assert not rev.sim.ok
        assert not rev.proceed                          # gate closed

    def test_interrupt_requires_acknowledgement(self):
        it = compiler().compile(
            "Send 4 PRSM to prsm1qqw3e5r7t9y act now airdrop claim",
            now_unix=NOW)
        rev = self.gate.review(it, snap(5), now_unix=NOW)
        assert rev.interruption
        assert not rev.proceed                          # model advises...
        rev2 = self.gate.review(it, snap(5), now_unix=NOW,
                                acknowledged_risk=True)
        assert rev2.proceed                             # ...human decides

    def test_gate_emits_no_signing_api(self):
        forbidden = {"sign", "broadcast", "private_key", "share"}
        public = {n.lower() for n in dir(SIM.SigningGate)
                  if not n.startswith("_")}
        assert not any(any(f in p for f in forbidden) for p in public)

    def test_review_reasons_are_human_readable(self):
        it = compiler().compile("Send 4.99 PRSM to prsm1zzz unknown migrate wallet",
                                now_unix=NOW)
        rev = self.gate.review(it, snap(5), now_unix=NOW)
        assert all(isinstance(r, str) for r in rev.reasons)
        assert any("scam pattern" in r for r in rev.reasons)
