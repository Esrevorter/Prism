"""Tests for prism.wallet.intent — NL Intent Compiler v0 (spec §6.2, §7.3).

Covers: shard math, address-book resolution rules (exact / ambiguous /
unknown / typo-suggest), oracle quote windows + expiry gate, lifecycle
gates (draft → awaiting_confirm → confirmed; broadcast immutability),
clarification folding, advisory-only fraud overlay (§8 hard rule), and
the ≥95% correct-or-100%-safe benchmark acceptance criterion.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest  # noqa: E402

from prism.wallet import intent as W  # noqa: E402

NOW = 1_893_456_000  # arbitrary fixed wall-clock; time is always injected


def book():
    return W.AddressBook([
        W.Contact("alex", "Alex (mastering)", "prsm1alexaaaaaa",
                  ["alex", "alex@studio"], verified=True,
                  verification_method="qr_in_person"),
        W.Contact("sam", "Sam Rivera", "prsm1sammmmmmm", ["sam"],
                  verified=True, verification_method="dns_proof"),
        W.Contact("jordan", "Jordan Kay", "prsm1jjjjjjjj", ["jordan"],
                  verified=False),
    ])


def compiler(b=None):
    b = b or book()
    return W.IntentCompiler(b, W.StubOracle())


# ---------------------------------------------------------------------------
# shard math (§6.3: integers only; floats live in fiat quotes)
# ---------------------------------------------------------------------------


class TestShardMath:
    def test_exact_decimal_conversion(self):
        assert W.prsm_to_shard("12.34567901") == 1_234_567_901
        assert W.prsm_to_shard(0.5) == 50_000_000
        assert W.prsm_to_shard("100") == 10**10

    def test_truncation_not_rounding_up_drift(self):
        # sub-shard precision truncates toward zero
        assert W.prsm_to_shard("1.000000009") == 100_000_000

    def test_display_roundtrip(self):
        for s in (1, 999, 10**8, 12_345_679_01, 100_000):
            assert W.prsm_to_shard(W.shard_to_prsm(s)) == s

    def test_rejects_garbage(self):
        with pytest.raises(ValueError):
            W.prsm_to_shard("12.abc")


# ---------------------------------------------------------------------------
# address book
# ---------------------------------------------------------------------------


class TestAddressBook:
    def test_display_name_head_is_implicit_alias(self):
        hits = book().lookup("alex")
        assert [c.contact_id for c in hits] == ["alex"]

    def test_casefold_and_whitespace(self):
        assert len(book().lookup("  ALEX@STUDIO ")) == 1

    def test_ambiguous_returns_many(self):
        b = book()
        b.add(W.Contact("t1", "Taylor Swift", "prsm1t1", ["taylor"]))
        b.add(W.Contact("t2", "Taylor Jones", "prsm1t2", ["taylor"]))
        assert len(b.lookup("taylor")) == 2

    def test_typo_suggestion_edit_distance(self):
        hits = book().suggest("allex")          # distance 2 from "alex"
        assert {c.contact_id for c in hits} == {"alex"}
        assert book().suggest("zzzzzz") == []   # nothing close


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------


class TestParse:
    def test_happy_path_fields(self):
        p = W.parse_utterance(
            "Send $50 to Alex for the mastering session, keep it private")
        assert p.action == "transfer"
        assert p.fiat_amount == 50.0
        assert p.recipient_term.lower().startswith("alex")
        assert "mastering session" in (p.memo or "")
        assert not p.explicit_public

    def test_prsm_amount_captured_as_string(self):
        p = W.parse_utterance("send 12.5 PRSM to sam")
        assert p.prsm_amount == "12.5"
        assert p.fiat_amount is None

    def test_two_amounts_is_ambiguous(self):
        with pytest.raises(W.Ambiguous):
            W.parse_utterance("Send $50 plus 2 PRSM to alex")

    def test_no_action_refuses(self):
        with pytest.raises(W.Ambiguous):
            W.parse_utterance("Buy groceries")

    def test_public_phrasing_sets_flag(self):
        p = W.parse_utterance("Send 1 PRSM to sam with a public receipt")
        assert p.explicit_public


# ---------------------------------------------------------------------------
# compiler happy paths
# ---------------------------------------------------------------------------


class TestCompileHappyPath:
    def test_fiat_intent_locks_quote_window(self):
        it = compiler().compile(
            "Send $50 to Alex for the mastering session, keep it private",
            now_unix=NOW)
        assert it.status == "awaiting_confirm"
        q = it.compiled.amount["fiat_quote"]
        assert q["locked_until_unix"] == NOW + W.RATE_WINDOW_SECONDS
        assert q["max_slippage_pct"] == 2.0
        assert it.compiled.recipient_ref == "contact:alex"
        assert it.compiled.privacy_mode == "default_max"
        # preview wording mirrors §7.3 step 3
        eff = it.simulation.predicted_effects
        assert "PRSM" in eff and "Fee: $0.001" in eff

    def test_prsm_intent_exact_shards(self):
        it = compiler().compile("Pay Sam 0.25 PRSM for the mix", now_unix=NOW)
        assert it.compiled.amount == {"or_prsm": 25_000_000}
        assert it.total_cost_shard() == 25_000_000 + W.BASE_FEE_SHARD

    def test_raw_address_literal(self):
        it = compiler().compile("Send 1 PRSM to prsm1qqw3e5r7t9y", now_unix=NOW)
        assert it.compiled.recipient_ref == "addr:prsm1qqw3e5r7t9y"

    def test_stake_needs_no_recipient(self):
        it = compiler().compile("Stake 100 PRSM", now_unix=NOW)
        assert it.status == "awaiting_confirm"
        assert it.compiled.action == "stake"
        assert it.compiled.recipient_ref is None

    def test_fee_tiers_within_cap(self):
        it = compiler().compile("Send 1 PRSM to sam", now_unix=NOW)
        base = it.total_cost_shard()
        it.compiled.fee = "priority_max"
        # priority_max = 10x total including base ⇒ +9 base units
        assert it.total_cost_shard() == base + 9 * W.BASE_FEE_SHARD


# ---------------------------------------------------------------------------
# ambiguity & fallback safety (§7.3 step 2: chips, never guesses)
# ---------------------------------------------------------------------------


class TestAmbiguitySafety:
    def test_ambiguous_nickname_stays_draft_with_chip(self):
        b = book()
        b.add(W.Contact("t1", "Taylor Swift", "prsm1t1", ["taylor"]))
        b.add(W.Contact("t2", "Taylor Jones", "prsm1t2", ["taylor"]))
        it = compiler(b).compile("Send $50 to Taylor", now_unix=NOW)
        assert it.status == "draft"
        assert it.needs_clarify and "Taylor Swift" in it.needs_clarify
        assert it.compiled.recipient_ref is None

    def test_unknown_recipient_did_you_mean(self):
        it = compiler().compile("Send $10 to Allex", now_unix=NOW)
        assert it.status == "draft"
        assert "Did you mean Alex" in it.needs_clarify

    def test_missing_amount_chips(self):
        it = compiler().compile("Send to alex please", now_unix=NOW)
        assert it.status == "draft"
        assert it.needs_clarify == "How much?"
        assert it.compiled.recipient_ref == "contact:alex"

    def test_unresolved_slots_always_have_a_chip(self):
        # invariant: draft with missing slots must carry needs_clarify
        it = compiler().compile("Send money to Alex", now_unix=NOW)
        assert it._slots_missing()
        assert it.needs_clarify

    def test_draft_with_missing_slot_and_no_chip_fails_validation(self):
        it = W.Intent(intent_id="x", raw_utterance="", status="draft",
                     compiled=W.Compiled(action="transfer", amount={}))
        with pytest.raises(W.IntentError):
            it.validate()

    def test_advance_past_missing_slot_blocked(self):
        it = compiler().compile("Send to alex", now_unix=NOW)
        sim = W.Simulation(predicted_effects="x")
        with pytest.raises(W.IntentError):
            it.mark_awaiting_confirm(sim, now_unix=NOW)


# ---------------------------------------------------------------------------
# clarification folding
# ---------------------------------------------------------------------------


class TestClarification:
    def test_answer_amount_then_ready(self):
        c = compiler()
        it = c.compile("Send to alex please", now_unix=NOW)
        done = W.apply_clarification(it, "$50", c.book, c.oracle,
                                     now_unix=NOW + 10)
        assert done.status == "awaiting_confirm"
        assert "fiat_quote" in done.compiled.amount
        # window re-locked at answer time, not original time
        assert done.compiled.amount["fiat_quote"]["locked_until_unix"] \
            == NOW + 10 + W.RATE_WINDOW_SECONDS

    def test_mini_utterance_fills_both_slots(self):
        c = compiler()
        it = c.compile("Send money to Alex", now_unix=NOW)  # both slots open
        done = W.apply_clarification(it, "$50 to alex", c.book, c.oracle,
                                     now_unix=NOW + 5)
        assert done.status == "awaiting_confirm"
        assert done.compiled.recipient_ref == "contact:alex"

    def test_still_ambiguous_rechips(self):
        b = book()
        b.add(W.Contact("t1", "Taylor Swift", "prsm1t1", ["taylor"]))
        b.add(W.Contact("t2", "Taylor Jones", "prsm1t2", ["taylor"]))
        c = compiler(b)
        it = c.compile("Send $50 to Taylor", now_unix=NOW)
        again = W.apply_clarification(it, "Taylor", c.book, c.oracle,
                                      now_unix=NOW + 1)
        assert again.status == "draft"
        assert "Still unsure" in again.needs_clarify

    def test_useless_answer_raises(self):
        c = compiler()
        it = c.compile("Send to alex", now_unix=NOW)
        with pytest.raises(W.Ambiguous):
            W.apply_clarification(it, "banana", c.book, c.oracle,
                                  now_unix=NOW + 1)

    def test_original_draft_untouched_new_id(self):
        c = compiler()
        it = c.compile("Send to alex", now_unix=NOW)
        before = it.canonical_digest()
        done = W.apply_clarification(it, "2 PRSM", c.book, c.oracle,
                                     now_unix=NOW + 1)
        assert it.canonical_digest() == before       # immutable-by-convention
        assert done.intent_id != it.intent_id        # fresh audit identity


# ---------------------------------------------------------------------------
# oracle window / stale-rate gate (E5)
# ---------------------------------------------------------------------------


class TestQuoteWindow:
    def test_expired_quote_cannot_advance(self):
        it = compiler().compile("Send $50 to sam", now_unix=NOW)
        late = NOW + 2 * W.RATE_WINDOW_SECONDS
        sim = W.simulate(it, now_unix=late)
        assert any("expired" in w for w in sim.warnings)
        with pytest.raises(W.IntentError):
            it.mark_awaiting_confirm(sim, now_unix=late)

    def test_slippage_bounds_bracket_exact(self):
        q = W.StubOracle().quote("USD", 50.0, now_unix=NOW)
        lo, hi = q.slippage_bounds_shard()
        assert lo <= q.amount_shard <= hi

    def test_non_usd_currency_never_mispriced_as_usd(self):
        # € is in the money grammar but the USD-only stub oracle refuses it;
        # the compiler must degrade to a clarification chip, never silently
        # price €50 as $50 (E-safety: no wrong-currency guesses).
        c = W.IntentCompiler(book(), W.StubOracle())
        with pytest.raises(W.Unsupported):
            c.oracle.quote("EUR", 50.0, now_unix=NOW)
        it = c.compile("Send $50 to alex", now_unix=NOW)   # USD still fine
        assert it.status == "awaiting_confirm"


# ---------------------------------------------------------------------------
# lifecycle gates
# ---------------------------------------------------------------------------


class TestLifecycle:
    def test_confirm_requires_awaiting_state(self):
        it = compiler().compile("Send 1 PRSM to sam", now_unix=NOW)
        it.confirm(now_unix=NOW + 1)
        assert it.confirmation.signed_at == NOW + 1
        d = compiler().compile("Send to alex", now_unix=NOW)
        with pytest.raises(W.IntentError):
            d.confirm(now_unix=NOW)

    def test_cancel_allowed_before_broadcast(self):
        it = compiler().compile("Send 1 PRSM to sam", now_unix=NOW)
        assert it.cancel().status == "cancelled"

    def test_broadcast_irreversible(self):
        it = compiler().compile("Send 1 PRSM to sam", now_unix=NOW)
        it.status = "broadcast"
        with pytest.raises(W.IntentError):
            it.cancel()

    def test_simulator_preview_mandatory(self):
        it = compiler().compile("Send 1 PRSM to sam", now_unix=NOW)
        it.status = "draft"
        with pytest.raises(W.IntentError):
            it.mark_awaiting_confirm(W.Simulation(), now_unix=NOW)

    def test_digest_is_deterministic(self):
        c = compiler()
        a = c.compile("Send 2 PRSM to sam", now_unix=NOW)
        b = c.compile("Send 2 PRSM to sam", now_unix=NOW)
        a.intent_id = b.intent_id = "fixed"
        assert a.canonical_digest() == b.canonical_digest()


# ---------------------------------------------------------------------------
# balance & fraud overlays — ADVISORY ONLY (§8 hard rule)
# ---------------------------------------------------------------------------


class TestAdvisoryOverlays:
    def test_insufficient_balance_warns_but_compiles(self):
        it = compiler().compile("Send 999999 PRSM to sam", now_unix=NOW,
                                balance_shard=W.prsm_to_shard(1))
        assert any("insufficient funds" in w for w in it.simulation.warnings)
        assert it.status == "awaiting_confirm"   # warning ≠ veto

    def test_unseen_address_scores_watch_band(self):
        it = compiler().compile("Send 1 PRSM to prsm1neverseen", now_unix=NOW)
        assert W.RISK_WATCH <= it.simulation.risk_score < 1.0
        assert any("never-seen" in w for w in it.simulation.warnings)

    def test_verified_contact_clean(self):
        it = compiler().compile("Send 1 PRSM to alex", now_unix=NOW)
        assert it.simulation.risk_score == 0.0

    def test_unverified_contact_flags(self):
        hist = {"contact_verified": [0]}
        it = compiler().compile("Send 1 PRSM to jordan", now_unix=NOW)
        score, flags = W.fraud_flags(it, history=hist)
        assert score > 0 and any("unverified" in f for f in flags)

    def test_public_audit_adds_small_penalty(self):
        it = compiler().compile("Send 1 PRSM to alex publicly visible for audit",
                                now_unix=NOW)
        assert it.compiled.privacy_mode == "public_audit"
        assert it.simulation.risk_score >= 0.1


# ---------------------------------------------------------------------------
# benchmark acceptance (Phase-1 exit criterion)
# ---------------------------------------------------------------------------


class TestBenchmarkAcceptance:
    def test_benchmark_meets_bar(self):
        b = book()
        b.add(W.Contact("t1", "Taylor Swift", "prsm1t1", ["taylor"]))
        b.add(W.Contact("t2", "Taylor Jones", "prsm1t2", ["taylor"]))
        res = W.benchmark(b)
        assert res["n"] == len(W.BENCHMARK_CASES)
        assert res["correct_pct"] >= 95.0, res["rows"]
        assert res["safe_pct"] == 100.0, res["rows"]
        assert res["passes_acceptance"]
