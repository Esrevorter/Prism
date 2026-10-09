"""Transaction simulator v0 — ai/ deliverable (spec §8.2, §7.3 step 3).

Deterministic executor against a local chain snapshot + template explainer.
Two hard boundaries from §8:

  * Numeric facts in the pre-sign preview come from HERE, never from an LLM
    (anti-hallucination boundary). The explainer is template-based.
  * The simulator's output is advisory input to the UI; it does not sign and
    cannot be bypassed INTO signing either — ``SigningGate`` refuses any
    intent that has not passed a fresh simulation (§7.3 steps 3–5 order).

Wiring: ``SigningGate.review(intent)`` composes simulate() + FraudDetector
and returns a Review whose ``proceed`` flag the wallet UI consumes. The gate
mirrors the structural checks the MPC signer enforces anyway (belt-and-braces
§8.4), so even a UI bug can't broadcast an inert intent.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Optional

from prism.ai.fraud import FraudDetector, RiskReport, TxFeatures
from prism.wallet.intent import (BASE_FEE_SHARD, MAX_PRIORITY_MULTIPLIER,
                                 PRSM_RATE, SHARDS_PER_PRSM, Intent,
                                 IntentError, Quote, StubOracle, prsm_to_shard,
                                 shard_to_prsm)

PRIORITY_STEPS = {"base_only": 0, "priority_low": 1, "priority_max":
                  MAX_PRIORITY_MULTIPLIER - 1}


# ---------------------------------------------------------------------------
# Local chain snapshot (§8.2: "deterministic executor against local snapshot")
# ---------------------------------------------------------------------------


@dataclass
class SnapshotOutput:
    """A spendable owned output as seen by the wallet post-scan (§6.2)."""

    txid: str
    output_index: int
    amount_shard: int
    maturity: str = "unlocked"        # unlocked | locked (coinbase-style)


class ChainSnapshot:
    """Minimal UTXO-ish view for deterministic effect prediction."""

    def __init__(self, outputs: list[SnapshotOutput] | None = None):
        self.outputs = list(outputs or [])
        self.height = 0

    @property
    def balance_shard(self) -> int:
        return sum(o.amount_shard for o in self.outputs if o.maturity == "unlocked")

    def coin_select(self, need_shard: int) -> list[SnapshotOutput]:
        """Deterministic largest-first selection (auditable, no RNG)."""
        pool = sorted((o for o in self.outputs if o.maturity == "unlocked"),
                      key=lambda o: (-o.amount_shard, o.txid, o.output_index))
        picked: list[SnapshotOutput] = []
        total = 0
        for o in pool:
            picked.append(o)
            total += o.amount_shard
            if total >= need_shard:
                return picked
        return []  # insufficient funds


# ---------------------------------------------------------------------------
# Simulation result + explanation templates (§8.2: not generative)
# ---------------------------------------------------------------------------


@dataclass
class SimResult:
    ok: bool
    amount_shard: int                  # resolved principal (post re-quote check)
    fee_shard: int                     # base + priority
    change_shard: int
    total_out_shard: int
    balance_before_shard: int
    balance_after_shard: int
    quote_refreshed: bool = False      # stale window → auto re-quote (E5)
    errors: list[str] = field(default_factory=list)
    plain_english: str = ""
    sim_digest: str = ""               # binds preview → gate (no swap attacks)

    def to_json(self) -> dict:
        d = {k: getattr(self, k) for k in
             ("ok", "amount_shard", "fee_shard", "change_shard",
              "total_out_shard", "balance_before_shard", "balance_after_shard",
              "quote_refreshed")}
        d["errors"] = list(self.errors)
        d["plain_english"] = self.plain_english
        d["sim_digest"] = self.sim_digest
        return d


_PRIVACY_LINES = {
    "default_max": "It will appear private to everyone except {who} and you.",
    "disclosed_to_recipient": "Private on-chain; {who} also gets a copyable disclosure ID.",
    "public_audit": "WARNING: this transfer opts into audit-visible disclosure.",
}

_ACTION_VERBS = {"transfer": "sends", "stake": "stakes",
                 "open_channel": "opens a payment channel funded with"}


def _usd(shard: int) -> str:
    return f"${shard / SHARDS_PER_PRSM * PRSM_RATE:.4f}"


def explain(sim: SimResult, intent: Intent, *, recipient_name: str) -> str:
    """Template renderer — every number traces to the executor, §8.2."""
    c = intent.compiled
    amt_txt = f"{shard_to_prsm(sim.amount_shard)} PRSM ({_usd(sim.amount_shard)})"
    if "fiat_quote" in c.amount:
        q = c.amount["fiat_quote"]
        amt_txt = (f"≈{shard_to_prsm(sim.amount_shard)} PRSM "
                   f"(${float(q['value']):.2f} @ ${q['rate']}/PRSM)")
    who = "you" if c.action == "stake" else recipient_name
    priv = _PRIVACY_LINES[c.privacy_mode].format(who=who)
    if c.action == "stake":
        priv = "It will appear private to everyone except you."
    lines = [f"This {_ACTION_VERBS[c.action]} {amt_txt}. Fee: {_usd(sim.fee_shard)}. {priv}"]
    if sim.change_shard:
        lines.append(f"Change of {shard_to_prsm(sim.change_shard)} PRSM returns to your wallet.")
    lines.append(f"Balance after: {shard_to_prsm(sim.balance_after_shard)} PRSM.")
    if sim.quote_refreshed:
        lines.append("Note: your rate window had expired, so the price was re-locked just now.")
    if c.memo:
        lines.append(f"Memo: {c.memo!r} (stays encrypted on-chain).")
    lines.append("Confirm?")
    return " ".join(lines)


# ---------------------------------------------------------------------------
# Deterministic execution
# ---------------------------------------------------------------------------


def _resolved_amount_shard(intent: Intent, *, now_unix: int,
                           oracle: Optional[StubOracle]) -> tuple[int, bool]:
    """Principal in shards; re-quotes stale fiat windows instead of failing
    silently (E5 stale-rate: refresh explicitly, flag it in the preview)."""
    amt = intent.compiled.amount
    refreshed = False
    if "or_prsm" in amt:
        return int(amt["or_prsm"]), refreshed
    q = amt["fiat_quote"]
    value, rate = float(q["value"]), float(q["rate"])
    if now_unix >= int(q["locked_until_unix"]):
        if oracle is None:
            raise IntentError("quote window expired and no oracle available to re-quote")
        nq = oracle.quote(q["currency"], value, now_unix=now_unix,
                          max_slippage_pct=q["max_slippage_pct"])
        q = {"currency": nq.currency, "value": nq.fiat_value, "oracle": nq.oracle,
             "rate": nq.rate, "max_slippage_pct": nq.max_slippage_pct,
             "locked_until_unix": nq.locked_until_unix}
        intent.compiled.amount = {"fiat_quote": q}
        rate = nq.rate
        refreshed = True
    return prsm_to_shard(round(value / rate, 8)), refreshed


def simulate_intent(intent: Intent, snapshot: ChainSnapshot, *, now_unix: int,
                    oracle: Optional[StubOracle] = None,
                    recipient_name: str = "the recipient") -> SimResult:
    """Run the intent against the snapshot; produce numbers + preview text."""
    errors: list[str] = []
    try:
        intent.validate()
    except IntentError as e:
        return SimResult(ok=False, amount_shard=0, fee_shard=0, change_shard=0,
                         total_out_shard=0, balance_before_shard=snapshot.balance_shard,
                         balance_after_shard=snapshot.balance_shard,
                         errors=[str(e)], plain_english=f"I can't preview this yet: {e}")

    try:
        amount, refreshed = _resolved_amount_shard(intent, now_unix=now_unix,
                                                   oracle=oracle)
    except IntentError as e:
        return SimResult(ok=False, amount_shard=0, fee_shard=BASE_FEE_SHARD,
                         change_shard=0, total_out_shard=0,
                         balance_before_shard=snapshot.balance_shard,
                         balance_after_shard=snapshot.balance_shard,
                         errors=[str(e)],
                         plain_english=f"I can't preview this yet: {e}")
    fee = BASE_FEE_SHARD + PRIORITY_STEPS[intent.compiled.fee] * BASE_FEE_SHARD
    need = amount + fee
    before = snapshot.balance_shard
    coins = snapshot.coin_select(need)
    if not coins:
        sim = SimResult(ok=False, amount_shard=amount, fee_shard=fee, change_shard=0,
                        total_out_shard=need, balance_before_shard=before,
                        balance_after_shard=before, quote_refreshed=refreshed,
                        errors=[f"insufficient funds: need {shard_to_prsm(need)} PRSM, "
                                f"have {shard_to_prsm(before)}"])
        sim.plain_english = ("You don't have enough to cover this plus the fee. "
                             f"You'd need {shard_to_prsm(need)} PRSM "
                             f"(you have {shard_to_prsm(before)}).")
        return sim
    change = sum(c.amount_shard for c in coins) - need
    sim = SimResult(ok=True, amount_shard=amount, fee_shard=fee, change_shard=change,
                    total_out_shard=need, balance_before_shard=before,
                    balance_after_shard=before - need, quote_refreshed=refreshed)
    sim.plain_english = explain(sim, intent, recipient_name=recipient_name)
    blob = repr((intent.canonical_digest(), sim.to_json(), now_unix))
    sim.sim_digest = hashlib.sha256(blob.encode()).hexdigest()
    return sim


# ---------------------------------------------------------------------------
# Signing gate — where fraud flags + simulation meet human confirmation
# ---------------------------------------------------------------------------


@dataclass
class Review:
    sim: SimResult
    risk: RiskReport
    proceed: bool                     # UI may show confirm button iff True
    interruption: bool                # calm card + sleep-on-it offered (§7.3 step 4)
    reasons: list[str] = field(default_factory=list)
    policy_refusals: list[str] = field(default_factory=list)  # agent grants only (§8.4)


class SigningGate:
    """Advisory review composed at the point of confirmation (§7.3 3–5).

    Invariants:
      * A failed simulation NEVER yields proceed=True — but this is a UX
        guarantee only; the MPC signer independently re-validates structure.
      * Risk 'interrupt' band requires an explicit acknowledgement token
        from the user before the gate opens (the model advises, the human
        decides — §8 hard rule in both directions).
      * This class holds no key material and emits no signature request.
    """

    def __init__(self, detector: Optional[FraudDetector] = None,
                 engine=None):
        self.detector = detector or FraudDetector()
        # Optional prism.wallet.agent_auth.PolicyEngine — when present, agent
        # proposals are policy-checked HERE, at the same place the human
        # confirmation gate lives (§8.4 belt-and-braces; the signer re-checks
        # again at signing time regardless).
        self.engine = engine

    def review(self, intent: Intent, snapshot: ChainSnapshot, *, now_unix: int,
               oracle: Optional[StubOracle] = None,
               recipient_name: str = "the recipient",
               contact_verified: Optional[bool] = None,
               acknowledged_risk: bool = False,
               grant=None, spent_this_month_shard: int = 0) -> Review:
        sim = simulate_intent(intent, snapshot, now_unix=now_unix,
                              oracle=oracle, recipient_name=recipient_name)
        feats = TxFeatures(
            destination=intent.compiled.recipient_ref or "",
            amount_shard=sim.amount_shard or intent.total_cost_shard(),
            balance_shard=snapshot.balance_shard,
            memo=intent.compiled.memo or "",
            utterance=intent.raw_utterance,
            contact_verified=contact_verified,
            privacy_mode=intent.compiled.privacy_mode,
            seen_before=bool(snapshot.outputs),   # prototype history proxy
            now_unix=now_unix,
        )
        risk = self.detector.score(feats)
        reasons = list(sim.errors) + ([f"risk={risk.band}: {r}" for r in risk.reasons]
                                      if risk.band != "clean" else [])
        # Agent path (§8.4): when this intent rides a scoped grant, the SAME
        # gate that shows humans their preview also runs the policy engine —
        # so a UI bug can never render a confirm button on a violating agent
        # action. Refusals name every violated constraint at once.
        policy_refusals: list[str] = []
        if grant is not None:
            if self.engine is None:
                policy_refusals.append(
                    "grant presented but gate has no PolicyEngine wired")
            else:
                policy_refusals = self.engine.check_proposal(
                    intent, grant, now_unix=now_unix,
                    spent_this_month_shard=spent_this_month_shard,
                    contact_verified=contact_verified)
        interruption = risk.band == "interrupt"
        proceed = (sim.ok and not policy_refusals
                   and (not interruption or acknowledged_risk))
        return Review(sim=sim, risk=risk, proceed=proceed,
                      interruption=interruption, reasons=reasons,
                      policy_refusals=policy_refusals)
