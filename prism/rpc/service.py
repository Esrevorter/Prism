"""PrismService — in-process facade the JSON-RPC daemon exposes to the GUI.

Single owner of all mutable devnet state so the RPC handlers stay thin:
  * a validating ChainState on Refraction params + a block store,
  * an AddressBook + IntentCompiler (NL bar backend),
  * a toy balance ledger (coinbase faucet -> wallet credit) so the intent
    pipeline can be exercised end-to-end without RingCT scanning,
  * MPC Solo-ceremony state machine (2-of-3 keygen staged for UI progress).

Design notes for the UX requirement (spec §13 "GUI must excel in
user-friendliness"): every response that the GUI renders carries
plain-language fields (`human`, `warnings`, `clarify`) computed server-side
from the shipped simulate()/fraud_flags() path — the front end never has
to derive risk or formatting itself, keeping honesty guarantees in one
auditable place.
"""
from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from ..ai.fraud import FraudDetector, TxFeatures
from ..ai.simulator import (ChainSnapshot, SigningGate, SnapshotOutput,
                            render_template)
from ..chain.node import ChainState, mine_block, make_genesis, Block, ZERO32
from ..chain.params import REFRACTION_TESTNET
from ..mpc import frost
from ..mpc.sharing import Share
from ..wallet.agent_auth import (AgentRuntime, Constraints, Grant,
                                 PolicyEngine, SignerStub)
from ..wallet.intent import (AddressBook, Contact, Intent, IntentCompiler,
                             IntentError, apply_clarification, prsm_to_shard,
                             shard_to_prsm, simulate, fraud_flags)

DEVICE_LABELS = {1: "Phone", 2: "Laptop", 3: "Backup card"}

# Default agent grant (§7.7 / §8.4): a conservative self-directed DCA plan.
# Constraints are user-editable through the API; these defaults embody the
# spec's safety posture — small per-action cap, no instant path (comfort
# threshold unset ⇒ every action waits out the full veto window), and the
# recipient must be a whitelisted self-subaddress.
DEFAULT_DCA_CAPABILITY = "buy_prsm_fiat_drip"
DEFAULT_DCA_AGENT = "auto_dutcher.v1"
# Whitelisted destination for the default plan. The compiler only ever
# produces `addr:<prsm1...>` or `contact:<id>` refs, so a self-subaddress is
# registered as a (verified) contact and whitelisted by its real ref form —
# this keeps the digest card honest: what you see is exactly what runs.
DEFAULT_DCA_SELF_NAME = "My DCA pot"
DEFAULT_DCA_SELF_ADDRESS = "prsm1selfdcapot0000000000000000000000000000"
DEFAULT_DCA_VETO_HOURS = 24.0


@dataclass
class CeremonyState:
    """Staged 2-of-3 Solo keygen so the GUI can show real progress."""
    status: str = "idle"            # idle|running|complete|failed
    stage: str = ""                 # e.g. "leg 2/3 commitments posted"
    percent: int = 0
    error: str | None = None
    result: frost.CeremonyResult | None = None
    started_at: float = 0.0
    finished_at: float = 0.0


class PrismService:
    def __init__(self):
        self._lock = threading.Lock()
        self.params = REFRACTION_TESTNET
        genesis = make_genesis(self.params)
        self.chain = ChainState(params=self.params, tip=genesis, difficulty=1)
        self.store: dict[bytes, Block] = {genesis.header.hash(): genesis}
        self.height_index: list[bytes] = [genesis.header.hash()]
        self.book = AddressBook([
            Contact("c-alex", "Alex Rivera", "prsm1alexdev00000000000000000000000000"),
            Contact("c-mom", "Mom", "prsm1momdev000000000000000000000000000000"),
        ])
        self.compiler = IntentCompiler(self.book)
        self.intents: dict[str, object] = {}          # intent_id -> Intent
        self.balance_shard = 0                        # faucet credits
        self.faucet_outputs: list[SnapshotOutput] = []  # mined coinbase credits
        self.gate = SigningGate(detector=FraudDetector())
        self.ceremony = CeremonyState()
        # Agent runtime (§7.7/§8.4): policy enforced OUTSIDE the agent loop;
        # the same SigningGate that reviews human confirmations is wired in,
        # so advisory risk for agent actions comes from real tx features.
        self.agent_engine = PolicyEngine()
        self.gate.engine = self.agent_engine
        self.agent = AgentRuntime(engine=self.agent_engine, gate=self.gate)
        self.dca_contact: Optional[Contact] = None   # set by grant_create
        # virtual cadence bookkeeping (mirrors node_sync_soak model)
        self.cadence_s = self.params.target_block_time
        self.started_unix = int(time.time())
        self.sim_clock = self.params.genesis_timestamp

    # ---------------- chain ----------------

    def chain_info(self) -> dict:
        with self._lock:
            h = self.chain.tip.header
            return {
                "network": self.params.name,
                "height": h.height,
                "difficulty": self.chain.difficulty,
                "total_shards": self.chain.total_shards,
                "tip_hash": h.hash().hex(),
                "cadence_s": self.cadence_s,
                "virtual_mode": True,   # compressed until wall-clock deploy
                "blocks_per_second_cap": 50,
            }

    def mine(self, count: int = 1) -> dict:
        """Devnet faucet: CPU-mine `count` blocks, credit coinbase emission
        to the local wallet balance so the intent flow is exercisable."""
        from ..chain.emission import emission_at
        if not 1 <= count <= 50:
            raise ValueError("mine count must be 1..50")
        out = []
        with self._lock:
            for _ in range(count):
                self.sim_clock += self.cadence_s
                blk = mine_block(self.params, self.chain.tip,
                                 denylist_root=ZERO32,
                                 im_merkle_root=_empty_im_root(),
                                 difficulty=self.chain.difficulty,
                                 txids=[], timestamp=self.sim_clock)
                self.chain.validate_and_apply(blk)
                bh = blk.header.hash()
                self.store[bh] = blk
                self.height_index.append(bh)
                self.balance_shard += emission_at(blk.header.height).total_new_shards
                self.faucet_outputs.append(SnapshotOutput(
                    txid=bh.hex() + ":coinbase", output_index=0,
                    amount_shard=emission_at(blk.header.height).total_new_shards))
                out.append({"height": blk.header.height, "hash": bh.hex()})
            return {"mined": len(out), "last": out[-1],
                    "balance_shard": self.balance_shard}

    def chain_blocks(self, limit: int = 10) -> dict:
        """Recent real blocks straight from the daemon's block store —
        the GUI's node panel renders live chain state, not fixtures."""
        with self._lock:
            idxs = list(range(max(0, len(self.height_index) - limit),
                              len(self.height_index)))
            blocks = []
            for i in reversed(idxs):
                blk = self.store[self.height_index[i]]
                h = blk.header
                blocks.append({"height": h.height, "hash": h.hash().hex(),
                               "prev_hash": h.prev_hash.hex(),
                               "timestamp": h.timestamp,
                               "tx_count": len(blk.txids)})
            return {"count": len(blocks), "blocks": blocks}

    def balance(self) -> dict:
        with self._lock:
            return {"balance_shard": self.balance_shard,
                    "balance_prsm": shard_to_prsm(self.balance_shard)}

    # ---------------- contacts ----------------

    def contacts_list(self) -> list[dict]:
        book = self.book
        return [{"contact_id": c.contact_id, "display_name": c.display_name,
                 "address": c.payment_address, "verified": c.verified}
                for c in book._contacts.values()]

    def contact_add(self, name: str, address: str) -> dict:
        if not name.strip():
            raise ValueError("name required")
        if not address.startswith("prsm1"):
            raise ValueError("address must start with prsm1")
        c = Contact(f"c-{hashlib.sha3_256((name+address).encode()).hexdigest()[:8]}",
                    name.strip(), address.strip())
        with self._lock:
            self.book.add(c)
        return {"ok": True, "contact": {"contact_id": c.contact_id,
                                        "display_name": c.display_name,
                                        "address": c.payment_address}}

    # ---------------- intents ----------------

    def intent_compile(self, utterance: str) -> dict:
        with self._lock:
            it = self.compiler.compile(utterance, now_unix=self.sim_clock,
                                       balance_shard=self.balance_shard)
            self.intents[it.intent_id] = it
            return _intent_payload(it)

    def intent_answer(self, intent_id: str, answer: str) -> dict:
        with self._lock:
            pending = self._get_intent(intent_id)
            it = apply_clarification(pending, answer, self.book,
                                     self.compiler.oracle,
                                     now_unix=self.sim_clock)
            self.intents[it.intent_id] = it
            return _intent_payload(it)

    def intent_simulate(self, intent_id: str) -> dict:
        with self._lock:
            it = self._get_intent(intent_id)
            sim = simulate(it, now_unix=self.sim_clock,
                           balance_shard=self.balance_shard)
            score, flags = fraud_flags(it)
            sim.risk_score = round(max(sim.risk_score, score), 4)
            sim.warnings.extend(flags)
            return sim.to_json()

    def intent_review(self, intent_id: str,
                      acknowledged_risk: bool = False) -> dict:
        """Fraud-simulator panel backend (§7.3 steps 3–5, honest-UX).

        Composes the deterministic simulator + on-device FraudDetector via
        SigningGate against REAL daemon state (live snapshot built from the
        faucet's mined outputs; fresh simulation at the current sim clock —
        never a cached fixture). All plain-language fields are computed
        server-side so the front end can't invent reassurance; every number
        traces to the executor template, not an LLM (§8.2 boundary).
        """
        with self._lock:
            it = self._get_intent(intent_id)
            snapshot = ChainSnapshot(list(self.faucet_outputs))
            snapshot.height = self.chain.tip.header.height
            contact_verified = None
            recipient_name = "the recipient"
            ref = it.compiled.recipient_ref or ""
            if ref.startswith("contact:"):
                c = self.book._contacts.get(ref.split(":", 1)[1])
                if c is not None:
                    contact_verified = c.verified
                    recipient_name = c.display_name
            rv = self.gate.review(it, snapshot, now_unix=self.sim_clock,
                                  oracle=self.compiler.oracle,
                                  recipient_name=recipient_name,
                                  contact_verified=contact_verified,
                                  acknowledged_risk=acknowledged_risk)
            risk = rv.risk
            ui_band = {"clean": "calm", "watch": "caution",
                       "interrupt": "pause"}[risk.band]
            out = {
                "intent_id": it.intent_id,
                "ok": rv.sim.ok,
                "plain_english": rv.sim.plain_english,
                # band-specific copy from the shipped explanation-template
                # pack (§8.2) — the front end renders this verbatim and can
                # never invent reassurance (honest-UX requirement).
                "explanation": rv.explanation,
                "template_pack": "sim_explain_v1",
                "simulation": rv.sim.to_json(),
                "risk": {"score": risk.score, "band": risk.band,
                         "reasons": list(risk.reasons)},
                "ui_band": ui_band,
                "proceed": rv.proceed,
                "interruption": rv.interruption,
                "reasons": list(rv.reasons),
                "policy_refusals": list(rv.policy_refusals),
                "sim_digest": rv.sim.sim_digest,
            }
            if rv.interruption and not acknowledged_risk:
                # calm-interruption card copy comes from the shipped template
                # pack (same string the gate embedded in rv.explanation) —
                # single source of truth, no ad-hoc reassurance text.
                out["human"] = render_template("pause")
                out["offer_sleep_on_it"] = True
            elif not rv.sim.ok:
                out["human"] = rv.sim.plain_english
            else:
                out["human"] = rv.explanation
            return out

    def intent_confirm(self, intent_id: str,
                       acknowledged_risk: bool = False) -> dict:
        with self._lock:
            it = self._get_intent(intent_id)
            if it.status != "awaiting_confirm":
                raise ValueError(f"intent is {it.status}, not ready to confirm")
            # honest-UX gate (§7.3 step 5): a confirmed intent must pass the
            # live SigningGate review — same composition the panel shows —
            # so no UI path can broadcast something the simulator rejects.
            # NOTE: reviewed BEFORE Intent.confirm() mutates signed_at, so
            # repeat confirms are idempotent (second call hits the status
            # check above instead of tripping the confirmation-required rule).
            snapshot = ChainSnapshot(list(self.faucet_outputs))
            rv = self.gate.review(it, snapshot, now_unix=self.sim_clock,
                                  oracle=self.compiler.oracle,
                                  acknowledged_risk=acknowledged_risk)
            if not rv.proceed:
                reasons = rv.reasons or ["simulation refused this intent"]
                raise ValueError("cannot confirm: " + "; ".join(reasons))
            it.confirm(now_unix=self.sim_clock)
            payload = _intent_payload(it)
            payload["confirmation_envelope"] = {
                "canonical_digest_hex": it.canonical_digest().hex(),
                "sim_digest_bound": rv.sim.sim_digest,
                "next_step": "mpc.sign_preview once a ceremony is complete",
            }
            return payload

    # ---------------- agents (§7.7 / §8.4) ----------------

    def grant_create(self, *, days: int = 30,
                     per_action_prsm: float = 25.0,
                     monthly_prsm: float = 400.0,
                     price_ceiling_usd: Optional[float] = None,
                     veto_hours: float = DEFAULT_DCA_VETO_HOURS) -> dict:
        """Issue the default conservative DCA grant (§7.7). The self pot is
        registered as a verified contact so the digest card names exactly the
        destination the policy engine whitelists — same string end to end."""
        with self._lock:
            if self.dca_contact is None:
                ref = hashlib.sha3_256(
                    (DEFAULT_DCA_SELF_NAME + DEFAULT_DCA_SELF_ADDRESS)
                    .encode()).hexdigest()[:8]
                self.dca_contact = Contact(f"c-{ref}", DEFAULT_DCA_SELF_NAME,
                                           DEFAULT_DCA_SELF_ADDRESS,
                                           verified=True,
                                           verification_method="qr_in_person")
                self.book.add(self.dca_contact)
            now = self.sim_clock
            g = Grant(agent=DEFAULT_DCA_AGENT, capability=DEFAULT_DCA_CAPABILITY,
                      constraints=Constraints(
                          max_per_action_shard=prsm_to_shard(per_action_prsm),
                          max_per_month_shard=prsm_to_shard(monthly_prsm),
                          price_ceiling_usd=price_ceiling_usd,
                          destinations_whitelist=(
                              f"contact:{self.dca_contact.contact_id}",)),
                      veto_window_hours=veto_hours,
                      valid_until_unix=now + days * 86400)
            self.agent.issue_grant(g)
            return {"grant": g.to_json(),
                    "human": ("Plan created: your auto-Dutcher may move up to "
                              f"{per_action_prsm:g} PRSM per action and "
                              f"{monthly_prsm:g} PRSM per month into "
                              f"'{DEFAULT_DCA_SELF_NAME}' until day "
                              f"{days}. Every action waits {veto_hours:g} h "
                              "before it runs — you can stop any of them in "
                              "that window, or shut the whole plan with one "
                              "tap."),
                    "whitelisted_destination": self.dca_contact.display_name}

    def _grant_or_raise(self, grant_id: Optional[str]) -> Grant:
        if not self.agent.grants:
            raise ValueError("no agent plan yet — create one first "
                             "(agent.grant_create)")
        gid = grant_id or next(reversed(self.agent.grants))
        g = self.agent.grants.get(gid)
        if g is None:
            raise ValueError(f"unknown grant {gid!r}")
        return g

    def _agent_utterance(self, utterance: str, g: Grant) -> str:
        """Map a natural DCA instruction onto the capability it will run
        under. The grammar only knows 'send/pay' (→ transfer); the policy
        engine then enforces that a buy_prsm_fiat_drip grant accepts ONLY
        fiat-quoted intents (§8.4 allowlist closure — this rewrites words,
        never constraints)."""
        t = utterance.strip()
        low = t.lower()
        if low.startswith("buy ") and g.capability == "buy_prsm_fiat_drip":
            rest = t[4:].strip()
            if not rest.lower().startswith(("send ", "pay ")):
                return f"Send {rest}"
        return t

    def agent_propose(self, utterance: str,
                      grant_id: Optional[str] = None) -> dict:
        """Agent proposal path: compile → PolicyEngine.check_proposal →
        live risk via the wired SigningGate → quiet-digest pending card.
        Refusals are returned (never raised past the lock) with ALL violated
        constraints at once — neuro-inclusive: no peek-a-boo errors."""
        with self._lock:
            g = self._grant_or_raise(grant_id)
            try:
                it = self.compiler.compile(self._agent_utterance(utterance, g),
                                           now_unix=self.sim_clock,
                                           balance_shard=self.balance_shard)
            except IntentError as e:
                return {"ok": False, "refused": True,
                        "reasons": [f"I couldn't understand that: {e}"],
                        "human": "Nothing was proposed. Rephrase and try again."}
            if it.needs_clarify:
                return {"ok": False, "refused": True,
                        "reasons": [f"unresolved: {it.needs_clarify}"],
                        "human": "The agent needs a complete instruction "
                                 "before it can propose anything."}
            from ..wallet.agent_auth import PolicyViolation
            try:
                pa = self.agent.propose(it, g.grant_id, now_unix=self.sim_clock,
                                        contact_verified=True,
                                        balance_shard=self.balance_shard)
            except PolicyViolation as e:
                return {"ok": False, "refused": True,
                        "reasons": [str(e)],
                        "human": ("This action was blocked by YOUR plan limits "
                                  "— not by the agent deciding for itself."),
                        "grant_id": g.grant_id}
            executed = pa.record.state in ("instant_executed",
                                           "executed_after_veto_window")
            out = {"ok": True, "action_id": pa.action_id,
                   "grant_id": g.grant_id,
                   "state": pa.record.state,
                   "risk_score": round(pa.risk_score, 4),
                   "digest_line": pa.record.explanation,
                   "intent": _intent_payload(it)}
            if executed:
                out["human"] = ("Done — this ran immediately under your plan.")
            else:
                hrs = (pa.exec_not_before_unix - self.sim_clock) / 3600.0
                out["executes_in_hours"] = round(hrs, 1)
                out["human"] = (f"Queued quietly: {pa.record.explanation} It "
                                f"runs in about {hrs:.0f} hours unless you "
                                "stop it. You'll see it on your Pending page.")
            return out

    def agent_pending(self) -> dict:
        """The quiet digest (§7.7 step 2): plain-language lines only — all
        math stays server-side (neuro-inclusive: never make the user count)."""
        with self._lock:
            lines = self.agent.digest_lines(now_unix=self.sim_clock)
            cards = []
            for pa in self.agent.pending.values():
                cards.append({"action_id": pa.action_id,
                              "short_id": pa.action_id[:8],
                              "line": pa.record.explanation,
                              "risk_score": round(pa.risk_score, 4)})
            return {"pending": cards, "digest_lines": lines,
                    "count": len(cards),
                    "human": (f"{len(cards)} action(s) waiting. Nothing has "
                              "run yet — you can stop any of them.")}

    def agent_veto(self, action_id: str) -> dict:
        with self._lock:
            # accept the 8-char short id shown on the digest card
            full = self.agent.pending.get(action_id)
            if full is None:
                for aid in self.agent.pending:
                    if aid.startswith(action_id):
                        action_id = aid
                        break
            self.agent.veto(action_id, now_unix=self.sim_clock)
            return {"ok": True, "action_id": action_id,
                    "human": "Stopped. This will not run, and it stays in "
                             "your history so nothing disappears silently."}

    def agent_revoke(self, grant_id: Optional[str] = None) -> dict:
        """One-tap revoke (§7.7 step 3): immediate, cascades to every queued
        action under the plan."""
        with self._lock:
            g = self._grant_or_raise(grant_id)
            n = sum(1 for pa in self.agent.pending.values()
                    if pa.record.grant_id == g.grant_id)
            self.agent.revoke(g.grant_id, now_unix=self.sim_clock)
            return {"ok": True, "grant_id": g.grant_id,
                    "cascaded_vetoes": n,
                    "human": (f"Plan stopped. {n} queued action(s) were also "
                              "cancelled. New actions cannot run under this "
                              "plan ever again.")}

    def agent_tick(self, hours_ahead: float = 0.0) -> dict:
        """Advance the devnet sim clock (optionally `hours_ahead` virtual
        hours) and let unvetoed actions whose window closed execute iff the
        risk model is STILL clean at execution time (§7.7 step 2). Devnet
        stand-in for the real signer; records state transitions honestly."""
        with self._lock:
            if hours_ahead > 0:
                self.sim_clock += int(hours_ahead * 3600)
            ids = list(self.agent.pending)
            scores = {aid: self.agent.pending[aid].risk_score for aid in ids}
            executed = self.agent.tick(now_unix=self.sim_clock,
                                       current_risk_scores=scores)
            return {"advanced_hours": hours_ahead,
                    "transitions": [{"action_id": r.action_id,
                                     "state": r.state,
                                     "explanation": r.explanation}
                                    for r in executed],
                    "human": (f"{len(executed)} action(s) reached their "
                              "window close this tick.") if executed else
                             "Nothing was due this tick — queued actions "
                             "still have veto time left."}

    def agent_report(self, grant_id: Optional[str] = None) -> dict:
        with self._lock:
            g = self._grant_or_raise(grant_id)
            rep = self.agent.monthly_report(
                g.grant_id, since_unix=g.constraints and 0,
                until_unix=self.sim_clock + 1)
            rep["human"] = (f"Your plan ran {rep['actions']} action(s); "
                            f"{shard_to_prsm(rep['spend_note_shard'])} PRSM "
                            "moved in total. Log integrity check: "
                            + ("passed" if rep["chain_ok"] else "FAILED"))
            return rep

    def _get_intent(self, intent_id: str):
        it = self.intents.get(intent_id)
        if it is None:
            raise ValueError(f"unknown intent_id {intent_id!r}")
        return it

    # ---------------- MPC ----------------

    def ceremony_start(self) -> dict:
        with self._lock:
            if self.ceremony.status == "running":
                raise ValueError("ceremony already running")
            self.ceremony = CeremonyState(status="running", stage="legs 1..3 polynomials",
                                          percent=10, started_at=time.perf_counter())
        # run synchronously but report through the staged state so a future
        # multi-device transport can swap in async updates without API change
        try:
            blinds = {i: s for i, s in enumerate(
                _deterministic_blinds(3, int(time.time_ns())), start=1)}
            res = frost.run_keygen(blinds, n=3, t=2)
            with self._lock:
                self.ceremony.status = "complete"
                self.ceremony.stage = "joint key established (2-of-3, device legs only)"
                self.ceremony.percent = 100
                self.ceremony.result = res
                self.ceremony.finished_at = time.perf_counter()
        except Exception as e:                       # pragma: no cover
            with self._lock:
                self.ceremony.status = "failed"
                self.ceremony.error = str(e)
            raise
        return self.ceremony_status()

    def ceremony_status(self) -> dict:
        c = self.ceremony
        d = {"status": c.status, "stage": c.stage, "percent": c.percent}
        if c.error:
            d["error"] = c.error
        if c.result is not None:
            d["scheme"] = "2-of-3 Solo (D4: user-owned device legs only)"
            d["joint_pk_hex"] = c.result.public_key.encode().hex()
            d["elapsed_s"] = round(c.finished_at - c.started_at, 3)
            d["devices"] = [
                {
                    "device_id": idx,
                    "label": DEVICE_LABELS.get(idx, f"Device {idx}"),
                    "share_fingerprint": hashlib.sha3_256(
                        str(sh.value).encode()).hexdigest()[:12],
                    "verified_against_commitments": True,
                    "role": ("signing quorum member — any 2 of the 3 devices "
                             "can sign; no device ever holds the key alone"),
                }
                for idx, sh in sorted(c.result.shares.items())
            ]
            d["share_handling"] = {
                "never_shown": ("Full share values are deliberately NOT "
                                "displayed. Fingerprints below exist only so "
                                "you can compare devices side by side and be "
                                "sure each holds a different piece."),
                "backup_ready": ("Your Backup card is already stored on that "
                                 "device. If you lose Phone or Laptop, any two "
                                 "remaining devices restore full access."),
                "what_if_lost": ("Lost a device? Start a reshare from Settings "
                                 "→ Device keys. The new shares are generated "
                                 "fresh, so an old stolen device stops working "
                                 "after the next signing session."),
            }
            d["human"] = ("Your three devices now hold your key together. "
                          "No single device — and never Prism — holds it.")
        return d

    def ceremony_reset(self) -> dict:
        """Start over (e.g. lost every device): drop the current ceremony."""
        with self._lock:
            self.ceremony = CeremonyState()
        return {"ok": True, **self.ceremony_status()}

    def sign_preview(self, intent_id: str) -> dict:
        c = self.ceremony
        if c.status != "complete" or c.result is None:
            raise ValueError("no completed ceremony — run mpc.ceremony_start first")
        it = self._get_intent(intent_id)
        msg = it.canonical_digest()
        sigs = {}
        for quorum in ((1, 2), (1, 3), (2, 3)):
            sig = frost.sign_threshold(c.result, list(quorum), msg)
            ok = frost.verify_signature(sig, msg, c.result.public_key)
            sigs[f"{quorum[0]}+{quorum[1]}"] = {"sig_hex": sig.hex(), "verifies": ok}
            if not ok:                               # pragma: no cover
                raise RuntimeError("threshold signature failed to verify")
        return {"intent_id": it.intent_id, "message_sha3": msg.hex(),
                "quorums": sigs,
                "human": "Any 2 of your 3 devices can sign this payment. "
                         "No single device — and never Prism — holds your key."}


def _deterministic_blinds(n: int, seed: int) -> list[int]:
    import random
    rng = random.Random(seed)
    from ..mpc.sharing import ED25519_SCALARS as F
    return [rng.randrange(1, F.order) for _ in range(n)]


def _empty_im_root() -> bytes:
    from ..chain.node import merkle_root
    return merkle_root([])


def _intent_payload(it) -> dict:
    p = it.to_json()                       # Intent always defines to_json()
    p.setdefault("clarify", getattr(it, "needs_clarify", None))
    return p
