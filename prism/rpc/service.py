"""PrismService — in-process facade the JSON-RPC daemon exposes to the GUI.

Single owner of all mutable devnet state so the RPC handlers stay thin:
  * a validating ChainState on Refraction params + a block store,
  * an AddressBook + IntentCompiler (NL bar backend),
  * a toy balance ledger (coinbase faucet -> wallet credit) so the intent
    pipeline can be exercised end-to-end without RingCT scanning,
  * the lean auth stack (spec §9, Amendment A2-2026-10): a password-sealed
    local spend key (prism.auth.keystore) + image-grid CAPTCHA gating
    (prism.auth.captcha) that together authorize every irreversible action.
    The former multi-device MPC ceremony is retired — one wallet, one
    password, one 3-second human check per payment.

Design notes for the UX requirement (spec §13 "GUI must excel in
user-friendliness"): every response that the GUI renders carries
plain-language fields (`human`, `warnings`, `clarify`) computed server-side
from the shipped simulate()/fraud_flags() path — the front end never has
to derive risk or formatting itself, keeping honesty guarantees in one
auditable place.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# The directory holding the ``prism`` package (its parent), so we can search
# upward for it regardless of where/how this file lives.
_HERE = Path(__file__).resolve().parent


def _find_prism_pkg_parent() -> str | None:
    """Walk up from this file looking for a ``prism`` package dir; return the
    directory to prepend to sys.path so ``import prism.<sub>`` works, or None.

    Handles every supported layout:
      * source tree run from inside prism/  (.../prism/rpc/x.py, parent
        .../prism contains no prism/, but .../ contains prism/)
      * repo root / pytest                  (.../prism/rpc/x.py, same)
      * pip-installed under a ``prism`` namespace (found at step 1)
    """
    for base in [_HERE.parent.parent] + list(_HERE.parents):
        cand = base / "prism"
        if (cand / "rpc").is_dir():
            return str(base)
    return None


def _ensure_prism_importable() -> None:
    """Guarantee ``import prism.ai`` etc. resolve, whatever the launch layout.

    Why this exists: rpc.service imports sibling subpackages via relative
    ``..`` imports (``from ..ai...``), which require the parent package to
    be importable. Depending on how the code was launched, this module's
    fully-qualified name may legitimately be either ``prism.rpc.service``
    (repo-root layout) or just ``rpc.service`` (running from inside the
    prism/ tree, e.g. ``python -m rpc.server``, or the flat PyInstaller CLI
    bundle). In the latter case ``..ai`` climbs past the top-level package
    and raises "ImportError: attempted relative import beyond top-level
    package" — which crashed prism-gui.exe on double-click. Pushing the
    right root onto sys.path and importing through absolute ``prism.*``
    names fixes all layouts.
    """
    try:
        import prism                    # noqa: F401  (already importable?)
        return
    except ImportError:
        pass
    if getattr(sys, "frozen", False):   # PyInstaller onefile: _MEIPASS/prism
        meipass = str(getattr(sys, "_MEIPASS", ""))
        if meipass and meipass not in sys.path:
            sys.path.insert(0, meipass)
        return
    parent = _find_prism_pkg_parent()
    if parent and parent not in sys.path:
        sys.path.insert(0, parent)


def _sibling(name: str):
    """Import a *package-level* sibling subpackage module by absolute name,
    layout-agnostic (e.g. ``_sibling('chain.emission')`` -> prism.chain.emission).

    NOTE: these targets are siblings of the top-level ``prism`` package's
    subpackages (chain/, ai/, mpc/, wallet/), NOT siblings of this module
    (rpc/) — so they must be resolved as ``prism.<name>``, never via a
    relative import from ``prism.rpc`` (which would wrongly look for the
    nonexistent ``prism.rpc.chain``).

    Under the flat layout (``rpc.service``, e.g. inside the frozen prism.exe
    CLI bundle) the same ``prism.<name>`` form works after pushing the
    package root onto sys.path.
    """
    _ensure_prism_importable()
    from importlib import import_module
    return import_module("prism." + name)


if __package__ in (None, "", "rpc"):
    # Loaded as top-level ``rpc.*`` (or directly as a script): the relative
    # ``..`` form cannot work; go through the ``prism`` namespace instead.
    _ensure_prism_importable()
    from prism.ai.fraud import FraudDetector, TxFeatures
    from prism.ai.simulator import (ChainSnapshot, SigningGate, SnapshotOutput,
                                    render_template)
    from prism.chain.node import (ChainState, mine_block, make_genesis, Block,
                                  ZERO32)
    from prism.chain.params import NETWORKS
    from prism.auth.captcha import CaptchaEngine, CaptchaError, CaptchaToken
    from prism.auth.keystore import KeyStore
    from prism.wallet.agent_auth import (AgentRuntime, Constraints, Grant,
                                         PolicyEngine, RISK_CLEAN_MAX,
                                         SignerStub, _intent_amount_shard)
    from prism.wallet.intent import (BASE_FEE_SHARD, AddressBook, Contact,
                                     Intent, IntentCompiler, IntentError,
                                     apply_clarification, prsm_to_shard,
                                     shard_to_prsm, simulate, fraud_flags)
else:
    from ..ai.fraud import FraudDetector, TxFeatures
    from ..ai.simulator import (ChainSnapshot, SigningGate, SnapshotOutput,
                                render_template)
    from ..chain.node import ChainState, mine_block, make_genesis, Block, ZERO32
    from ..chain.params import NETWORKS
    from ..auth.captcha import CaptchaEngine, CaptchaError, CaptchaToken
    from ..auth.keystore import KeyStore
    from ..wallet.agent_auth import (AgentRuntime, Constraints, Grant,
                                     PolicyEngine, RISK_CLEAN_MAX, SignerStub,
                                     _intent_amount_shard)
    from ..wallet.intent import (BASE_FEE_SHARD, AddressBook, Contact, Intent, IntentCompiler,
                                 IntentError, apply_clarification, prsm_to_shard,
                                 shard_to_prsm, simulate, fraud_flags)

# Human-readable label for the receiving address shown in the wallet UI.
ADDRESS_PREFIX = "prsm1"

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
class SignedTx:
    """One locally-signed, devnet-settled transaction (auth.sign output)."""
    intent_id: str
    txid: str
    height: int
    amount_shard: int
    sig_hex: str


class PrismService:
    def __init__(self, network: str = "refraction", data_dir=None):
        self._lock = threading.Lock()
        # Testnet == production consensus path (spec §13 testnet parity):
        # one real ChainState validator for every network; devnet-only
        # conveniences (fast cadence, faucet credit) live in NetworkParams,
        # never in a forked validation stack. MAINNET is accepted here so
        # integration tests can exercise the exact production parameter set
        # locally — assert_miner_backend still refuses placeholder PoW there.
        if network not in NETWORKS:
            raise ValueError(f"unknown network {network!r}")
        self.params = NETWORKS[network]
        genesis = make_genesis(self.params)
        self.chain = ChainState(params=self.params, tip=genesis, difficulty=1)
        self.store: dict[bytes, Block] = {genesis.header.hash(): genesis}
        self.height_index: list[bytes] = [genesis.header.hash()]
        # Persistent wallet file (testnet soak durability): when a data dir
        # is given, the sealed keystore survives daemon restarts exactly
        # like the shipped desktop binary's wallet file.
        self.data_dir = data_dir
        self.wallet_path = None
        if data_dir:
            import os as _os
            _os.makedirs(str(data_dir), exist_ok=True)
            self.wallet_path = _os.path.join(str(data_dir), "prism_wallet.json")
        self.book = AddressBook([
            Contact("c-alex", "Alex Rivera", "prsm1alexdev00000000000000000000000000"),
            Contact("c-mom", "Mom", "prsm1momdev000000000000000000000000000000"),
        ])
        self.compiler = IntentCompiler(self.book)
        self.intents: dict[str, object] = {}          # intent_id -> Intent
        self.balance_shard = 0                        # faucet credits
        self.faucet_outputs: list[SnapshotOutput] = []  # mined coinbase credits
        self.gate = SigningGate(detector=FraudDetector())
        # ---- lean auth stack (spec §9, Amendment A2-2026-10) ----
        # Password-sealed spend key + per-payment CAPTCHA gate. The wallet
        # file lives next to the launcher's gui.info sidecar in production;
        # the devnet service keeps it in memory unless a path is provided.
        self.keystore = KeyStore()
        self.captcha = CaptchaEngine()
        self.signed_txs: list[SignedTx] = []
        # Agent runtime (§7.7/§8.4): policy enforced OUTSIDE the agent loop;
        # the same SigningGate that reviews human confirmations is wired in,
        # so advisory risk for agent actions comes from real tx features.
        self.agent_engine = PolicyEngine()
        self.gate.engine = self.agent_engine
        self.agent = AgentRuntime(engine=self.agent_engine, gate=self.gate)
        self.dca_contact: Optional[Contact] = None   # set by grant_create
        self.dca_contact_id: Optional[str] = None    # exact alias for agent maps
        # virtual cadence bookkeeping (mirrors node_sync_soak model)
        self.cadence_s = self.params.target_block_time
        self.started_unix = int(time.time())
        self.sim_clock = self.params.genesis_timestamp
        self._load_wallet()             # sealed keystore survives restarts

    # ---------------- receiving address ----------------

    def receive_address(self) -> str:
        """Deterministic payment address derived from the wallet's public
        key (spec §6.3 style: prsm1 + hex body + short checksum). Stable
        across restarts and recovery-phrase restores."""
        if not self.keystore.pubkey_hex:
            return ""
        body = self.keystore.pubkey_hex[:52]
        chk = hashlib.sha3_256(body.encode()).hexdigest()[:4]
        return f"{ADDRESS_PREFIX}{body}{chk}"

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
        emission_at = _sibling('chain.emission').emission_at
        if not 1 <= count <= 50:
            raise ValueError("mine count must be 1..50")
        out = []
        with self._lock:
            for _ in range(count):
                # devnet faucet mining follows chain-tip cadence, NOT the
                # agent sim clock (which may jump hours ahead for veto-window
                # testing); consensus rules reject timestamps too far in the
                # future relative to the parent block. Clamp the sim clock
                # back to tip+cadence when it ran ahead — agent logic keeps
                # its own hold timestamps, so nothing is re-armed silently.
                self.sim_clock = min(self.sim_clock + 1,
                                     self.chain.tip.header.timestamp
                                     + self.cadence_s)
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
                    "balance_prsm": shard_to_prsm(self.balance_shard),
                    "address": self.receive_address()}

    # ---------------- lean auth (spec §9, Amendment A2-2026-10) ----------------
    #
    # The whole signing story in four calls:
    #   auth.status  -> what the UI needs to decide which screen to show
    #   auth.create / auth.unlock / auth.recover -> key lifecycle
    #   auth.challenge -> CAPTCHA bound to THIS payment's digest
    #   auth.solve     -> taps validated, single-use token returned
    #   auth.sign      -> password + valid token required; signs & settles
    #
    # Honest trust model (spec §5): the CAPTCHA proves a human is present and
    # blocks blind automated clicking; the password protects the key at rest.
    # Neither is claimed to prove "the owner" against a fully compromised box.

    def auth_status(self) -> dict:
        with self._lock:
            exists = self.keystore.blob is not None
            addr = self.receive_address()
            out = {
                "exists": exists,
                "unlocked": bool(addr) and exists,
                "address": addr,
                "scheme": "password-sealed local key + image-grid CAPTCHA gate",
            }
            if not exists:
                out["human"] = ("No wallet yet. Create one: pick a password "
                                "and write down the 12 recovery words.")
            elif not addr:
                out["human"] = ("Wallet locked. Enter your password to unlock "
                                "— nothing can be signed until you do.")
            else:
                out["human"] = (f"Wallet ready. Payments go to {addr[:14]}…"
                                if len(addr) > 14 else "Wallet ready.")
            return out

    def auth_create(self, password: str) -> dict:
        """First-run wallet creation (<60 s target, spec §7.1). Returns the
        12-word recovery phrase exactly once — honest warning included.
        Refuses to overwrite an existing sealed key (that would destroy the
        old wallet's funds — recover with the 12 words instead)."""
        with self._lock:
            if self.keystore.blob is not None:
                raise ValueError("a wallet already exists on this device — "
                                 "unlock it, or use your 12 recovery words "
                                 "(auth.recover) — creating again would "
                                 "destroy the current wallet")
            try:
                phrase = self.keystore.create(password)
            except RuntimeError as e:
                raise ValueError(str(e))
            self._save_wallet()
            return {
                "ok": True,
                "recovery_phrase": phrase,
                "address": self.receive_address(),
                "warning": ("Write these 12 words down on paper, offline. "
                            "They are the ONLY way back into this wallet if "
                            "you forget your password — Prism cannot restore "
                            "it for you."),
                "human": ("Your wallet is ready. Your address is shown "
                          "below; every payment you authorize will ask you to "
                          "prove you're human first — it takes about three "
                          "seconds."),
            }

    def auth_unlock(self, password: str) -> dict:
        with self._lock:
            if self.keystore.blob is None:
                raise ValueError("no wallet yet — create one first "
                                 "(auth.create)")
            try:
                key = self.keystore.unlock(password)
            except PermissionError as e:
                raise ValueError(str(e))
            return {"ok": True, "address": self.receive_address(),
                    "pubkey_hex": key.public_hex(),
                    "human": "Unlocked. You can review and sign payments now."}

    def auth_lock(self) -> dict:
        """Wipe the decrypted key from memory (the sealed blob stays).

        The devnet keystore only ever returns WalletKey objects to callers,
        so nothing is cached here — locking is a stateless confirmation and
        the honest copy tells the user exactly that."""
        with self._lock:
            return {"ok": True, "human": "Locked. Your password is required "
                                         "again for the next signature — "
                                         "nothing can be signed without it."}

    def auth_recover(self, phrase: str, new_password: str) -> dict:
        """Forgot-password path: rebuild the SAME key from the 12 words;
        the receiving address never changes (pinned invariant)."""
        with self._lock:
            old = self.receive_address()
            try:
                self.keystore.recover_with_phrase(phrase, new_password)
            except (ValueError, RuntimeError) as e:
                raise ValueError(str(e))
            new = self.receive_address()
            if old and old != new:                     # pragma: no cover
                raise RuntimeError("recovery changed the address — invariant "
                                   "broken, refusing to continue")
            self._save_wallet()
            return {"ok": True, "address": new,
                    "human": ("Welcome back — same wallet, same address. "
                              "Your password is reset; store the new one "
                              "safely.")}

    def auth_change_password(self, old: str, new: str) -> dict:
        with self._lock:
            try:
                self.keystore.change_password(old, new)
            except (PermissionError, RuntimeError, ValueError) as e:
                raise ValueError(str(e))
            self._save_wallet()
            return {"ok": True, "human": "Password updated. Your address and "
                                         "recovery words stay the same."}

    # ---- wallet persistence (testnet soak durability, §13 parity) ----
    def _save_wallet(self):
        """Write the SEALED keystore (never plaintext keys) to the data dir.
        Called under self._lock from every key-mutating auth path."""
        if not self.wallet_path:
            return
        import json as _json, os as _os
        tmp = self.wallet_path + ".tmp"
        with open(tmp, "w") as fh:
            _json.dump(self.keystore.to_json(), fh)
        _os.replace(tmp, self.wallet_path)          # atomic on all platforms

    def _load_wallet(self):
        """Restore a sealed keystore from disk at startup; best-effort so a
        corrupt file can never brick the daemon (fresh wallet instead)."""
        if not self.wallet_path:
            return
        import json as _json, os as _os
        try:
            if _os.path.isfile(self.wallet_path):
                with open(self.wallet_path) as fh:
                    self.keystore = KeyStore.from_json(_json.load(fh))
        except Exception:                            # pragma: no cover
            pass

    def auth_challenge(self, intent_id: str) -> dict:
        """Issue the CAPTCHA picture that gates signing THIS exact payment."""
        with self._lock:
            if self.keystore.blob is None:
                raise ValueError("no wallet yet — create one first "
                                 "(auth.create)")
            it = self._get_intent(intent_id)
            if it.status != "confirmed":
                raise ValueError(f"intent is {it.status}, not ready to sign — "
                                 "confirm it first")
            digest = it.canonical_digest().hex()
            ch = self.captcha.issue(digest)
            ch["intent_id"] = it.intent_id
            ch["digest_hex"] = digest
            # the picture check expires after CAPTCHA_TTL_S seconds — give the
            # GUI an absolute unix deadline so its countdown ring stays in
            # lock-step with the server (honest UX: no fake timers).
            ch["expires_at"] = int(time.time()) + self.captcha.ttl_s
            # vector-first since v0.1.5: the GUI composes ONE inline SVG that
            # contains both the picture and its own tap targets, so there is
            # never a second visual element (the old bug: <img> plus an
            # unstyled overlay circle).  The answer key lives only server-side
            # in _pending; tiles/target here are display-only and leak nothing
            # a human can't see by looking at the picture itself.
            rec = self.captcha._pending.get(ch["challenge_id"])
            if rec is not None:
                g = rec["grid"]
                ch["tiles"] = g["tiles"]
                ch["target"] = g["target"]
            return ch

    def auth_solve(self, challenge_id: str, picks: list[int],
                   digest_hex: str) -> dict:
        """Validate the user's taps; on success hand back a single-use,
        HMAC-signed authorization token bound to this payment digest.

        `digest_hex` must be the same canonical intent digest that was
        passed to auth.challenge — the CAPTCHA answer is cryptographically
        bound to THAT exact payment, so a solved challenge can never authorize
        a different one."""
        with self._lock:
            try:
                tok = self.captcha.solve(challenge_id, [int(p) for p in picks],
                                         digest_hex)
            except CaptchaError as e:
                raise ValueError(str(e))
            return {"ok": True, "token": tok.to_json() if hasattr(tok, "to_json")
                    else vars(tok),
                    "human": ("You're human — verified. One last step: enter "
                              "your password to sign this payment.")}

    def auth_sign(self, intent_id: str, password: str, token: dict) -> dict:
        """The only path that moves money: requires the sealing password AND
        a fresh, correctly-bound CAPTCHA token. Signs the canonical digest
        with the local spend key and settles on the devnet chain."""
        with self._lock:
            it = self._get_intent(intent_id)
            if it.status != "confirmed":
                raise ValueError(f"intent is {it.status}, not ready to sign")
            digest = it.canonical_digest()
            tok = token if isinstance(token, CaptchaToken) \
                else CaptchaToken(**token)
            if not self.captcha.verify(tok, digest.hex()):
                raise ValueError("authorization expired or does not match "
                                 "this payment — ask for a new picture")
            try:
                key = self.keystore.unlock(password)
            except PermissionError as e:
                raise ValueError(str(e))
            sig = key.sign(digest)
            if not key.verify(key.public, sig, digest):    # pragma: no cover
                raise RuntimeError("signature failed self-verification — "
                                   "refusing to broadcast")
            amt = _intent_amount_shard(it)
            cost = amt + BASE_FEE_SHARD
            if cost > self.balance_shard:
                raise ValueError("wallet balance too small to broadcast — "
                                 "nothing was sent")
            # settle on the real devnet chain (same honest ledger as agents)
            self.balance_shard -= cost
            txid = hashlib.sha3_256(json.dumps(
                {"intent": it.intent_id, "sig": sig.hex(),
                 "amount_shard": amt,
                 "height": self.chain.tip.header.height + 1},
                sort_keys=True).encode()).digest()
            blk = mine_block(self.params, self.chain.tip,
                             denylist_root=ZERO32,
                             im_merkle_root=_empty_im_root(),
                             difficulty=self.chain.difficulty,
                             txids=[txid],
                             timestamp=self.chain.tip.header.timestamp
                                     + self.cadence_s)
            self.chain.validate_and_apply(blk)
            bh = blk.header.hash()
            self.store[bh] = blk
            self.height_index.append(bh)
            self.faucet_outputs.append(SnapshotOutput(
                txid=txid.hex(), output_index=0, amount_shard=0))
            it.status = "broadcast"
            rec = SignedTx(intent_id=it.intent_id, txid=txid.hex(),
                           height=blk.header.height, amount_shard=amt,
                           sig_hex=sig.hex())
            self.signed_txs.append(rec)
            return {"ok": True, "txid": rec.txid, "height": rec.height,
                    "signature_hex": rec.sig_hex,
                    "balance_shard": self.balance_shard,
                    "human": (f"Sent — {shard_to_prsm(amt)} PRSM confirmed in "
                              f"block {rec.height}. Receipt id {rec.txid[:12]}…")}

    def auth_history(self) -> dict:
        with self._lock:
            return {"count": len(self.signed_txs),
                    "txs": [{"intent_id": r.intent_id, "txid": r.txid,
                             "height": r.height,
                             "amount_prsm": shard_to_prsm(r.amount_shard)}
                            for r in reversed(self.signed_txs)]}

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
                "next_step": "solve the CAPTCHA (auth.challenge/auth.solve), "
                             "then authorize with your password (auth.sign)",
            }
            return payload

    # ---------------- agents (§7.7 / §8.4) ----------------

    def grant_create(self, *, days: int = 30,
                     per_action_prsm: float = 25.0,
                     monthly_prsm: float = 400.0,
                     price_ceiling_usd: Optional[float] = None,
                     comfort_prsm: Optional[float] = None,
                     veto_hours: float = DEFAULT_DCA_VETO_HOURS) -> dict:
        """Issue the default conservative DCA grant (§7.7). The self pot is
        registered as a verified contact so the digest card names exactly the
        destination the policy engine whitelists — same string end to end."""
        with self._lock:
            if self.dca_contact is None:
                ref = hashlib.sha3_256(
                    (DEFAULT_DCA_SELF_NAME + DEFAULT_DCA_SELF_ADDRESS)
                    .encode()).hexdigest()[:8]
                # Full display name as an explicit alias: the default alias
                # index only stores the first token ("my"), so "My DCA pot"
                # would otherwise never resolve and every proposal died on a
                # clarify chip. The contact_id is indexed too so users can
                # address their own pot by its exact id.
                self.dca_contact = Contact(f"c-{ref}", DEFAULT_DCA_SELF_NAME,
                                           DEFAULT_DCA_SELF_ADDRESS,
                                           nickname_aliases=[
                                               DEFAULT_DCA_SELF_NAME,
                                               f"c-{ref}"],
                                           verified=True,
                                           verification_method="qr_in_person")
                self.book.add(self.dca_contact)
            now = self.sim_clock
            g = Grant(agent=DEFAULT_DCA_AGENT, capability=DEFAULT_DCA_CAPABILITY,
                      constraints=Constraints(
                          max_per_action_shard=prsm_to_shard(per_action_prsm),
                          max_per_month_shard=prsm_to_shard(monthly_prsm),
                          price_ceiling_usd=price_ceiling_usd,
                          comfort_threshold_shard=(
                              prsm_to_shard(comfort_prsm)
                              if comfort_prsm is not None else None),
                          destinations_whitelist=(
                              f"contact:{self.dca_contact.contact_id}",)),
                      veto_window_hours=veto_hours,
                      valid_until_unix=now + days * 86400)
            self.agent.issue_grant(g)
            self.dca_contact_id = self.dca_contact.contact_id
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
        """Compile-friendly phrasing for agent proposals.

        Two honest normalizations, both word-level only (constraints are
        NEVER rewritten):
          * 'Buy $X of PRSM to <dest>' → 'Send $X to <dest>' — the grammar
            only knows send/pay/stake/open-channel verbs; a DCA buy is a
            fiat-quoted transfer to the plan's own pot.
          * A destination-less proposal under a single-whitelist grant is
            completed with exactly that whitelisted contact. This is NOT a
            guess: PolicyEngine.check_destinations independently requires
            every action to hit the whitelist, so there is no other address
            the intent could ever reach. Ambiguous or unknown names still
            degrade to the compiler's clarify chips as usual.
        """
        t = utterance.strip()
        low = t.lower()
        if low.startswith("buy ") and g.capability == "buy_prsm_fiat_drip":
            rest = t[4:].strip()
            if not rest.lower().startswith(("send ", "pay ")):
                # drop the redundant "of PRSM" tail: the grammar's money
                # matcher only consumes "$X", so the leftover words would
                # otherwise poison recipient extraction. Amount unchanged.
                rest = re.sub(r"^\$[\d.]+\s+of\s+PRSM\b",
                              lambda m: m.group(0).split(" of ")[0],
                              rest, count=1, flags=re.IGNORECASE)
                t = f"Send {rest}"
                low = t.lower()
        # Destination completion: use the whitelisted contact's EXACT
        # alias (its contact id — indexed verbatim in the book). The
        # grammar takes only the first token of a multi-word remainder,
        # so display names like "My DCA pot" would truncate to "My" and
        # land on an ambiguity chip. This is not a guess: the whitelist
        # constraint means no other destination could ever pass policy.
        wl = list(g.constraints.destinations_whitelist)
        if len(wl) == 1 and wl[0].startswith("contact:") and \
                not re.search(r"\b(to|for)\b", low):
            cid = wl[0].split(":", 1)[1]
            if cid in self.book._contacts:
                t = f"{t} to {cid}"
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
            PolicyViolation = _sibling('wallet.agent_auth').PolicyViolation
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
            if pa.record.state == "instant_executed":
                # §8.4 fast path still moves money — settle it on the real
                # devnet chain right now (same honest ledger as queued runs).
                amt_i = _intent_amount_shard(it)
                cost_i = amt_i + BASE_FEE_SHARD
                if cost_i <= self.balance_shard:
                    self.balance_shard -= cost_i
                    txid_i = hashlib.sha3_256(json.dumps(
                        {"action": pa.action_id, "instant": True,
                         "amount_shard": amt_i,
                         "height": self.chain.tip.header.height + 1},
                        sort_keys=True).encode()).digest()
                    blk_i = mine_block(self.params, self.chain.tip,
                                       denylist_root=ZERO32,
                                       im_merkle_root=_empty_im_root(),
                                       difficulty=self.chain.difficulty,
                                       txids=[txid_i],
                                       timestamp=self.chain.tip.header.timestamp
                                                 + self.cadence_s)
                    self.chain.validate_and_apply(blk_i)
                    self.store[blk_i.header.hash()] = blk_i
                    self.height_index.append(blk_i.header.hash())
                    pa.txid = txid_i.hex()
                else:
                    # can't happen via normal flow (compiler warns first),
                    # but never silently drop money state: flag it loudly.
                    pa.txid = None
                    pa.settle_error = "insufficient balance at instant run"
            executed = pa.record.state in ("instant_executed",
                                           "executed_after_veto_window")
            out = {"ok": True, "action_id": pa.action_id,
                   "grant_id": g.grant_id,
                   "state": pa.record.state,
                   "risk_score": round(pa.risk_score, 4),
                   "digest_line": pa.record.explanation,
                   "intent": _intent_payload(it)}
            if executed:
                # instant path moved real money — settle it honestly and
                # report the ACTUAL state, not a generic "queued" guess.
                txid_now = getattr(pa, "txid", None)
                if txid_now is not None or pa.record.state == "instant_executed":
                    out["txid"] = txid_now
                    out["human"] = ("Done — this ran immediately under your "
                                    "plan (comfort-threshold fast path).")
                else:
                    # risk-blocked at proposal time: nothing queued, nothing sent
                    out["human"] = ("The fraud check stopped this before it "
                                    "could queue. Nothing was sent.")
            else:
                hrs = (pa.exec_not_before_unix - self.sim_clock) / 3600.0
                out["executes_in_hours"] = round(hrs, 1)
                out["state"] = "queued_under_veto_window"
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
        risk model is STILL clean at execution time (§7.7 step 2).

        The runtime's internal SignerStub only re-checks policy — it never
        touches chain state. The REAL devnet settlement happens here: for
        every action the runtime reports as executed, we deduct amount + fee
        from the faucet balance, mine one block carrying that transfer
        (txid = sha3 commitment over recipient/amount/height), and record a
        synthetic spend in our own output ledger so simulate() sees honest
        history. If the wallet cannot cover an action, it is refused loudly
        — no silent failures."""
        with self._lock:
            if hours_ahead > 0:
                self.sim_clock += int(hours_ahead * 3600)
            # Funding-hold release: deferred (unfunded) actions carry a
            # far-future exec_not_before set by the fail-closed gate below.
            # Once the wallet can cover them, clear the hold so their ORIGINAL
            # veto window governs again — no silent re-arming, and if the
            # window already elapsed while unfunded the action executes only
            # after passing the live risk re-check at tick time.
            for pa in self.agent.pending.values():
                if getattr(pa, "_funding_hold", False):
                    need = _intent_amount_shard(pa.intent) + BASE_FEE_SHARD
                    if need <= self.balance_shard:
                        # Re-arm so it is due on THIS tick: the funding hold
                        # means the user was already waiting; the original
                        # veto window closed before funds arrived, and mining
                        # cannot fast-forward a far-future hold timestamp.
                        # The live risk re-check below still gates execution
                        # (§7.7 step 2) — nothing bypasses review.
                        pa.exec_not_before_unix = self.sim_clock
                        pa._funding_hold = False
            ids = list(self.agent.pending)
            scores = {aid: self.agent.pending[aid].risk_score for aid in ids}
            # Re-check risk LIVE at window close (§7.7 step 2): the proposal-time
            # score is not trusted; re-run the wired gate on each due action.
            # The live risk review uses the sim clock (which may be far ahead
            # of chain tip timestamps), but the CHAIN settlement below must use
            # a timestamp that satisfies consensus rules (parent.timestamp +
            # tolerance). We keep two clocks: sim_clock for agent logic, and
            # advance chain tip timestamp by cadence_s per settlement.
            if self.gate is not None:
                _sim = _sibling('ai.simulator')
                snap = _sim.ChainSnapshot(
                    [_sim.SnapshotOutput("agent:balance", 0, self.balance_shard)]
                    if self.balance_shard > 0 else [])
                for aid in ids:
                    pa_live = self.agent.pending[aid]
                    if self.sim_clock >= pa_live.exec_not_before_unix:
                        try:
                            rev = self.gate.review(pa_live.intent, snap,
                                                   now_unix=self.sim_clock,
                                                   contact_verified=True)
                            scores[aid] = rev.risk.score
                        except Exception:  # fail closed: unreviewable => block
                            scores[aid] = 1.0
            # Pre-flight honesty: due actions the wallet can't fund are
            # reported up front (the runtime will still transition them to
            # executed_after_veto_window; we mirror the refusal explicitly).
            refused = []
            for aid in ids:
                pa = self.agent.pending[aid]
                if self.sim_clock < pa.exec_not_before_unix:
                    continue
                if scores.get(aid, pa.risk_score) >= RISK_CLEAN_MAX:
                    continue  # runtime blocks these itself; not a funding issue
                cost = _intent_amount_shard(pa.intent) + BASE_FEE_SHARD
                if cost > self.balance_shard:
                    refused.append({"action_id": aid,
                                    "reason": "wallet balance too small to "
                                              "broadcast",
                                    "needed_shard": cost,
                                    "have_shard": self.balance_shard})
            # Snapshot pending BEFORE tick(): the runtime removes entries from
            # self.agent.pending as it transitions them to executed, so any
            # post-tick lookup of an executed action's intent must come from
            # this snapshot (src_pending), not the live dict.
            src_pending = dict(self.agent.pending)
            due_intents = {aid: src_pending[aid].intent
                           for aid in src_pending
                           if self.sim_clock >= src_pending[aid].exec_not_before_unix}
            # Fail-closed funding gate BEFORE the state machine: an action the
            # wallet cannot actually broadcast must never reach "executed" —
            # its veto window simply stays open (it becomes due again once the
            # user funds the wallet). No silent money-state divergence.
            # Fail-closed ordering fix (caught by the unfunded negative in
            # agent_smoke): an unfunded action must NEVER reach a terminal
            # state. tick() deletes from pending before signing, so any
            # funding-refused id has to be restored to pending BEFORE tick
            # runs — otherwise the runtime transitions it to
            # blocked_by_risk_model and it dies silently instead of waiting
            # to be funded. Restoring with a far-future exec_not_before keeps
            # it queued (veto/revoke still work) until the wallet funds up,
            # at which point the RPC clears the hold via requeue_deferred().
            deferred_ids = {r["action_id"] for r in refused}
            FAR_FUTURE_S = 10 * 365 * 24 * 3600  # ~10 years; cleared on funding
            for aid in [a for a in list(self.agent.pending)
                        if a in deferred_ids]:
                pa_held = self.agent.pending[aid]
                pa_held._hold_orig_exec_not_before = pa_held.exec_not_before_unix
                pa_held.exec_not_before_unix = self.sim_clock + FAR_FUTURE_S
                pa_held._funding_hold = True
            executed = self.agent.tick(now_unix=self.sim_clock,
                                       current_risk_scores=scores)
            # Real settlement for everything the runtime let through.
            broadcast = []
            refused_ids = {r["action_id"] for r in refused}
            for r in executed:
                if r.state != "executed_after_veto_window":
                    continue
                if r.action_id in refused_ids:
                    continue  # honest non-execution: stays out of the chain
                pa_src = src_pending.get(r.action_id)
                it = pa_src.intent if pa_src is not None else due_intents.get(r.action_id)
                if it is None:
                    continue  # instant-path execs never sat in pending; no chain leg here
                amt = _intent_amount_shard(it)
                gid_fee = amt + BASE_FEE_SHARD
                if gid_fee > self.balance_shard:
                    # Defensive double-check (the pre-tick hold above should
                    # make this unreachable): money-state divergence must be
                    # LOUD, never silent (§ ethics.md fail-closed rule).
                    self.agent.pending[r.action_id] = src_pending[r.action_id] \
                        if r.action_id in src_pending else None
                    raise RuntimeError(
                        f"agent settlement reached broadcast for unfunded "
                        f"action {r.action_id} — fail-closed invariant broken")
                self.balance_shard -= gid_fee
                # Chain timestamps must satisfy consensus rules (monotonic,
                # not too far in future relative to parent). The agent sim
                # clock can jump ahead by hours; mine settlement blocks from
                # tip_timestamp + cadence instead of the raw sim clock.
                blk_ts = self.chain.tip.header.timestamp + self.cadence_s
                txid = hashlib.sha3_256(json.dumps(
                    {"action": r.action_id, "to": r.explanation,
                     "amount_shard": amt, "height": self.chain.tip.header.height + 1},
                    sort_keys=True).encode()).digest()
                blk = mine_block(self.params, self.chain.tip,
                                 denylist_root=ZERO32, im_merkle_root=_empty_im_root(),
                                 difficulty=self.chain.difficulty,
                                 txids=[txid], timestamp=blk_ts)
                self.chain.validate_and_apply(blk)
                bh = blk.header.hash()
                self.store[bh] = blk
                self.height_index.append(bh)
                self.faucet_outputs.append(SnapshotOutput(
                    txid=txid.hex(), output_index=0, amount_shard=0))
                broadcast.append({"action_id": r.action_id,
                                  "txid": txid.hex(), "amount_shard": amt,
                                  "block_height": blk.header.height})
            transitions = [{"action_id": r.action_id, "state": r.state,
                            "explanation": r.explanation,
                            "txid": next((b["txid"] for b in broadcast
                                          if b["action_id"] == r.action_id),
                                         None)}
                           for r in executed]
            n_exec = sum(1 for t in transitions
                         if t["state"].startswith("executed"))
            parts = []
            if executed:
                parts.append(f"{n_exec} action(s) ran on-chain, "
                             f"{len(executed) - n_exec} blocked before running")
            if refused:
                parts.append(f"{len(refused)} could NOT run — not enough "
                             "balance (nothing was sent; it stays queued and "
                             "will retry after you fund your wallet)")
            human = (". ".join(parts) + ". Every outcome is on your "
                     "Pending/History page.") if parts else \
                  "Nothing was due this tick — queued actions still have " \
                  "veto time left."
            return {"advanced_hours": hours_ahead,
                    "broadcast": broadcast,
                    "refused_insufficient": refused,
                    "transitions": transitions,
                    "human": human}

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


def _empty_im_root() -> bytes:
    merkle_root = _sibling('chain.node').merkle_root
    return merkle_root([])


def _intent_payload(it) -> dict:
    p = it.to_json()                       # Intent always defines to_json()
    p.setdefault("clarify", getattr(it, "needs_clarify", None))
    return p
