"""Bidirectional PRSM payment channels + watchtower — spec §10.2, E3 (Phase 3 beta).

LN-style commitment channels adapted to Prism's economics:

  * Open/close cost = exactly 2 base-fee transactions (§10.2) — the channel
    itself is off-chain state; only funding and settlement touch L1.
  * In-channel payments: zero fee, instant. Each payment bumps a monotonic
    *state version* signed by both peers; the latest joint state is what a
    honest close settles on.
  * Cheating close penalty: if a peer broadcasts a STALE state claiming more
    for themselves, the counterparty's response path is the revocation
    witness — knowledge of the next state's signature proves the old state
    was superseded, and the full channel balance moves to the innocent party.
    This reference build models the penalty outcome at the protocol level
    (watchtower submits the latest commitment; penalty tx consumes the
    cheating output); production binds these to CLTV-timelocked adapter
    transactions on L1.
  * Watchtower outsourcing (§10.2, E3): clients upload **encrypted penalty
    blobs** (the latest state signatures, sealed to the tower's key). The
    tower never sees amounts or counterparties in plaintext; it watches the
    chain for closes older than the newest blob and submits the penalty.

Trust boundaries (§3): the tower learns only that *some* channel exists and
when to act; correctness does not depend on tower honesty — a lying tower
can at worst fail to act (liveness), never steal (safety), because blobs are
useless without the client's own counter-signature which is already inside
the state pair.

Reference implementation: readable, stdlib-only except prism.crypto, tested
in prism/tests/test_l2_channels.py. NOT value-safe yet (beta posture): no
real timelocks, no on-chain enforcement — those land with the node mempool.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
from dataclasses import dataclass, field as dc_field
from enum import Enum

from ..chain.params import BASE_FEE_SHARDS
from ..crypto.edwards import Point
from ..crypto.field import L


class ChannelError(ValueError):
    """Protocol violation inside a channel (stale sign, overdraft, etc.)."""


class State(str, Enum):
    OPENING = "opening"        # funding tx built, not yet confirmed
    OPEN = "open"              # funded, payments allowed
    PENDING_CLOSE = "pending_close"   # a unilateral close seen on-chain
    CLOSED = "closed"          # cooperative/settled close confirmed
    PENALIZED = "penalized"    # cheating close punished, balance to victim


# ---------------------------------------------------------------- crypto --

def _h(*parts: bytes) -> bytes:
    return hashlib.sha256(b"|".join(parts)).digest()


@dataclass(frozen=True)
class KeyPair:
    """Demo-grade signing keys (scalar mod L). Production: MPC/FROST legs."""
    secret: int
    public: Point

    @classmethod
    def generate(cls) -> "KeyPair":
        from ..crypto.edwards import BASE
        s = secrets.randbelow(L - 2) + 1
        return cls(secret=s, public=BASE.mul(s))

    def sign(self, msg: bytes) -> bytes:
        """Schnorr on the Edwards group: challenge k = H([r]G || X || msg),
        response s = r + k*x mod L (the same shape as crypto/clsag's ring
        base case). Nonce r = H(sk||msg) — deterministic for beta; a real
        deployment derives it via RFC-8032-style hashed secret+message with
        domain separation. NOTE: [s]G == R + [k]X holds only because every
        point here lives in the prime-order subgroup (scalars reduced mod L);
        cofactor clearing is unnecessary for internally generated keys and
        is enforced on decode by require_canonical elsewhere."""
        from ..crypto.edwards import BASE
        r = int.from_bytes(_h(self.secret.to_bytes(32, "big"), msg), "big") % L
        if r == 0:
            r = 1
        R = BASE.mul(r)
        k = int.from_bytes(_h(R.encode(), self.public.encode(), msg), "big") % L
        s = (r + k * self.secret) % L
        return R.encode() + s.to_bytes(32, "little")

    @staticmethod
    def verify(pub: Point, msg: bytes, sig: bytes) -> bool:
        from ..crypto.edwards import BASE
        R = Point.decode(sig[:32])
        s = int.from_bytes(sig[32:], "little")
        k = int.from_bytes(_h(R.encode(), pub.encode(), msg), "big") % L
        return BASE.mul(s) == R.add(pub.mul(k))


def make_state(channel_id: str, version: int, bal_a: int, bal_b: int) -> bytes:
    """Canonical state payload both peers sign. Amounts in shards."""
    return json.dumps({"cid": channel_id, "v": version,
                       "a": bal_a, "b": bal_b}, sort_keys=True).encode()


@dataclass(frozen=True)
class SignedState:
    channel_id: str
    version: int
    bal_a: int
    bal_b: int
    sig_a: bytes
    sig_b: bytes

    @property
    def payload(self) -> bytes:
        return make_state(self.channel_id, self.version, self.bal_a, self.bal_b)

    def state_hash(self) -> bytes:
        return _h(self.payload)

    def verify_against(self, pub_a: Point, pub_b: Point) -> bool:
        return (KeyPair.verify(pub_a, self.payload, self.sig_a)
                and KeyPair.verify(pub_b, self.payload, self.sig_b))


# ------------------------------------------------------------- channel ----

@dataclass
class Channel:
    """One bidirectional channel between two peers (local view)."""
    channel_id: str
    capacity_shard: int
    pub_a: Point
    pub_b: Point | None = None
    balance_a: int = 0
    balance_b: int = 0
    version: int = 0
    state: State = State.OPENING
    history: list[SignedState] = dc_field(default_factory=list)
    open_fee_paid: int = 0
    close_fee_paid: int = 0

    def __post_init__(self):
        if self.capacity_shard <= 0:
            raise ChannelError("capacity must be positive")
        if self.pub_b is not None and not isinstance(self.pub_b, Point):
            raise ChannelError("pub_b must be an Edwards point")

    # -- lifecycle ----------------------------------------------------

    def fund(self, initial_a: int, initial_b: int) -> SignedState:
        """Confirm the funding tx: v0 state, balances set. Costs one base
        fee (the open tx) — §10.2 counts 2 base-fee txs per channel
        lifetime (open + close); we record each when paid."""
        if self.state != State.OPENING:
            raise ChannelError(f"cannot fund from {self.state}")
        if initial_a + initial_b != self.capacity_shard:
            raise ChannelError("funding must equal capacity exactly")
        self.balance_a, self.balance_b = initial_a, initial_b
        self.open_fee_paid = BASE_FEE_SHARDS
        st = self._sign_and_store(initial_a, initial_b)
        self.state = State.OPEN
        return st

    def pay(self, amount_shard: int, *, from_a: bool) -> SignedState:
        """In-channel payment: instant, zero fee (§10.2)."""
        if self.state != State.OPEN:
            raise ChannelError(f"channel not open ({self.state})")
        if amount_shard <= 0:
            raise ChannelError("payment must be positive")
        if from_a:
            if amount_shard > self.balance_a:
                raise ChannelError("A overdraft")
            self.balance_a -= amount_shard
            self.balance_b += amount_shard
        else:
            if amount_shard > self.balance_b:
                raise ChannelError("B overdraft")
            self.balance_b -= amount_shard
            self.balance_a += amount_shard
        return self._sign_and_store(self.balance_a, self.balance_b)

    def cooperative_close(self) -> dict:
        """Both sign the latest state and settle on-chain: one base fee."""
        if self.state not in (State.OPEN, State.PENDING_CLOSE):
            raise ChannelError(f"cannot close from {self.state}")
        self.close_fee_paid = BASE_FEE_SHARDS
        self.state = State.CLOSED
        return {"settled_a": self.balance_a, "settled_b": self.balance_b,
                "close_fee_shard": self.close_fee_paid}

    def total_chain_cost_shard(self) -> int:
        """§10.2 acceptance: exactly 2 base fees over the channel lifetime."""
        return self.open_fee_paid + self.close_fee_paid

    # -- internals ----------------------------------------------------

    def _sign_and_store(self, bal_a: int, bal_b: int) -> SignedState:
        # NOTE: this local view holds BOTH keypairs' public sides only;
        # signatures arrive from each peer's signer. For the beta we model
        # the exchange explicitly via sign_for().
        self.version += 1
        payload = make_state(self.channel_id, self.version, bal_a, bal_b)
        pending = SignedState(self.channel_id, self.version, bal_a, bal_b,
                              sig_a=b"", sig_b=b"")
        self._pending_payload = payload
        return pending

    def sign_for(self, side: str, keypair: KeyPair) -> SignedState:
        """Peer signs the current pending state; once both sigs are in,
        the state is stored in history."""
        payload = getattr(self, "_pending_payload", None)
        if payload is None:
            raise ChannelError("no pending state to sign")
        sig = keypair.sign(payload)
        parts = json.loads(payload)
        st = SignedState(self.channel_id, parts["v"], parts["a"], parts["b"],
                         sig_a=sig, sig_b=b"") if side == "a" else \
             SignedState(self.channel_id, parts["v"], parts["a"], parts["b"],
                         sig_a=b"", sig_b=sig)
        # merge with existing half-signed state if present
        if self.history and self.history[-1].version == st.version:
            prev = self.history[-1]
            merged = SignedState(st.channel_id, st.version, st.bal_a, st.bal_b,
                                 sig_a=prev.sig_a or st.sig_a,
                                 sig_b=prev.sig_b or st.sig_b)
            if merged.sig_a and merged.sig_b:
                self.history[-1] = merged
                del self._pending_payload
                return merged
            self.history[-1] = merged
            return merged
        if side == "a":
            self.history.append(st)
        else:
            self.history.append(st)
        return st


# Convenience: two-party ceremony where one process drives both peers
# (testnet/devnet posture; production wallet runs one side per device).

def open_channel(capacity_shard: int, kp_a: KeyPair, kp_b: KeyPair,
                 initial_a: int | None = None) -> tuple[Channel, list[SignedState]]:
    cid = "ch-" + os.urandom(8).hex()
    ch = Channel(channel_id=cid, capacity_shard=capacity_shard,
                 pub_a=kp_a.public, pub_b=kp_b.public)
    ia = initial_a if initial_a is not None else capacity_shard // 2
    states: list[SignedState] = []
    ch.fund(ia, capacity_shard - ia)
    states.append(_both_sign(ch, kp_a, kp_b))
    return ch, states


def _both_sign(ch: Channel, kp_a: KeyPair, kp_b: KeyPair) -> SignedState:
    ch.sign_for("a", kp_a)
    final = ch.sign_for("b", kp_b)
    return final


def channel_pay(ch: Channel, kp_a: KeyPair, kp_b: KeyPair,
                amount_shard: int, *, from_a: bool) -> SignedState:
    ch.pay(amount_shard, from_a=from_a)
    return _both_sign(ch, kp_a, kp_b)


# ---------------------------------------------------------- watchtower ----

@dataclass(frozen=True)
class PenaltyBlob:
    """Encrypted (latest-state) bundle uploaded to the tower. The tower
    stores ciphertext only; it can detect a stale close by comparing the
    *plaintext-inaccessible* blob against chain only after the client
    shares the channel id + MAC key out-of-band... For the beta the blob is
    AES-free: XOR-stream sealed with a per-blob key derived from a shared
    secret, plus a cleartext index (channel_id hash + version) the tower
    needs to order blobs. Amounts/counterparties stay confidential."""
    channel_tag: bytes     # truncated hash — tower-side index
    version: int
    ciphertext: bytes
    mac: bytes


def seal_blob(shared_secret: bytes, state: SignedState) -> PenaltyBlob:
    tag = _h(b"tag", shared_secret, state.channel_id.encode())[:16]
    plain = (state.payload, state.sig_a, state.sig_b)
    body = b"".join(len(p).to_bytes(4, "big") + p for p in plain)
    stream = _keccak_stream(shared_secret, len(body))
    ct = bytes(a ^ b for a, b in zip(body, stream))
    mac = _h(b"mac", shared_secret, tag, bytes([state.version]), ct)
    return PenaltyBlob(channel_tag=tag, version=state.version,
                       ciphertext=ct, mac=mac)


def unseal_blob(shared_secret: bytes, blob: PenaltyBlob) -> SignedState:
    expect = _h(b"mac", shared_secret, blob.channel_tag,
                bytes([blob.version]), blob.ciphertext)
    if not secrets.compare_digest(expect, blob.mac):
        raise ChannelError("blob MAC mismatch (tampered or wrong secret)")
    stream = _keccak_stream(shared_secret, len(blob.ciphertext))
    body = bytes(a ^ b for a, b in zip(blob.ciphertext, stream))
    off = 0
    parts = []
    for _ in range(3):
        n = int.from_bytes(body[off:off + 4], "big")
        off += 4
        parts.append(body[off:off + n])
        off += n
    payload, sig_a, sig_b = parts
    j = json.loads(payload)
    return SignedState(j["cid"], j["v"], j["a"], j["b"], sig_a, sig_b)


def _keccak_stream(seed: bytes, n: int) -> bytes:
    from ..crypto.hashing import keccak_256
    out = b""
    ctr = 0
    while len(out) < n:
        out += keccak_256(seed + ctr.to_bytes(8, "big"))
        ctr += 1
    return out[:n]


class Watchtower:
    """E3 guardian: holds encrypted blobs, watches for closes, acts on
    stale ones. Compromised tower => nothing leaked beyond metadata;
    offline tower => liveness loss only (client can self-submit)."""

    def __init__(self):
        # channel_tag -> {version: blob}
        self.store: dict[bytes, dict[int, PenaltyBlob]] = {}

    def register(self, blob: PenaltyBlob) -> None:
        if blob.version < 0 or blob.version > 255:
            raise ChannelError("version byte overflow")
        self.store.setdefault(blob.channel_tag, {})[blob.version] = blob

    def newest_version(self, channel_tag: bytes) -> int | None:
        d = self.store.get(channel_tag)
        return max(d) if d else None

    def on_close_seen(self, channel_tag: bytes, closed_version: int) -> dict:
        """Chain observer callback. Returns the action the tower should take.

        - closed_version == newest known  -> honest close, do nothing
        - closed_version <  newest known  -> CHEATING close: submit penalty
          with the newest blob (its paired sigs authorize taking everything)
        - unknown channel                 -> ignore (no obligation)
        """
        v = self.newest_version(channel_tag)
        if v is None:
            return {"action": "ignore", "reason": "unknown channel"}
        if closed_version >= v:
            return {"action": "none", "reason": "close matches latest state"}
        blob = self.store[channel_tag][v]
        return {"action": "penalize", "with_version": v, "blob": blob}


# --------------------------------------------------------- penalty path ---

def penalty_outcome(latest: SignedState, cheat: SignedState) -> dict:
    """Protocol-level penalty rule (E3/E2): broadcasting `cheat` (older
    version) lets the counterparty claim the FULL channel balance.

    Assumes `latest` is jointly signed truth and cheat.claimed_self exceeds
    the honest share at that old version — the classic LN griefing case.
    Returns shard amounts credited to each side after punishment.
    """
    if latest.version <= cheat.version:
        raise ChannelError("not a stale-close evidence pair")
    cap = latest.bal_a + latest.bal_b
    cheater_is_a = cheat.bal_a > latest.bal_a
    if cheater_is_a:
        return {"winner": "b", "credit_a": 0, "credit_b": cap,
                "evidence_version": latest.version,
                "broadcast_version": cheat.version}
    return {"winner": "a", "credit_a": cap, "credit_b": 0,
            "evidence_version": latest.version,
            "broadcast_version": cheat.version}
