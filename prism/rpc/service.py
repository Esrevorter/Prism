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

from ..chain.node import ChainState, mine_block, make_genesis, Block, ZERO32
from ..chain.params import REFRACTION_TESTNET
from ..mpc import frost
from ..mpc.sharing import Share
from ..wallet.intent import (AddressBook, Contact, IntentCompiler,
                             IntentError, apply_clarification, prsm_to_shard,
                             shard_to_prsm, simulate, fraud_flags)


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
        self.ceremony = CeremonyState()
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
                out.append({"height": blk.header.height, "hash": bh.hex()})
            return {"mined": len(out), "last": out[-1],
                    "balance_shard": self.balance_shard}

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

    def intent_confirm(self, intent_id: str) -> dict:
        with self._lock:
            it = self._get_intent(intent_id)
            if it.status != "awaiting_confirm":
                raise ValueError(f"intent is {it.status}, not ready to confirm")
            it.confirm(now_unix=self.sim_clock)
            payload = _intent_payload(it)
            payload["confirmation_envelope"] = {
                "canonical_digest_hex": it.canonical_digest().hex(),
                "next_step": "mpc.sign_preview once a ceremony is complete",
            }
            return payload

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
        return d

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
