"""CLSAG — "Chain-Linkable Spontaneous Anonymous Group" ring signatures.

Pure-Python REFERENCE implementation for test vectors only (spec §5.1, §13:
"slow but readable reference; the Rust/FFI fast path is differentially tested
against these vectors"). Single-key variant per the Bootle–Dulieu–Fatecha /
Monero `clsag` design, simplified to ONE key image per ring for Phase 1
(multi-input aggregation shares one Fiat-Shamir transcript; the aggregated
variant is a Phase-2 refactor behind the same API).

Setup
-----
  G   : verify base (RFC 8032 basepoint)              — pedersen.G
  H   : image base, DLog_G(H) unknown (§pedersen.py)  — pedersen.H
  Hp(P) = [keccak_256(DOMAIN || enc(P)) mod L] · H    — lands in <H> by construction

Signature relation (per leg j, challenge c_j, response s_j)
-----------------------------------------------------------
  [s_j]·G  − [c_j]·P_j          == L_j
  [s_j]·Hp(P_j) − [c_j]·I       == R_j
  c_{j+1} = H(msg, ring, I, j, L_j, R_j)   (sequential Fiat-Shamir, index-bound)
  accept iff rotating n legs returns c_n == c_0.

The real signer (index i, secret x with P_i = x·G, I = x·Hp(P_i)) picks α,
sets L_i/R_i = [α]G / [α]Hp(P_i), and closes the loop with s_i = α + c_i·x.

Security properties covered by tests/test_crypto.py:
  * completeness            — honest sign → verify accepts (all ring sizes/indexes)
  * soundness               — wrong secret, tampered msg/ring/image/scalar → reject
  * linkability             — same output secret ⇒ identical image across sigs
  * anonymity (structural)  — sig size & verify cost independent of signer index
  * subgroup gating         — non-prime-order ring members/images rejected (§11 E-row)

Domain tag PRISM_CLSAG_v1 is a consensus constant: changing it changes the
chain ID (§params). NEVER reuse α or make it predictable — the reference folds
an RFC6979-style deterministic nonce derived from (x, message) with an injected
rng contribution so wallets can add entropy while test vectors stay reproducible.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .edwards import BASE, Point, decode, encode
from .field import L, modp
from .hashing import keccak_256
from .pedersen import G, H as IMAGE_BASE

DOMAIN = b"PRISM_CLSAG_v1:"


# ---------------------------------------------------------------------------
# Hash-to-point for images
# ---------------------------------------------------------------------------

def hp(pt: Point) -> Point:
    """Hp(P) = [keccak_256(DOMAIN || enc(P)) mod L] · H."""
    k = int.from_bytes(keccak_256(DOMAIN + pt.encode()), "little") % L
    return IMAGE_BASE.mul(k)


def _require_subgroup(pt: Point, what: str) -> None:
    if not pt.mul(L).is_identity():
        raise ValueError(f"{what} not in prime-order subgroup")


# ---------------------------------------------------------------------------
# Fiat-Shamir transcript (sequential, one hash per leg, index-bound)
# ---------------------------------------------------------------------------

def _leg_challenge(msg: bytes, ring: list[Point], image: Point, j: int,
                   lj: Point, rj: Point) -> int:
    """c_{j+1} = H(DOMAIN || msg || ring || I || u16(j) || L_j || R_j).

    Leg-index binding stops an adversary transplanting a valid leg between
    ring positions (transcript domain separation across permutations).
    """
    buf = bytearray(DOMAIN + msg)
    for p in ring:
        buf += p.encode()
    buf += image.encode()
    buf += (j & 0xFFFF).to_bytes(2, "little")
    buf += lj.encode() + rj.encode()
    return int.from_bytes(keccak_256(bytes(buf)), "little") % L


# ---------------------------------------------------------------------------
# Signature container
# ---------------------------------------------------------------------------

@dataclass
class ClsagSignature:
    """Wire form: opening challenge c_0 + per-member responses s_j (+ image)."""
    c0: int
    s: list[int]                          # len == ring size
    image: bytes = field(default=b"")     # encoded key image (tx prefix carries it too)

    def serialize(self) -> bytes:
        out = bytearray((self.c0 % L).to_bytes(32, "little"))
        for si in self.s:
            out += (si % L).to_bytes(32, "little")
        return bytes(out)

    @classmethod
    def deserialize(cls, blob: bytes, ring_size: int, image: bytes) -> "ClsagSignature":
        expected = 32 * (1 + ring_size)
        if len(blob) != expected:
            raise ValueError(f"clsag blob must be {expected} bytes, got {len(blob)}")
        c0 = int.from_bytes(blob[:32], "little")
        s = [int.from_bytes(blob[32 + 32 * j:64 + 32 * j], "little")
             for j in range(ring_size)]
        return cls(c0=c0, s=s, image=image)


# ---------------------------------------------------------------------------
# Signing
# ---------------------------------------------------------------------------

def _ring_and_checks(secret_x: int, signer_index: int, ring_enc: list[bytes]) -> list[Point]:
    ring = [decode(b, require_canonical=True) for b in ring_enc]
    n = len(ring)
    if not (0 <= signer_index < n):
        raise IndexError("signer_index outside ring")
    if BASE.mul(secret_x) != ring[signer_index]:
        raise ValueError("secret does not match ring member at signer_index")
    for j, p in enumerate(ring):
        _require_subgroup(p, f"ring member {j}")
    return ring


def sign(secret_x: int, signer_index: int, ring_enc: list[bytes],
         message: bytes, rng_scalar) -> ClsagSignature:
    """Produce a CLSAG signature over `message` as ring member `signer_index`.

    ring_enc:   encoded member pubkeys P_j (real ring; spec §5.1 mandates ≥16).
    rng_scalar: callable() -> fresh non-zero scalar mod L. Test vectors pass a
                deterministic counter-based generator; wallets pass a CSPRNG.
    """
    ring = _ring_and_checks(secret_x, signer_index, ring_enc)
    n = len(ring)

    P_i = ring[signer_index]
    Hp_i = hp(P_i)
    image_pt = Hp_i.mul(secret_x)                    # I = x·Hp(P_i)

    # Nonce: fold injected randomness with a deterministic (x, msg) term so
    # vectors reproduce with a fixed rng while wallets gain extra entropy.
    det = int.from_bytes(keccak_256(
        b"PRISM_NONCE:" + secret_x.to_bytes(32, "little") + message), "little")
    alpha = (det + rng_scalar()) % L
    if alpha == 0:
        raise ValueError("degenerate nonce")

    ss: list[int] = [0] * n
    cs: list[int] = [0] * n

    # Real leg commitment, then rotate forward through all decoys until we
    # come back around to index i — that returned challenge IS c_i.
    l_cur = G.mul(alpha)
    r_cur = Hp_i.mul(alpha)
    c_next = _leg_challenge(message, ring, image_pt, signer_index, l_cur, r_cur)

    step = 1
    while True:
        j = (signer_index + step) % n               # leg whose challenge is c_next
        if j == signer_index:                        # full rotation complete
            break
        cs[j] = c_next
        s_j = rng_scalar()
        ss[j] = s_j
        Hj = hp(ring[j])
        l_cur = G.mul(s_j).sub(ring[j].mul(c_next))  # L_j
        r_cur = Hj.mul(s_j).sub(image_pt.mul(c_next))  # R_j
        c_next = _leg_challenge(message, ring, image_pt, j, l_cur, r_cur)
        step += 1

    c_i = c_next                                     # closed loop back onto real leg
    ss[signer_index] = (alpha + c_i * secret_x) % L
    c_0 = cs[0]                                      # wire opener: challenge of leg 0

    return ClsagSignature(c0=c_0, s=ss, image=encode(image_pt))


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

def verify(sig: ClsagSignature, ring_enc: list[bytes], message: bytes) -> bool:
    """Verify a CLSAG signature. Returns True/False; raises on malformed input."""
    ring = [decode(b, require_canonical=True) for b in ring_enc]
    n = len(ring)
    if len(sig.s) != n:
        return False
    try:
        image_pt = decode(sig.image, require_canonical=True)
    except ValueError:
        return False
    _require_subgroup(image_pt, "key image")
    for j, p in enumerate(ring):
        _require_subgroup(p, f"ring member {j}")

    c = sig.c0 % L                                   # challenge for leg 0
    for j in range(n):
        s_j = sig.s[j] % L
        Hj = hp(ring[j])
        lj = G.mul(s_j).sub(ring[j].mul(c))
        rj = Hj.mul(s_j).sub(image_pt.mul(c))
        c = _leg_challenge(message, ring, image_pt, j, lj, rj)   # next leg's challenge
    return c == (sig.c0 % L)                         # rotation must close the ring
