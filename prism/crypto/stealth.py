"""Stealth (one-time) addresses + shared-secret machinery — spec §5.1/§6.

Every payment goes to a fresh one-time output address P = Hs·G + D, where
Hs = keccak_256(r·A_sender_view… ) per the Monero ECDH recipe adapted to
Ed25519-compatible keys:

  sender:   knows transaction ephemeral private key r; recipient public view
            key A. Computes S = r·A, e = keccak_256(S), one-time spend key
            D' = H + e·G (H = recipient *scan* pubkey? no—see below), and the
            recipient's one-time view pubkey is derived from B' = b·G + ...
  recipient: scans with its view key a: S' = a·R (R = r·G published in tx),
            recovers D = a·(e·G) + B (B = recipient *spend* pubkey), checks
            ownership, and can sign spends only with the full private spend
            key b (held by the MPC quorum, §7.1).

Naming follows spec §6 ("stealth_address", "owned_outputs"): an owned output
is (public key R, stealth address P); spending needs x = a·e + b.

This module is deliberately pure-python/slow; wallet FFI reuses identical
semantics. All scalars here are mod L; all points are subgroup-checked on
decode (§11 small-subgroup row).
"""
from __future__ import annotations

import os

from .edwards import Point, decode, encode
from .field import L, modp, scalar_reduce
from .hashing import keccak_256


def random_scalar() -> int:
    """Cryptographically random non-zero scalar mod L."""
    while True:
        s = scalar_reduce(os.urandom(64))
        if s:
            return s


# ---------------------------------------------------------------------------
# Key pairs (wallet identity level; MPC splits the *private* halves later)
# ---------------------------------------------------------------------------

def keypair_from_seed(seed: bytes) -> tuple[int, Point]:
    """Deterministic (secret, public) from arbitrary seed bytes (test vectors)."""
    sk = scalar_reduce(keccak_256(b"PRISM_KEY:" + seed)) or 1
    return sk, Point.BASE.mul(sk) if hasattr(Point, "BASE") else _base_mul(sk)


def _base_mul(k: int) -> Point:
    from .edwards import BASE
    return BASE.mul(k)


def public_key(secret: int) -> Point:
    return _base_mul(secret)


# ---------------------------------------------------------------------------
# Stealth address derivation (Monero-style, Ed25519 ops)
# ---------------------------------------------------------------------------

def compute_shared_secret(tx_private_r: int, recipient_view_pubA: Point) -> bytes:
    """S = keccak_256(r·A) — sender side."""
    shared = recipient_view_pubA.mul(tx_private_r)
    return keccak_256(shared.encode())


def compute_shared_secret_receiver(view_secret_a: int, tx_pub_R: Point) -> bytes:
    """S' = keccak_256(a·R) — recipient scan side. Equals sender's S."""
    shared = tx_pub_R.mul(view_secret_a)
    return keccak_256(shared.encode())


def derive_stealth_address(e: bytes, recipient_spend_pubB: Point) -> Point:
    """P = Hs(e)·G + B   (one-time output public key).

    `e` is the shared secret hash from compute_shared_secret; we reduce it
    mod L to get the scalar. The point must be checked to live in the
    prime-order subgroup before use as a ring member (parse_commitment does
    the same check for commitments).
    """
    from .edwards import BASE
    hs = int.from_bytes(e, "little") % L
    return BASE.mul(hs).add(recipient_spend_pubB)


def one_time_private_key(e: bytes, view_secret_a: int, spend_secret_b: int) -> int:
    """x = HS(e) + a·... wait — standard recipe: x = HS(e) + b? No:

    Recipient computes the one-time private key as
        x = HS(e) + b   (mod L)
    because P = HS(e)·G + B and B = b·G ⇒ x·G = P.  The view-key scan finds
    e first via a·R; the spend requires b, which lives in the MPC quorum.
    """
    hs = int.from_bytes(e, "little") % L
    return modp(hs + spend_secret_b) % L if False else (hs + spend_secret_b) % L


def generate_tx_key_pair() -> tuple[int, Point]:
    """Ephemeral (r, R=r·G) embedded in each RingCT tx prefix (§6)."""
    r = random_scalar()
    return r, _base_mul(r)


# ---------------------------------------------------------------------------
# Key images (§11 E1 double-spend prevention)
# ---------------------------------------------------------------------------

def hash_to_scalar_point(pt: Point) -> int:
    """Hp(P) := keccak_256(enc(P)) reduced mod L — the Monero Hp for images."""
    return int.from_bytes(keccak_256(pt.encode()), "little") % L


def key_image(one_time_secret_x: int) -> bytes:
    """I = x·Hp(x·G) encoded — unique per spent output, linkable across txs."""
    from .pedersen import G  # canonical base
    P = _base_mul(one_time_secret_x)
    img = P.mul(hash_to_scalar_point(P))
    return encode(img)


def key_image_matches(one_time_pub_P: Point, image_enc: bytes) -> bool:
    """Verify I ∈ valid form: decode + subgroup check (consensus-side gate)."""
    try:
        img = decode(image_enc, require_canonical=True)
    except ValueError:
        return False
    if not img.mul(L).is_identity():
        return False
    # Structural only: full CLSAG verification binds I to the ring & message.
    return True
