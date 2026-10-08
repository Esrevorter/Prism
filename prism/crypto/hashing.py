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
_ROT = [
    [0, 36, 3, 41, 18],
    [1, 44, 10, 45, 2],
    [62, 6, 45, 16, 47],
    [28, 55, 43, 23, 39],
    [27, 20, 39, 7, 13],
]
_MASK = (1 << 64) - 1


def _rol(x: int, n: int) -> int:
    n %= 64
    return ((x << n) | (x >> (64 - n))) & _MASK


def _keccak_f(state: list[list[int]]) -> None:
    for rc in _RC:
        # theta
        c = [state[x][0] ^ state[x][1] ^ state[x][2] ^ state[x][3] ^ state[x][4]
             for x in range(5)]
        d = [c[(x - 1) % 5] ^ _rol(c[(x + 1) % 5], 1) for x in range(5)]
        for x in range(5):
            for y in range(5):
                state[x][y] ^= d[x]
        # rho + pi
        b = [[0] * 5 for _ in range(5)]
        for x in range(5):
            for y in range(5):
                b[y][(2 * x + 3 * y) % 5] = _rol(state[x][y], _ROT[x][y])
        # chi
        for x in range(5):
            for y in range(5):
                state[x][y] = b[x][y] ^ ((~b[(x + 1) % 5][y] & _MASK)
                                         & b[(x + 2) % 5][y])
        # iota
        state[0][0] ^= rc


def _keccak(data: bytes, out_len: int, pad: int) -> bytes:
    rate = 200 - 2 * out_len          # bytes absorbed per lane block
    st = [[0] * 5 for _ in range(5)]
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
            lane = int.from_bytes(block[8 * i:8 * i + 8], "little")
            st[i % 5][i // 5] ^= lane
        _keccak_f(st)
    # squeeze
    out = bytearray()
    while len(out) < out_len:
        for i in range(rate // 8):
            out += st[i % 5][i // 5].to_bytes(8, "little")
            if len(out) >= out_len:
                break
        if len(out) < out_len:
            _keccak_f(st)
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
