"""Pedersen commitments + the two-base setup — spec §5.1, crypto/ deliverable 2.

Prism commits to transaction amounts as

    C = v·H + r·G

where `v` is the value in shards (uint64), `r` is a random blinding scalar,
`G` is the Ed25519 basepoint and `H` is a second generator whose discrete log
base G is *nobody-knows-by-construction* (see `compute_H`). Binding hides the
value; hiding hides the value behind `r`. Additive homomorphism —
C(v1,r1) + C(v2,r2) == C(v1+v2, r1+r2) — is what makes RingCT balance proofs
(Σinputs == Σoutputs) work without revealing any amount.

Domain separation: every on-the-wire commitment hashes a fixed ASCII prefix
into keccak_256 before reducing mod L to derive its scalar multiple of H. The
prefixes are consensus constants; changing them changes the chain ID (§params).
"""
from __future__ import annotations

from .edwards import BASE, Point, decode, encode
from .field import L, modp, scalar_reduce
from .hashing import keccak_256

G = BASE  # primary generator (RFC 8032 basepoint), order L.

# --------------------------------------------------------------------------
# H — the secondary generator ("nothing-up-my-sleeve" derivation)
# --------------------------------------------------------------------------
# Monero derives H as the hash-to-point of the canonical encoding of B (=G):
#   H = [8 · (keccak_256(enc(G)) mod L)] · G.  Clearing the cofactor matters:
#   it puts H in the prime-order subgroup, so <H> ∩ torsion = {identity} —
#   the property the key-image check I = x·H relies on (§11 small-subgroup row).
def _derive_h_scalar() -> int:
    raw = int.from_bytes(keccak_256(G.encode()), "little") % L
    return modp(8 * raw)


_Hk = _derive_h_scalar()
H = G.mul(_Hk)          # H = [8·(keccak256(enc(G)) mod L)] · G

assert not H.is_identity()


# --------------------------------------------------------------------------
# Domain-separated generators for other purposes
# --------------------------------------------------------------------------

def hp_named(prefix: str) -> Point:
    """Derive an independent generator from an ASCII domain tag.

    Used for: pseudo-output commitment base (fee transparency), memo keys,
    accumulator roots. Same nothing-up-my-sleeve recipe as H.
    """
    raw = int.from_bytes(keccak_256(b"PRISM_GEN:" + prefix.encode("ascii")), "little")
    return G.mul(modp(8 * raw) % L)


# Convenience: base for the fee-visible pseudo-output commitment (§6 tx schema).
FEE_BASE = hp_named("pseudo_output_v3")


# --------------------------------------------------------------------------
# Commitments
# --------------------------------------------------------------------------

def commit(value: int, mask: int) -> Point:
    """C = v·H + r·G  (value in shards, 0 <= v < 2^64; mask reduced mod L)."""
    if not (0 <= value < (1 << 64)):
        raise ValueError("commitment value must fit in uint64 shards")
    return H.mul(value).add(G.mul(mask % L))


def commit_bytes(value: int, mask: int) -> bytes:
    return encode(commit(value, mask))


def parse_commitment(b: bytes) -> Point:
    """Decode a wire commitment; reject points outside the prime-order subgroup."""
    pt = decode(b, require_canonical=True)
    if not pt.mul(L).is_identity():
        raise ValueError("commitment not in prime-order subgroup")
    return pt


def blind_hash(data: bytes) -> int:
    """Deterministic blinding scalar from entropy (test vectors / MPC reseed)."""
    return scalar_reduce(data)


class OpeningError(Exception):
    pass


def verify_opening(commitment: Point, value: int, mask: int) -> bool:
    """Check an opening of a commitment (used by tests & auditor tooling)."""
    try:
        return commitment == commit(value, mask)
    except ValueError:
        return False
