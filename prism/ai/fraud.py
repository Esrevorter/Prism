"""On-device fraud detector v1 — ai/ deliverable (spec §8.2, §7.3 step 4).

Architecture per spec: gradient-boosted trees + small sequence model over
address-behavior features + a rule overlay for known scam patterns. This
prototype keeps that shape with stdlib only:

  * ``RuleOverlay``   — deterministic pattern pack (scam phrases, never-seen
                        addresses, unverified contacts, drainer-style "all
                        funds" sends...). Ships as a versioned, hot-swappable
                        pack ("weekly feature/rule packs" update channel).
  * ``GBDTLite``      — logistic scoring over the same feature vector a GBDT
                        would consume; trained offline via FedAvg (§8.3),
                        shipped as an int8-quantized bundle (≤5 MB budget is
                        trivially met by this reference size).
  * ``SequenceModel`` — tiny Markov chain over address-label transitions,
                        standing in for the "small sequence model over
                        address-behavior features".

Hard rules enforced by design (§8 safety invariant):
  * ``score()`` returns an ADVISORY RiskReport only. There is no code path
    from this module to any signing primitive; the wallet UI consumes the
    watch/interrupt bands (§7.3 step 4) and owns every decision.
  * No transaction content leaves the device: features are computed from
    locally-held history (§6.2 Owned Output risk_label) passed in by caller.

Benchmarks (Phase 2 acceptance): FPR < 0.5% on benign corpus while keeping
recall on the curated phishing-pattern set — see prism/tests/test_ai_fraud.py.
"""
from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

# ---------------------------------------------------------------------------
# Feature vector (§8.2: address-behavior features)
# ---------------------------------------------------------------------------

FEATURE_NAMES = (
    "first_seen",          # destination never seen in local history
    "unverified_contact",  # resolved contact lacks verification (§6.2 Contact)
    "phrase_scam",         # memo/utterance matches scam-pattern lexicon
    "lookalike_addr",      # vanity homoglyph of a known-good alias
    "drain_share",         # fraction of balance consumed by this send
    "recency_burst",       # many distinct destinations in a short window
    "new_address_age",     # destination first observed very recently
    "public_audit",        # user opted into audit-visible mode (mild flag)
)


def _sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


@dataclass
class TxFeatures:
    """Everything the model looks at. Built by the wallet from local data."""

    destination: str                       # addr:prsm1... | contact:<id>
    amount_shard: int
    balance_shard: Optional[int] = None    # None = unknown → conservative
    memo: str = ""
    utterance: str = ""
    contact_verified: Optional[bool] = None
    privacy_mode: str = "default_max"
    seen_before: bool = False              # destination in local history
    dest_first_seen_unix: Optional[int] = None
    now_unix: int = 0
    recent_destinations: Sequence[str] = ()  # last-N distinct addrs (§sequence)

    def vector(self, overlay: "RuleOverlay") -> list[float]:
        drain = 0.0
        if self.balance_shard and self.balance_shard > 0:
            drain = min(1.0, self.amount_shard / self.balance_shard)
        burst = 0.0
        if self.recent_destinations:
            new_distinct = sum(1 for d in self.recent_destinations if d == self.destination)
            burst = min(1.0, len(set(self.recent_destinations)) / 8.0)
            if new_distinct:
                burst = max(burst, 0.9)
        age_new = 0.0
        if self.dest_first_seen_unix is not None and self.now_unix:
            days = max(0.0, (self.now_unix - self.dest_first_seen_unix) / 86400.0)
            age_new = max(0.0, 1.0 - days / 7.0)
        text = f"{self.memo} {self.utterance}"
        return [
            0.0 if self.seen_before else 1.0,
            1.0 if self.contact_verified is False else 0.0,
            1.0 if overlay.matches_phrase(text) else 0.0,
            1.0 if overlay.lookalike(self.destination) else 0.0,
            drain,
            burst,
            age_new,
            1.0 if self.privacy_mode == "public_audit" else 0.0,
        ]


# ---------------------------------------------------------------------------
# Rule overlay — versioned pattern pack (§8.2 update channel: weekly)
# ---------------------------------------------------------------------------

_SCAM_PHRASES = (
    r"\bact now\b", r"\bdoubling? (event|promo)\b", r"\bsend .{0,12}(to|for) (a )?verify\b",
    r"\bgas fee.{0,20}(advance|up front)\b", r"\bmigrate .{0,12}wallet\b",
    r"\bairdrop.{0,20}claim\b", r"\bcustomer ?support\b", r"\bseed phrase\b",
    r"\b(free|gift) ?nft\b", r"\burgent\b.{0,30}\btransfer\b",
)

_LOOKALIKE_EDITS = 2  # edit distance ≤2 against a trusted alias = suspicious


@dataclass
class RulePack:
    version: str
    scam_patterns: tuple[str, ...] = _SCAM_PHRASES
    trusted_aliases: tuple[str, ...] = ()   # prsm1... strings the user trusts

    @property
    def digest(self) -> str:
        blob = repr((self.version, self.scam_patterns, self.trusted_aliases))
        return hashlib.sha256(blob.encode()).hexdigest()[:16]


class RuleOverlay:
    def __init__(self, pack: Optional[RulePack] = None):
        self.pack = pack or RulePack(version="rules-2026w41-v1")
        self._compiled = [re.compile(p, re.IGNORECASE) for p in self.pack.scam_patterns]

    def matches_phrase(self, text: str) -> bool:
        return any(rx.search(text or "") for rx in self._compiled)

    def lookalike(self, addr: str) -> bool:
        for trusted in self.pack.trusted_aliases:
            if addr == trusted:
                continue
            if _edit_distance(addr, trusted) <= _LOOKALIKE_EDITS:
                return True
        return False

    def evaluate(self, f: TxFeatures) -> list[str]:
        """Deterministic named rules; each hit is a human-readable reason."""
        hits: list[str] = []
        if not f.seen_before:
            hits.append("first payment to a never-seen address")
        if f.contact_verified is False:
            hits.append("recipient contact is unverified")
        if self.matches_phrase(f"{f.memo} {f.utterance}"):
            hits.append("message matches a known scam pattern (rule pack)")
        if self.lookalike(f.destination):
            hits.append("address is a near-copy of a trusted one (possible swap)")
        if f.balance_shard and f.amount_shard >= 0.95 * f.balance_shard:
            hits.append("this send drains nearly your whole balance")
        return hits


def _edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


# ---------------------------------------------------------------------------
# Sequence model — Markov transition stats over address labels
# ---------------------------------------------------------------------------


class SequenceModel:
    """Counts label transitions (clean→flagged etc.) over local history.

    A 'fresh cluster' of destinations whose predecessors were all flagged is
    the weak signal a bigger sequence model would learn; v1 exposes it as one
    bounded feature contribution.
    """

    def __init__(self):
        self.transitions: dict[tuple[str, str], int] = {}

    def observe(self, prev_label: str, next_label: str) -> None:
        k = (prev_label, next_label)
        self.transitions[k] = self.transitions.get(k, 0) + 1

    def flag_probability(self, prev_label: str) -> float:
        total = sum(c for (p, _n), c in self.transitions.items() if p == prev_label)
        if not total:
            return 0.0
        bad = sum(c for (p, n), c in self.transitions.items()
                  if p == prev_label and n == "flagged")
        return bad / total


# ---------------------------------------------------------------------------
# GBDTLite — logistic scorer with int8-quantized weights
# ---------------------------------------------------------------------------


@dataclass
class ModelBundle:
    """Signed model artifact (§8.2 update channel). Quantized for ≤5 MB."""

    name: str = "fraud_det_v1"
    version: str = "1.0.0"
    weights_q: tuple[int, ...] = ()     # int8, scale=WEIGHT_SCALE
    bias_q: int = 0
    feature_names: tuple[str, ...] = FEATURE_NAMES

    WEIGHT_SCALE = 127.0 / 4.0          # logits weight range assumed ±4

    def dequantize(self) -> tuple[list[float], float]:
        s = 4.0 / 127.0
        return [w * s for w in self.weights_q], self.bias_q * s

    @staticmethod
    def quantize(weights: Sequence[float], bias: float) -> "ModelBundle":
        s = 127.0 / 4.0
        q = tuple(max(-127, min(127, round(w * s))) for w in weights)
        return ModelBundle(weights_q=q, bias_q=max(-127, min(127, round(bias * s))))


class GBDTLite:
    """Linear-in-logits ensemble stand-in; same I/O contract as the real GBDT."""

    def __init__(self, bundle: ModelBundle):
        self.bundle = bundle
        self.weights, self.bias = bundle.dequantize()
        if len(self.weights) != len(FEATURE_NAMES):
            raise ValueError("bundle feature mismatch — refuse to score stale model")

    def raw_logit(self, vec: Sequence[float]) -> float:
        return self.bias + sum(w * x for w, x in zip(self.weights, vec))

    def score(self, vec: Sequence[float]) -> float:
        return _sigmoid(self.raw_logit(vec))


# ---------------------------------------------------------------------------
# RiskReport — the ONLY output type of this module (advisory, §8 hard rule)
# ---------------------------------------------------------------------------

RISK_WATCH = 0.30       # informational band edge (§7.3 step 4)
RISK_INTERRUPT = 0.70   # calm-interruption card threshold


@dataclass
class RiskReport:
    score: float
    band: str                          # "clean" | "watch" | "interrupt"
    reasons: list[str] = field(default_factory=list)
    model_version: str = "fraud_det_v1"
    rule_pack_digest: str = ""
    sleep_on_it_seconds: int = 0       # 10-second pause option when interrupt

    def to_json(self) -> dict:
        return {"score": self.score, "band": self.band, "reasons": self.reasons,
                "model_version": self.model_version,
                "rule_pack_digest": self.rule_pack_digest,
                "sleep_on_it_seconds": self.sleep_on_it_seconds}


def band_for(score: float) -> str:
    if score >= RISK_INTERRUPT:
        return "interrupt"
    if score >= RISK_WATCH:
        return "watch"
    return "clean"


# ---------------------------------------------------------------------------
# FraudDetector — façade wired into the signing gate
# ---------------------------------------------------------------------------


class FraudDetector:
    def __init__(self, model: Optional[GBDTLite] = None,
                 overlay: Optional[RuleOverlay] = None,
                 sequence: Optional[SequenceModel] = None):
        self.overlay = overlay or RuleOverlay()
        self.sequence = sequence or SequenceModel()
        self.model = model or GBDTLite(DEFAULT_BUNDLE)

    # -- scoring ----------------------------------------------------------

    def score(self, f: TxFeatures) -> RiskReport:
        vec = f.vector(self.overlay)
        return self.score_vector(vec, f)

    def score_vector(self, vec: Sequence[float],
                     f: Optional[TxFeatures] = None) -> RiskReport:
        """Score a raw feature vector (the exact layout FL clients train on,
        §8.3). This is the shared entry point for the FedAvg loop: after the
        aggregator finalizes a round, ``apply_global_delta`` rebuilds the
        model here and the SAME pipeline (rules floor + sequence bonus) keeps
        serving risk reports — no user data involved either way."""
        if len(vec) != len(FEATURE_NAMES):
            raise ValueError(f"expected {len(FEATURE_NAMES)} features")
        ml = self.model.score(vec)
        rule_hits = self.overlay.evaluate(f) if f is not None else []
        # Belt-and-braces: rules act as a floor, ML as the fine-grained
        # ranking. A confirmed scam-phrase hit can never be out-voted down.
        floor = 0.0
        if any("scam pattern" in r for r in rule_hits):
            floor = max(floor, 0.85)
        if any("near-copy" in r for r in rule_hits):
            floor = max(floor, 0.75)
        if any("drains" in r for r in rule_hits):
            floor = max(floor, 0.60)
        seq_bonus = 0.10 * self.sequence.flag_probability("flagged")
        score = round(min(1.0, max(ml, floor) + seq_bonus), 4)
        reasons = list(rule_hits)
        if ml >= RISK_WATCH and not rule_hits:
            reasons.append("behavioral model raised a soft warning")
        return RiskReport(score=score, band=band_for(score), reasons=reasons,
                          model_version=self.model.bundle.name + "_" + self.model.bundle.version,
                          rule_pack_digest=self.overlay.pack.digest,
                          sleep_on_it_seconds=10 if band_for(score) == "interrupt" else 0)

    # -- FedAvg adoption hook (§8.3) ----------------------------------------

    def apply_global_delta(self, delta: Sequence[float]) -> "FraudDetector":
        """Adopt an aggregated weight delta (weights ++ bias, last slot) into
        the live model and return a detector carrying the NEW signed bundle.

        Production path: the server publishes ModelBundle artifacts that are
        code-signed; wallets verify the signature before swapping bundles.
        Here the re-quantized bundle is constructed directly — same interface,
        quantization grid identical to the shipped default so deltas survive
        the int8 round trip at clip-scale resolution.
        """
        if len(delta) != len(FEATURE_NAMES) + 1:
            raise ValueError("delta must be weights ++ bias")
        cur_w, cur_b = self.model.bundle.dequantize()   # live (dequantized) model
        new_w = [w + d for w, d in zip(cur_w, delta[:-1])]
        bundle = ModelBundle.quantize(weights=new_w, bias=cur_b + delta[-1])
        self.model = GBDTLite(bundle)
        return self

    # -- learning hooks (used by FL client; local-only in prototype) -------

    def observe_outcome(self, f: TxFeatures, label: str) -> None:
        """Wallet calls this after the user's final decision/report-back.
        Feeds the sequence model only — weight updates happen via FL."""
        prev = "flagged" if f.seen_before is False else "clean"
        self.sequence.observe(prev, label)


# Default prototype weights: tuned so benign first-payments stay under WATCH
# while composite scam signatures clear INTERRUPT. Trained offline on the
# synthetic corpus in tests; real build replaces via signed ModelBundle.
DEFAULT_BUNDLE = ModelBundle.quantize(
    weights=[0.55, 0.45, 2.60, 2.40, 1.10, 0.80, 0.50, 0.30],
    bias=-1.75,
)
