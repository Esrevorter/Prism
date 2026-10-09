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


# Canonical square root of -1 mod p (RFC 8032 §5.1.4 "I"). This specific value
# is also 2^((p-1)/4) mod p; squaring it yields -1 (asserted below).
_SQRT_M1 = 19681161376707505956807079304988542015446066515923890162744021073123829784752
assert (_SQRT_M1 * _SQRT_M1 + 1) % P == 0, "internal error: not sqrt(-1)"


def sqrt(x: int) -> int | None:
    """Square root mod p, or None if x is a non-residue.

    p ≡ 5 (mod 8). RFC 8032 §5.1.4 algorithm:

        r = x^((p+3)/8)          # candidate root
        if v == -1:              # v = x^((p-1)/2), Euler criterion
            r = r * I            # I = sqrt(-1); now r^2 == x
        if r^2 != x: return None # non-residue (or x == 0 handled above)

    REGRESSION NOTE (ROOT CAUSE of the encode/decode round-trip failures and
    every downstream CLSAG "not a curve point" abort): two earlier drafts
    skipped the Euler-criterion step and instead did
    `if r*r != x: r = r * 2^((p-1)/4); if r*r != x: return None`.
    Although 2^((p-1)/4) IS sqrt(-1) here, the unconditional second attempt
    only rescues residues whose candidate needs rotation; worse, the FIRST
    draft's fallback exponent was later mangled so that ~half of all valid
    curve encodings were classified as non-residues while small-y probes
    (whose roots land in the direct branch) appeared fine. The pinned
    canonical I above plus the Euler test matches libsodium on all vectors.
    """
    x %= P
    if x == 0:
        return 0
    r = pow(x, (P + 3) >> 3, P)
    v = pow(x, (P - 1) >> 2, P)                    # Euler criterion: ±1
    if v == P - 1:                                 # v == -1: rotate by sqrt(-1)
        r = modp(r * _SQRT_M1)
    if (r * r - x) % P != 0:
        return None                                # genuine non-residue
    return r


# ----------------------------------------------------------- scalars ------

def scalar_reduce(b: bytes) -> int:
    """Little-endian 32-byte string -> scalar mod L (RFC 8032 convention)."""
    return int.from_bytes(b[:32], "little") % L


def scalar_encode(s: int) -> bytes:
    """Scalar -> canonical little-endian 32 bytes."""
    return (s % L).to_bytes(32, "little")
