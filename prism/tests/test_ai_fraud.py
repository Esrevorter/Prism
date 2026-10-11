"""Tests for prism.ai.fraud — on-device fraud detector v1 (spec §8.2, §7.3 step 4).

Covers: feature-vector hygiene, rule-pack overlay (scam phrases, lookalike
swap detection, drainer rule), quantized bundle round-trip, band assignment,
the advisory-only hard rule (§8: model output can never veto or sign), and
the Phase-2 benchmark criterion FPR < 0.5% on a benign corpus with full
recall on the curated phishing-pattern set.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest  # noqa: E402

from prism.ai import fraud as FD  # noqa: E402

NOW = 1_893_456_000


def feats(**kw):
    base = dict(destination="addr:prsm1qqw3e5r7t9y", amount_shard=25_000_000,
                balance_shard=1_000_000_000, now_unix=NOW)
    base.update(kw)
    return FD.TxFeatures(**base)


# ---------------------------------------------------------------------------
# Feature vector
# ---------------------------------------------------------------------------


class TestFeatures:
    def test_vector_length_matches_names(self):
        ov = FD.RuleOverlay()
        v = feats().vector(ov)
        assert len(v) == len(FD.FEATURE_NAMES) == 8

    def test_drain_share_bounded(self):
        ov = FD.RuleOverlay()
        v = feats(amount_shard=2_000_000_000, balance_shard=1_000_000_000
                  ).vector(ov)
        assert v[FD.FEATURE_NAMES.index("drain_share")] == 1.0

    def test_first_seen_flag(self):
        ov = FD.RuleOverlay()
        assert feats(seen_before=True).vector(ov)[0] == 0.0
        assert feats(seen_before=False).vector(ov)[0] == 1.0


# ---------------------------------------------------------------------------
# Rule overlay (§8.2 weekly pattern packs)
# ---------------------------------------------------------------------------


class TestRuleOverlay:
    def setup_method(self):
        self.ov = FD.RuleOverlay()

    def test_scam_phrases_hit(self):
        assert self.ov.matches_phrase("act now free gift nft")
        assert self.ov.matches_phrase("please migrate your wallet today")
        assert self.ov.matches_phrase("urgent: transfer to customer support")

    def test_benign_memo_misses(self):
        assert not self.ov.matches_phrase("design session")
        assert not self.ov.matches_phrase("rent share for October")

    def test_lookalike_detection(self):
        pack = FD.RulePack(version="t", trusted_aliases=("prsm1alexaaaaaa",))
        ov = FD.RuleOverlay(pack)
        assert ov.lookalike("prsm1a1exaaaaaa")          # 1-char swap
        assert not ov.lookalike("prsm1zzzzzzzzzzz")     # unrelated
        assert not ov.lookalike("prsm1alexaaaaaa")      # exact match is fine

    def test_pack_digest_changes_with_version(self):
        a = FD.RulePack(version="v1").digest
        b = FD.RulePack(version="v2").digest
        assert a != b

    def test_evaluate_names_reasons(self):
        hits = self.ov.evaluate(feats(memo="act now!", seen_before=False,
                                      balance_shard=30_000_000,
                                      amount_shard=29_500_000))
        assert any("never-seen" in h for h in hits)
        assert any("scam pattern" in h for h in hits)
        assert any("drains" in h for h in hits)


# ---------------------------------------------------------------------------
# Quantized bundle + scorer
# ---------------------------------------------------------------------------


class TestBundle:
    def test_quantize_dequantize_roundtrip(self):
        w = [0.5, -1.25, 2.0, 0.0, 3.99, -0.01, 1.1, 0.2]
        b = FD.ModelBundle.quantize(w, bias=-1.75)
        assert all(-127 <= q <= 127 for q in b.weights_q)   # int8 range
        wd, bd = b.dequantize()
        for a, c in zip(w, wd):
            assert abs(a - c) < 0.04                        # quantization error
        assert abs(bd - (-1.75)) < 0.04

    def test_stale_bundle_refused(self):
        short = FD.ModelBundle(weights_q=(1, 2, 3), bias_q=0,
                               feature_names=FD.FEATURE_NAMES[:3])
        with pytest.raises(ValueError):
            FD.GBDTLite(short)


# ---------------------------------------------------------------------------
# Detector bands + §8 hard rule
# ---------------------------------------------------------------------------


class TestDetector:
    def setup_method(self):
        self.d = FD.FraudDetector()

    def test_benign_repeat_payment_clean(self):
        r = self.d.score(feats(seen_before=True, contact_verified=True,
                               memo="monthly retainer"))
        assert r.band == "clean"
        assert r.score < FD.RISK_WATCH

    def test_scam_phrase_interrupts(self):
        r = self.d.score(feats(memo="act now claim airdrop", seen_before=False))
        assert r.band == "interrupt"
        assert r.sleep_on_it_seconds == 10

    def test_rule_floor_beats_ml(self):
        # even with an optimistic model, a scam-phrase hit floors at interrupt
        weak = FD.GBDTLite(FD.ModelBundle.quantize([0.0] * 8, bias=-5.0))
        d = FD.FraudDetector(model=weak)
        r = d.score(feats(memo="migrate wallet act now"))
        assert r.band == "interrupt"

    def test_band_boundaries(self):
        assert FD.band_for(0.29) == "clean"
        assert FD.band_for(0.30) == "watch"
        assert FD.band_for(0.69) == "watch"
        assert FD.band_for(0.70) == "interrupt"

    def test_advisory_only_surface(self):
        # The module exposes no signing/broadcast/veto API whatsoever (§8).
        forbidden = {"sign", "broadcast", "veto", "block", "approve"}
        public = {n for n in dir(FD) if not n.startswith("_")}
        assert not (forbidden & public)
        report = self.d.score(feats())
        assert isinstance(report, FD.RiskReport)   # pure data, no side effects

    def test_sequence_model_bonus_bounded(self):
        seq = FD.SequenceModel()
        for _ in range(10):
            seq.observe("flagged", "flagged")
        d = FD.FraudDetector(sequence=seq)
        r = d.score(feats(seen_before=True, contact_verified=True))
        assert r.score <= 1.0
        assert seq.flag_probability("flagged") == 1.0


# ---------------------------------------------------------------------------
# Phase-2 benchmark: FPR < 0.5% benign; recall on phishing corpus
# ---------------------------------------------------------------------------


def benign_corpus(n=400):
    """Regular-user traffic: repeat merchants, verified friends, small
    amounts, plain memos."""
    rows = []
    for i in range(n):
        rows.append(feats(
            destination=f"contact:c{i % 25}",
            amount_shard=(i % 90 + 5) * 1_000_000,
            balance_shard=5_000_000_000,
            memo=["design fee", "session", "invoice #204", "lunch"][i % 4],
            contact_verified=True,
            seen_before=i % 7 != 0,           # occasional new-but-normal payee
        ))
    return rows


def phishing_corpus():
    return [
        feats(memo="act now doubling event send to verify"),
        feats(memo="claim airdrop free nft"),
        feats(memo="migrate your wallet gas fee up front"),
        feats(memo="urgent transfer customer support"),
        feats(memo="send 5 to verify seed phrase reset"),
        feats(destination="prsm1a1exaaaaaa",
              balance_shard=None, seen_before=False),
    ]


class TestBenchmark:
    def test_fpr_below_half_percent(self):
        d = FD.FraudDetector()
        false_pos = sum(1 for f in benign_corpus()
                        if d.score(f).band == "interrupt")
        fpr = false_pos / 400
        assert fpr < 0.005, f"FPR={fpr}"

    def test_full_recall_on_curated_phishing_set(self):
        d = FD.FraudDetector(overlay=FD.RuleOverlay(FD.RulePack(
            version="bench", trusted_aliases=("prsm1alexaaaaaa",))))
        for f in phishing_corpus():
            r = d.score(f)
            assert r.band == "interrupt", (f.memo, r.to_json())

    def test_report_is_serializable(self):
        import json
        r = FD.FraudDetector().score(feats(memo="act now"))
        json.dumps(r.to_json())
