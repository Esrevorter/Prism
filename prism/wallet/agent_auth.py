"""Autonomous-agent scoped-permissions framework — wallet/ deliverable
(spec §8.4, §7.7 DCA example, §6.2 "Agent Permission Grant" / "Agent Action Log").

The belt-and-braces rule (§8.4): constraints compile to a policy engine
enforced OUTSIDE the LLM/agent loop — even a fully compromised agent process
cannot exceed grant bounds, because the *signer* re-checks policy at signing
time and an auto-execution token is only ever produced for an intent that
passes here.

Pieces:
  * ``Grant``            — allowlisted capability + numeric constraints +
                           veto window + validity period (§6.2 schema).
  * ``PolicyEngine``     — pure predicate evaluation of an Intent against a
                           Grant. Deterministic, auditable, no model input.
  * ``AgentActionLog``   — hash-chained, tamper-evident local audit log
                           (``hashchain_prev`` per §6.2).
  * ``AgentRuntime``     — proposal → digest → 24 h veto window → auto-execute
                           iff unvetoed AND risk model clean (§7.7 step 2);
                           instant execution only for whitelisted destination
                           under the user's comfort threshold (§8.4); one-tap
                           revoke; monthly explainable report.

States mirror §6.2 exactly: proposed | vetoed | executed_after_veto_window |
blocked_by_risk_model (plus instant_executed for the §8.4 fast path).
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from prism.wallet.intent import (BASE_FEE_SHARD, Intent, IntentError,
                                 prsm_to_shard, shard_to_prsm)

# ---------------------------------------------------------------------------
# Capability allowlist (§8.4: NO arbitrary contract calls in v1)
# ---------------------------------------------------------------------------

CAPABILITIES = {
    "transfer.self":        "move funds between the user's own accounts/subaddresses",
    "buy_prsm_fiat_drip":   "scheduled small PRSM purchases (DCA / round-ups)",
    "channel_topup":        "top up an existing payment channel from self",
    "report_generate":      "produce local explainability reports (no signing)",
}

ACTION_TO_CAPABILITY = {          # which Intent action each verb may compile to
    "transfer": ("transfer.self", "buy_prsm_fiat_drip"),
    "open_channel": ("channel_topup",),
    "stake": ("transfer.self",),
}

VETO_WINDOW_HOURS_DEFAULT = 24    # §7.7 step 2 / §8.4
RISK_CLEAN_MAX = 0.30             # "risk model clean" == below WATCH band (§7.3)


class PolicyViolation(Exception):
    """Raised with a human-readable reason; goes into the action log."""


# ---------------------------------------------------------------------------
# Grant (§6.2 "Agent Permission Grant")
# ---------------------------------------------------------------------------


@dataclass
class Constraints:
    max_per_month_shard: Optional[int] = None
    max_per_action_shard: Optional[int] = None
    price_ceiling_usd: Optional[float] = None       # fiat intents only
    destinations_whitelist: tuple[str, ...] = ()    # e.g. ("self_subaddr:dca",)
    comfort_threshold_shard: Optional[int] = None   # instant-exec cap (§8.4)

    def to_json(self) -> dict:
        return {"max_per_month_shard": self.max_per_month_shard,
                "max_per_action_shard": self.max_per_action_shard,
                "price_ceiling_usd": self.price_ceiling_usd,
                "destinations_whitelist": list(self.destinations_whitelist),
                "comfort_threshold_shard": self.comfort_threshold_shard}


@dataclass
class Grant:
    agent: str                          # e.g. "auto_dutcher.v1"
    capability: str                     # key of CAPABILITIES
    constraints: Constraints = field(default_factory=Constraints)
    veto_window_hours: float = VETO_WINDOW_HOURS_DEFAULT
    valid_until_unix: int = 0           # 0 => invalid grant, refuse everything
    grant_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    revoked_at: Optional[int] = None

    # -- lifecycle ----------------------------------------------------------

    def revoke(self, *, now_unix: int) -> None:
        """One-tap revoke (§7.7 step 3): immediate, irreversible per grant."""
        if self.revoked_at is None:
            self.revoked_at = now_unix

    @property
    def active(self) -> bool:
        return self.revoked_at is None

    def validate_shape(self) -> None:
        if self.capability not in CAPABILITIES:
            raise PolicyViolation(f"capability {self.capability!r} not allowlisted")
        if self.veto_window_hours < 0:
            raise PolicyViolation("veto window cannot be negative")
        c = self.constraints
        if c.max_per_month_shard is not None and c.max_per_month_shard <= 0:
            raise PolicyViolation("monthly cap must be positive")
        if c.max_per_action_shard is not None and c.max_per_action_shard <= 0:
            raise PolicyViolation("per-action cap must be positive")
        if c.comfort_threshold_shard is not None:
            if c.max_per_action_shard and c.comfort_threshold_shard > c.max_per_action_shard:
                raise PolicyViolation("comfort threshold above per-action cap is incoherent")

    def to_json(self) -> dict:
        return {"grant_id": self.grant_id, "agent": self.agent,
                "capability": self.capability,
                "constraints": self.constraints.to_json(),
                "veto_window_hours": self.veto_window_hours,
                "valid_until": self.valid_until_unix,
                "revoked_at": self.revoked_at}


# ---------------------------------------------------------------------------
# Policy engine — enforced outside the agent, at proposal AND signing time
# ---------------------------------------------------------------------------


def _intent_amount_shard(intent: Intent) -> int:
    amt = intent.compiled.amount
    if "or_prsm" in amt:
        return int(amt["or_prsm"])
    q = amt.get("fiat_quote")
    if q:
        return prsm_to_shard(round(float(q["value"]) / float(q["rate"]), 8))
    raise PolicyViolation("intent has no resolved amount — agents may not propose drafts")


class PolicyEngine:
    """Pure checks. Every refusal names the violated constraint so the digest
    card can show WHY (explainability, §7.7 step 3)."""

    def check_proposal(self, intent: Intent, grant: Grant, *, now_unix: int,
                       spent_this_month_shard: int = 0,
                       contact_verified: Optional[bool] = None) -> list[str]:
        """Return [] if allowed, else list of violation reasons (all of them,
        so one review shows every problem — neuro-inclusive: no peek-a-boo
        sequential errors)."""
        reasons: list[str] = []
        g = grant
        try:
            g.validate_shape()
        except PolicyViolation as e:
            return [str(e)]
        if not g.active:
            reasons.append("grant revoked")
        if now_unix >= g.valid_until_unix:
            reasons.append("grant expired")
        # verb must compile to this capability (§8.4 allowlist closure)
        allowed_actions = ACTION_TO_CAPABILITY.get(g.capability, ())
        if intent.compiled.action not in allowed_actions:
            reasons.append(f"action {intent.compiled.action!r} not permitted by "
                           f"capability {g.capability!r}")
        # agents may never target third parties unless whitelisted as such
        ref = intent.compiled.recipient_ref or ""
        wl = g.constraints.destinations_whitelist
        if wl and ref not in wl:
            reasons.append(f"destination {ref!r} not in grant whitelist {list(wl)}")
        if not ref and g.capability != "report_generate":
            reasons.append("agent intents must have a fully resolved recipient")
        # structural inertness: an intent the signer would reject is refused now
        try:
            intent.validate()
            if intent.needs_clarify:
                reasons.append("unresolved clarification in agent proposal")
        except IntentError as e:
            reasons.append(f"inert intent: {e}")
        # numeric bounds (principal; fee rides on top but is bounded too)
        amount = 0
        try:
            amount = _intent_amount_shard(intent)
        except PolicyViolation as e:
            reasons.append(str(e))
        if amount:
            cost = amount + BASE_FEE_SHARD
            c = g.constraints
            if c.max_per_action_shard and amount > c.max_per_action_shard:
                reasons.append(f"amount {shard_to_prsm(amount)} PRSM exceeds "
                               f"per-action cap {shard_to_prsm(c.max_per_action_shard)} PRSM")
            if c.max_per_month_shard and spent_this_month_shard + cost > c.max_per_month_shard:
                remaining = max(0, c.max_per_month_shard - spent_this_month_shard)
                reasons.append(f"would exceed monthly cap: {shard_to_prsm(cost)} PRSM "
                               f"requested, {shard_to_prsm(remaining)} PRSM left")
            if "fiat_quote" in intent.compiled.amount and c.price_ceiling_usd is not None:
                rate = float(intent.compiled.amount["fiat_quote"]["rate"])
                if rate > c.price_ceiling_usd:
                    reasons.append(f"price ${rate}/PRSM above ceiling "
                                   f"${c.price_ceiling_usd}")
        return reasons

    def check_signing(self, intent: Intent, grant: Grant, *, now_unix: int,
                      spent_this_month_shard: int = 0,
                      exec_not_before_unix: int,
                      risk_score: float,
                      digest_recorded: bool) -> list[str]:
        """Second enforcement point AT THE SIGNER (§8.4 belt-and-braces):
        adds veto-window timing + risk-clean + digest-visible requirements."""
        reasons = self.check_proposal(intent, grant, now_unix=now_unix,
                                      spent_this_month_shard=spent_this_month_shard)
        if not digest_recorded:
            reasons.append("proposal was never surfaced in the quiet digest")
        if now_unix < exec_not_before_unix:
            hours_left = (exec_not_before_unix - now_unix) / 3600.0
            reasons.append(f"veto window still open ({hours_left:.1f} h remaining)")
        if risk_score >= RISK_CLEAN_MAX:
            reasons.append(f"risk model not clean (score {risk_score:.2f})")
        return reasons

    def instant_path_allowed(self, intent: Intent, grant: Grant, *,
                             contact_verified: bool) -> bool:
        """§8.4: instant execution ONLY for verified-whitelisted recipients
        AND amounts under the user-set comfort threshold."""
        c = grant.constraints
        if c.comfort_threshold_shard is None:
            return False
        ref = intent.compiled.recipient_ref or ""
        if ref not in c.destinations_whitelist:
            return False
        if not contact_verified:
            return False
        try:
            amount = _intent_amount_shard(intent)
        except PolicyViolation:
            return False
        return amount <= c.comfort_threshold_shard


# ---------------------------------------------------------------------------
# Hash-chained action log (§6.2 "Agent Action Log")
# ---------------------------------------------------------------------------

VALID_ACTION_STATES = {"proposed", "vetoed", "executed_after_veto_window",
                       "blocked_by_risk_model", "instant_executed",
                       "blocked_by_policy"}


@dataclass
class ActionRecord:
    grant_id: str
    state: str
    explanation: str
    proposed_intent: dict
    action_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at_unix: int = 0
    hashchain_prev: str = ""

    def payload_digest(self) -> str:
        blob = json.dumps({"action_id": self.action_id, "grant_id": self.grant_id,
                           "state": self.state, "explanation": self.explanation,
                           "proposed_intent": self.proposed_intent,
                           "created_at_unix": self.created_at_unix,
                           "hashchain_prev": self.hashchain_prev},
                          sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()


class AgentActionLog:
    """Append-only, tamper-evident. Genesis prev = sha256('prism-agent-log')."""

    GENESIS = hashlib.sha256(b"prism-agent-log").hexdigest()

    def __init__(self):
        self.records: list[ActionRecord] = []

    def append(self, *, grant_id: str, state: str, explanation: str,
               proposed_intent: dict, now_unix: int) -> ActionRecord:
        if state not in VALID_ACTION_STATES:
            raise ValueError(f"unknown action state {state!r}")
        rec = ActionRecord(grant_id=grant_id, state=state,
                           explanation=explanation,
                           proposed_intent=proposed_intent,
                           created_at_unix=now_unix,
                           hashchain_prev=(self.records[-1].payload_digest()
                                           if self.records else self.GENESIS))
        self.records.append(rec)
        return rec

    def verify_chain(self) -> bool:
        prev = self.GENESIS
        for r in self.records:
            if r.hashchain_prev != prev:
                return False
            prev = r.payload_digest()
        return True

    def transition(self, action_id: str, new_state: str, explanation: str,
                   *, now_unix: int) -> ActionRecord:
        """State changes are NEW appended records (log stays append-only)."""
        src = next((r for r in self.records if r.action_id == action_id), None)
        if src is None:
            raise KeyError(action_id)
        return self.append(grant_id=src.grant_id, state=new_state,
                           explanation=explanation,
                           proposed_intent=src.proposed_intent,
                           now_unix=now_unix)


# ---------------------------------------------------------------------------
# Runtime — proposals, digest, veto windows, auto-execution
# ---------------------------------------------------------------------------


@dataclass
class PendingAction:
    record: ActionRecord
    intent: Intent
    exec_not_before_unix: int
    risk_score: float
    vetoed: bool = False
    executed: bool = False

    @property
    def action_id(self) -> str:
        return self.record.action_id


class SignerStub:
    """Stand-in for the MPC signer that ENFORCES POLICY (§8.4).

    Production: prism.mpc.frost ceremony behind the wallet's share holders.
    The stub documents the contract: the signer calls
    ``PolicyEngine.check_signing`` itself and refuses regardless of whatever
    the agent runtime believes. It receives an Intent + attestations only —
    never grant-management authority.
    """

    def __init__(self, engine: PolicyEngine):
        self.engine = engine
        self.broadcast: list[Intent] = []

    def sign_and_broadcast(self, intent: Intent, grant: Grant, *, now_unix: int,
                           spent_this_month_shard: int,
                           exec_not_before_unix: int, risk_score: float,
                           digest_recorded: bool) -> tuple[bool, list[str]]:
        violations = self.engine.check_signing(
            intent, grant, now_unix=now_unix,
            spent_this_month_shard=spent_this_month_shard,
            exec_not_before_unix=exec_not_before_unix,
            risk_score=risk_score, digest_recorded=digest_recorded)
        if violations:
            return False, violations
        # (real build: frost.sign_threshold(...) then Dandelion++ broadcast)
        intent.status = "broadcast"
        self.broadcast.append(intent)
        return True, []


class AgentRuntime:
    """Owns grants + pending actions; produces the quiet digest and monthly
    report (§7.7). Contains NO signing authority and NO model authority —
    it shuttles advisory scores and defers final judgment to SignerStub."""

    def __init__(self, engine: Optional[PolicyEngine] = None,
                 signer: Optional[SignerStub] = None,
                 log: Optional[AgentActionLog] = None):
        self.engine = engine or PolicyEngine()
        self.log = log or AgentActionLog()
        self.signer = signer or SignerStub(self.engine)
        self.grants: dict[str, Grant] = {}
        self.pending: dict[str, PendingAction] = {}
        self.spent_by_grant: dict[str, int] = {}

    # -- grants -------------------------------------------------------------

    def issue_grant(self, grant: Grant) -> Grant:
        grant.validate_shape()
        self.grants[grant.grant_id] = grant
        self.log.append(grant_id=grant.grant_id, state="proposed",
                        explanation=f"grant issued: {grant.agent} → {grant.capability} "
                                    f"until {grant.valid_until_unix}",
                        proposed_intent={}, now_unix=int(time.time()))
        return grant

    def revoke(self, grant_id: str, *, now_unix: int) -> None:
        g = self.grants[grant_id]
        g.revoke(now_unix=now_unix)
        # kill everything already queued under this grant
        for pa in list(self.pending.values()):
            if pa.record.grant_id == grant_id:
                pa.vetoed = True
                self.log.transition(pa.action_id, "vetoed",
                                    "cascade: grant revoked", now_unix=now_unix)
        self.log.append(grant_id=grant_id, state="blocked_by_policy",
                        explanation="grant revoked (one-tap)", proposed_intent={},
                        now_unix=now_unix)

    # -- proposal path --------------------------------------------------------

    def propose(self, intent: Intent, grant_id: str, *, now_unix: int,
                risk_score: float = 0.0,
                contact_verified: bool = False) -> PendingAction:
        grant = self.grants.get(grant_id)
        if grant is None:
            raise PolicyViolation(f"unknown grant {grant_id!r}")
        violations = self.engine.check_proposal(
            intent, grant, now_unix=now_unix,
            spent_this_month_shard=self.spent_by_grant.get(grant_id, 0),
            contact_verified=contact_verified)
        rec = self.log.append(grant_id=grant_id, state="proposed",
                              explanation=self._explain(intent, grant),
                              proposed_intent=intent.to_json(),
                              now_unix=now_unix)
        if violations:
            self.log.transition(rec.action_id, "blocked_by_policy",
                                "; ".join(violations), now_unix=now_unix)
            raise PolicyViolation("; ".join(violations))
        if risk_score >= RISK_CLEAN_MAX:
            self.log.transition(rec.action_id, "blocked_by_risk_model",
                                f"risk score {risk_score:.2f} ≥ clean bound",
                                now_unix=now_unix)
            pa = PendingAction(rec, intent, 0, risk_score)
            return pa
        deadline = now_unix + int(grant.veto_window_hours * 3600)
        pa = PendingAction(rec, intent, deadline, risk_score)
        # §8.4 instant path: verified whitelisted recipient + under comfort cap
        if self.engine.instant_path_allowed(intent, grant,
                                            contact_verified=contact_verified):
            self._execute(pa, grant, now_unix=now_unix,
                          state="instant_executed")
        else:
            self.pending[pa.action_id] = pa
        return pa

    def veto(self, action_id: str, *, now_unix: int) -> None:
        pa = self.pending.pop(action_id, None)
        if pa is None:
            raise KeyError(action_id)
        pa.vetoed = True
        self.log.transition(action_id, "vetoed", "user vetoed in digest",
                            now_unix=now_unix)

    # -- tick: called by the wallet scheduler ----------------------------------

    def tick(self, *, now_unix: int, current_risk_scores: dict[str, float]
             ) -> list[ActionRecord]:
        """Auto-execute unvetoed actions whose window closed, only if the
        risk model is STILL clean at execution time (§7.7 step 2)."""
        executed = []
        for action_id, pa in list(self.pending.items()):
            if now_unix < pa.exec_not_before_unix:
                continue
            grant = self.grants[pa.record.grant_id]
            live_risk = current_risk_scores.get(action_id, pa.risk_score)
            del self.pending[action_id]
            if live_risk >= RISK_CLEAN_MAX:
                r = self.log.transition(action_id, "blocked_by_risk_model",
                                        f"risk {live_risk:.2f} at window close",
                                        now_unix=now_unix)
                executed.append(r)
                continue
            ok, violations = self.signer.sign_and_broadcast(
                pa.intent, grant, now_unix=now_unix,
                spent_this_month_shard=self.spent_by_grant.get(grant.grant_id, 0),
                exec_not_before_unix=pa.exec_not_before_unix,
                risk_score=live_risk, digest_recorded=True)
            if ok:
                r = self._execute(pa, grant, now_unix=now_unix,
                                  state="executed_after_veto_window")
            else:
                r = self.log.transition(action_id, "blocked_by_policy",
                                        "signer refused: " + "; ".join(violations),
                                        now_unix=now_unix)
            executed.append(r)
        return executed

    def _execute(self, pa: PendingAction, grant: Grant, *, now_unix: int,
                 state: str) -> ActionRecord:
        amount = _intent_amount_shard(pa.intent) + BASE_FEE_SHARD
        gid = grant.grant_id
        self.spent_by_grant[gid] = self.spent_by_grant.get(gid, 0) + amount
        self.pending.pop(pa.action_id, None)
        explanation = self._explain(pa.intent, grant, executed=True)
        return self.log.transition(pa.action_id, state, explanation,
                                   now_unix=now_unix)

    # -- reporting (§7.7 step 3: monthly explainable report) -------------------

    def digest_lines(self, *, now_unix: int) -> list[str]:
        out = []
        for pa in self.pending.values():
            hrs = max(0.0, (pa.exec_not_before_unix - now_unix) / 3600.0)
            out.append(f"{pa.record.explanation} — executes in {hrs:.0f} h "
                       f"unless vetoed (id {pa.action_id[:8]})")
        return out

    def monthly_report(self, grant_id: str, *, since_unix: int, until_unix: int) -> dict:
        executed_states = {"instant_executed", "executed_after_veto_window"}
        rows = [r for r in self.log.records
                if r.grant_id == grant_id and r.state in executed_states]
        rows = [r for r in rows if since_unix <= r.created_at_unix < until_unix]
        total = 0
        for r in rows:
            try:
                intent_json = r.proposed_intent or {}
                c = intent_json.get("compiled", {})
                amt = c.get("amount", {})
                if "or_prsm" in amt:
                    total += int(amt["or_prsm"]) + BASE_FEE_SHARD
                elif "fiat_quote" in amt:
                    q = amt["fiat_quote"]
                    total += prsm_to_shard(round(float(q["value"]) / float(q["rate"]), 8)) \
                        + BASE_FEE_SHARD
            except (KeyError, TypeError, ValueError):
                pass
        return {"grant_id": grant_id, "actions": len(rows),
                "spend_note_shard": total,
                "lines": [r.explanation for r in rows],
                "chain_ok": self.log.verify_chain()}

    @staticmethod
    def _explain(intent: Intent, grant: Grant, executed: bool = False) -> str:
        try:
            amt = shard_to_prsm(_intent_amount_shard(intent))
        except PolicyViolation:
            amt = "?"
        verb = "Bought" if executed else "Will buy"
        if grant.capability == "channel_topup":
            verb = "Topped up" if executed else "Will top up"
        elif grant.capability == "transfer.self":
            verb = "Moved" if executed else "Will move"
        return (f"{verb} {amt} PRSM under your {grant.agent} plan "
                f"({grant.capability}).")
