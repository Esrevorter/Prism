"""Field arithmetic mod the Ed25519 prime — spec §5.1, crypto/ deliverable 1.

p = 2^255 - 19. Pure-Python reference implementation; the Rust/FFI fast path
(reddsum-style) replaces this at hardhat time with identical semantics.

Everything here is deterministic and side-channel-naive ON PURPOSE: this is
the readable reference against which the constant-time production code is
differentially tested (§13 Phase-1 acceptance criteria).
"""
from __future__ import annotations

P = (1 << 255) - 19                      # field prime
# Canonical Ed25519 group order ℓ (RFC 8032 §4.1 / libsodium):
#   ℓ = 2^252 + 27742317777372353535851937790883648493
#     = 7237005577332262213973186563042994240857116359379907606001950938285454250989
# NOTE (ROOT CAUSE #3, take two): the previous "two-term derivation" here used a
# MISTYPED second term (...348520045438832269067752), producing a NON-prime
# modulus ~9e31 off. Its decimal pin (_L_CANONICAL) and its LE byte pin were
# BOTH transcribed from that same wrong derivation, so all three guards agreed
# with each other and disagreed with reality — the classic self-referential-pin
# failure. The value below is now derived from the RFC 8032 BIG-ENDIAN hex
# literal (an independent representation), cross-verified against libsodium's
# sc_reduce in tests/test_crypto.py, and guarded by import-time primality and
# L < P checks which the old composite value could never survive.
_L_RFC8032_BE_HEX = "1000000000000000000000000000000014DEF9DEA2F79CD65812631A5CF5D3ED"
L = int(_L_RFC8032_BE_HEX, 16)           # group order (cofactor 8)
assert L == (1 << 252) + 27742317777372353535851937790883648493
# Curve parameter d for a = -1: d = -(121665/121666) mod p.
D = (-121665 * pow(121666, P - 2, P)) % P                  # curve parameter (a = -1)

# Canonical-value pinning (import-time regression guards). Values independently
# verified against libsodium/pynacl ground truth during the [2]B-gate debug:
#   d = 37095705934669...0283555  (Ed25519 curve parameter, RFC 8032 §4.1)
#   l = 72370055773322...250989   (group order, RFC 8032 §4.1)
_D_CANONICAL = 37095705934669439343138083508754565189542113879843219016388785533085940283555
_L_CANONICAL = 7237005577332262213973186563042994240857116359379907606001950938285454250989
assert D == _D_CANONICAL, f"d mismatch: {D} != {_D_CANONICAL}"
assert L == _L_CANONICAL, f"l mismatch: {L} != {_L_CANONICAL}"
# Second, independent guard: ℓ as its canonical little-endian byte string
# (libsodium sc_reduce / RFC 8032 "l" encoding). The previous draft's LE pin was
# itself transcribed from the wrong derivation, so all guards were mutually
# consistent but wrong; this literal is now taken directly from RFC 8032 and
# cross-checked against crypto_core_ed25519_scalar_reduce in tests/test_crypto.py.
assert L.to_bytes(32, "little") == bytes.fromhex(
    "edd3f55c1a631258d69cf7a2def9de1400000000000000000000000000000010"
), "l does not match the RFC 8032 little-endian group-order encoding"


def _is_prime(n: int) -> bool:
    """Deterministic-enough Miller-Rabin for import-time sanity checks."""
    if n < 2 or n % 2 == 0:
        return n == 2
    d, s = n - 1, 0
    while d % 2 == 0:
        d //= 2
        s += 1
    for a in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        x = pow(a, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(s - 1):
            x = x * x % n
            if x == n - 1:
                break
        else:
            return False
    return True


# Structural guard: the subgroup order must be prime and < p. The old composite
# (mistyped) L would have failed this instantly — decimal pins copied from the
# same wrong source cannot catch transcription errors, primality can.
assert L < P, "group order l must be less than the field prime p"
assert _is_prime(L), "group order l must be prime"


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
    """Little-endian byte string of ANY length -> scalar mod L.

    Regression note: an earlier draft truncated with b[:32], silently dropping
    the upper half of keccak_512 digests. That made hash_to_scalar disagree
    with its documented definition (full keccak_512 -> LE int -> mod L,
    RFC 8032 style) and cascaded into every H_p / CLSAG test. Pinned by
    test_hash_to_scalar_matches_definition in tests/test_crypto.py.
    """
    return int.from_bytes(b, "little") % L


def scalar_encode(s: int) -> bytes:
    """Scalar -> canonical little-endian 32 bytes."""
    return (s % L).to_bytes(32, "little")
