"""Keccak-256/512 and Prism's `H_p` hash-to-curve — spec §5.1, crypto/ deliverable 1b.

Two families of hashes are needed:

* **Consensus hashing** — Keccak-f[1600] with the original (pre-NIST) padding
  0x01 ... 0x80, i.e. what Monero calls keccak_256. We implement it in pure
  Python so the reference node has zero third-party dependencies; the FFI
  path swaps in a vectorized version later (identical output).

* **H_p(message)** — "hash to point": keccak_512(msg) reduced mod L, used as
  a scalar times the secondary base H (§pedersen.py). This is the Monero
  practice: it is *not* a full SSWU map, but its image lies in the prime-order
  subgroup by construction (scalar · H), which is all CLSAG/Pedersen need.
  Documented deviation from strict RFC 9380 hash-to-curve; see R1 research.
"""
from __future__ import annotations

import struct

from .edwards import Point, BASE
from .field import L, scalar_reduce

# ------------------------------------------------------------- keccak ------

_RC = [
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000,
    0x000000000000808B, 0x0000000080000001, 0x8000000080008081, 0x8000000000008009,
    0x000000000000008A, 0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089, 0x8000000000008003,
    0x8000000000008002, 0x8000000000000080, 0x000000000000800A, 0x800000008000000A,
    0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
]
# Rotation offsets keyed by FLAT lane index i = x + 5*y. The table is pinned
# to the canonical Keccak published offsets; a self-check derives it from the
# rho lane walk (start (1,0); offset(t)=t(t+1)/2 mod 64 for t=1..23; advance
# (x,y)->(y,(2x+3y)%5)) and patches the single lane (1,1) whose visit falls
# outside the 23-step window. Any drift between the literal table and the
# derivation raises at import time — consensus-critical invariant.
_OFF_FLAT_LIT = [0, 1, 62, 28, 27, 36, 44, 6, 55, 20,
                 3, 10, 43, 25, 39, 41, 45, 15, 21, 8,
                 18, 2, 61, 56, 14]


def _derive_off_flat() -> list[int]:
    off = [0] * 25
    x, y = 1, 0
    for t in range(1, 24):
        off[x + 5 * y] = (t * (t + 1) // 2) % 64
        x, y = y, (2 * x + 3 * y) % 5
    # The pi-map walk has period 24 but only covers 23 of the non-zero lanes;
    # lane (1,1) (flat index 6) is never visited and must be assigned its
    # canonical offset explicitly.
    off[1 + 5 * 1] = 44
    return off


_OFF_FLAT = _derive_off_flat()
assert _OFF_FLAT == _OFF_FLAT_LIT, (
    "rho offset table drifted from canonical Keccak values: "
    f"derived={_OFF_FLAT} expected={_OFF_FLAT_LIT}"
)
# Bug history (three failed revisions, all caught by KATs + pycryptodome diff):
#  * a transposed display table (_ROT[y][x]) permuted off-diagonal offsets;
#  * a walk started at (1,0) assigning t=0's offset 0 to lane (1,0) shifted
#    the whole schedule by one step (lane (1,0) must get 1, not 0);
#  * a hybrid [x][y]-list version mixed row/column conventions with the flat
#    absorb map. Pinned by test_keccak256_known_answers and the differential
#    gate in prism/tests/test_crypto.py.
_MASK = (1 << 64) - 1


def _rol(x: int, n: int) -> int:
    n %= 64
    return ((x << n) | (x >> (64 - n))) & _MASK if n else x


def _keccak_f(a: list[int]) -> None:
    """Keccak-f[1600], state as a flat list of 25 lanes, lane (x,y) = a[x+5y]."""
    for rc in _RC:
        # theta
        c = [a[x] ^ a[x + 5] ^ a[x + 10] ^ a[x + 15] ^ a[x + 20] for x in range(5)]
        d = [c[(x - 1) % 5] ^ _rol(c[(x + 1) % 5], 1) for x in range(5)]
        for x in range(5):
            for y in range(5):
                a[x + 5 * y] ^= d[x]
        # rho + pi (flat form): B[y + 5*((2x+3y)%5)] = rol(A[x + 5y], off[x + 5y])
        b = [0] * 25
        for x in range(5):
            for y in range(5):
                b[y + 5 * ((2 * x + 3 * y) % 5)] = _rol(a[x + 5 * y], _OFF_FLAT[x + 5 * y])
        # chi
        for x in range(5):
            for y in range(5):
                a[x + 5 * y] = b[x + 5 * y] ^ ((~b[(x + 1) % 5 + 5 * y] & _MASK)
                                               & b[(x + 2) % 5 + 5 * y])
        # iota — lane (0,0) is flat index 0 (same lane the absorb/squeeze map hits first)
        a[0] ^= rc


def _keccak(data: bytes, out_len: int, pad: int) -> bytes:
    rate = 200 - 2 * out_len          # bytes absorbed per lane block
    a = [0] * 25
    # Keccak (original) padding: append pad byte, zero-fill, XOR 0x80 into
    # the final byte of the padded message.
    padded = bytearray(data)
    padded.append(pad)
    while len(padded) % rate:
        padded.append(0x00)
    padded[-1] ^= 0x80
    for off in range(0, len(padded), rate):
        block = padded[off:off + rate]
        for i in range(rate // 8):
            a[i] ^= int.from_bytes(block[8 * i:8 * i + 8], "little")
        _keccak_f(a)
    # squeeze
    out = bytearray()
    while len(out) < out_len:
        for i in range(rate // 8):
            out += a[i].to_bytes(8, "little")
            if len(out) >= out_len:
                break
        if len(out) < out_len:
            _keccak_f(a)
    return bytes(out[:out_len])


def keccak_256(data: bytes) -> bytes:
    """Original Keccak padding (0x01), NOT SHA3's 0x06 — consensus-critical."""
    return _keccak(data, 32, 0x01)


def keccak_512(data: bytes) -> bytes:
    return _keccak(data, 64, 0x01)


# ------------------------------------------------------------ H_p ---------

def hash_to_scalar(data: bytes) -> int:
    """keccak_512 -> scalar mod L (RFC 8032 style reduction)."""
    return scalar_reduce(keccak_512(data))


def hp(data: bytes, base: Point) -> Point:
    """H_p(message) := [keccak_512(msg) mod L] · base.

    Image is in <base> by construction — no cofactor clearing needed if
    `base` itself generates the prime-order subgroup (H does; see pedersen.py).
    """
    return base.mul(hash_to_scalar(data))


def cn_ptr(*items: bytes) -> bytes:
    """Concatenation hash used inside CLSAG challenge computation:
    H_p(item1 || item2 || ...) as a canonical, domain-separated helper."""
    return keccak_256(b"".join(items))
