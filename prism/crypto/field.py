"""Field arithmetic mod the Ed25519 prime — spec §5.1, crypto/ deliverable 1.

p = 2^255 - 19. Pure-Python reference implementation; the Rust/FFI fast path
(reddsum-style) replaces this at hardhat time with identical semantics.

Everything here is deterministic and side-channel-naive ON PURPOSE: this is
the readable reference against which the constant-time production code is
differentially tested (§13 Phase-1 acceptance criteria).
"""
from __future__ import annotations

P = (1 << 255) - 19                      # field prime
# Canonical Ed25519 group order ℓ = 2^252 + 2774231777737235348520045438832269067752.
# Regression note: an earlier draft carried a mistyped constant ending in
# ...8103544377797515327, which broke every scalar-mul identity (the [2]B gate),
# hash_to_scalar reductions, and subgroup checks simultaneously. Pinned by
# test_group_order_matches_rfc8032 in tests/test_crypto.py.
# Canonical Ed25519 group order ℓ (RFC 8032 §4.1), written directly as its
# decimal expansion to avoid transcription errors in the 2^252 + k form.
# Regression note: two earlier drafts carried mistyped constants, which broke
# every scalar-mul identity (the [2]B gate), hash_to_scalar reductions, and
# subgroup checks simultaneously. Pinned by test_group_order_matches_rfc8032
# in tests/test_crypto.py via the _L_CANONICAL import-time assert below.
L = 72370055773322622139731865630428944088091893758435788320921758847568941322382  # group order (cofactor 8)
# Curve parameter d for a = -1: d = -(121665/121666) mod p.
D = (-121665 * pow(121666, P - 2, P)) % P                  # curve parameter (a = -1)

# Canonical-value pinning (import-time regression guards). Values independently
# verified against libsodium/pynacl ground truth during the [2]B-gate debug:
#   d = 37095705934669...0283555  (Ed25519 curve parameter, RFC 8032 §4.1)
#   l = 72370055773322...1322382  (group order, RFC 8032 §4.1)
_D_CANONICAL = 37095705934669439343138083508754565189542113879843219016388785533085940283555
_L_CANONICAL = 72370055773322622139731865630428944088091893758435788320921758847568941322382
assert D == _D_CANONICAL, f"d mismatch: {D} != {_D_CANONICAL}"
assert L == _L_CANONICAL, f"l mismatch: {L} != {_L_CANONICAL}"


def modp(x: int) -> int:
    return x % P


def inv(x: int) -> int:
    """Multiplicative inverse mod p (x != 0)."""
    if x % P == 0:
        raise ZeroDivisionError("no inverse for 0 mod p")
    return pow(x, P - 2, P)


def sqrt(x: int) -> int | None:
    """Square root mod p, or None if x is a non-residue.

    p ≡ 5 (mod 8), so for a residue x we have r = x^((p+3)/8); if r^2 != x
    then multiply by the known square root of -1. Returns one of the two
    roots (the other is p - r).
    """
    x %= P
    if x == 0:
        return 0
    r = pow(x, (P + 3) >> 3, P)
    if (r * r - x) % P != 0:
        r = (r * pow(2, (P - 1) >> 2, P)) % P   # * sqrt(-1)
        if (r * r - x) % P != 0:
            return None
    return r


# ----------------------------------------------------------- scalars ------

def scalar_reduce(b: bytes) -> int:
    """Little-endian 32-byte string -> scalar mod L (RFC 8032 convention)."""
    return int.from_bytes(b[:32], "little") % L


def scalar_encode(s: int) -> bytes:
    """Scalar -> canonical little-endian 32 bytes."""
    return (s % L).to_bytes(32, "little")
