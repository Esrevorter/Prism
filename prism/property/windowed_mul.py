"""Windowed (Booth-free, width-4) fixed-base scalar multiplication for the
scaled spend-to-self property run.

The reference ladder (prism.crypto.edwards.Point.mul, double-and-add) runs
~290 muls/s; additions are ~115k/s.  A width-w windowed method needs only
64 table lookups + 64 adds per 256-bit scalar => ~3-5x fewer group ops and
no doublings per scalar after a one-time table build per worker process.

INDEPENDENCE: this is a different algorithm from the reference ladder
(window tables vs bitwise double-and-add).  The module self-verifies at
import against Point.mul on random scalars; any mismatch raises.  Every
iteration of the scaled run additionally cross-checks one commitment with
the reference ladder, so agreement is continuously differential-tested,
not assumed.
"""
from __future__ import annotations

import random

from prism.crypto.edwards import IDENTITY_POINT, Point
from prism.crypto.field import L
from prism.crypto.pedersen import G, H

W = 4
WINDOW = 1 << W          # 16
NWINDOWS = 256 // W      # 64


def _build_table(P: Point) -> list:
    """tbl[j][i] = [i * 16^j] P  for i in 0..15, j in 0..63."""
    tbl = []
    cur = P
    for _ in range(NWINDOWS):
        # compute [i]cur for i=1..15 by repeated addition of `cur`
        pt = IDENTITY_POINT
        row = [pt]
        for _i in range(1, WINDOW):
            pt = pt.add(cur)
            row.append(pt)
        tbl.append(row)
        # advance cur to [16]P_j via 4 doublings
        for _d in range(W):
            cur = cur.double()
    return tbl


class WindowedMul:
    def __init__(self, P: Point):
        self.tbl = _build_table(P)

    def mul(self, k: int) -> Point:
        k %= L
        acc = IDENTITY_POINT
        for j in range(NWINDOWS):
            digit = (k >> (W * j)) & (WINDOW - 1)
            if digit:
                acc = acc.add(self.tbl[j][digit])
        return acc


_GMUL = WindowedMul(G)
_HMUL = WindowedMul(H)


def commit_fast(value: int, mask: int) -> Point:
    """C(v, r) = [v]H + [r]G — same formula as pedersen.commit, faster path."""
    return _HMUL.mul(value).add(_GMUL.mul(mask % L))


# ---------------------------------------------------------------------------
# Import-time verification against the reference implementation.
# ---------------------------------------------------------------------------
def _verify() -> None:
    rng = random.Random(0xA11CE)
    for _ in range(6):
        v = rng.randrange(1, 1 << 40)
        r = rng.randrange(1, L)
        ref = H.mul(v).add(G.mul(r % L))
        fast = commit_fast(v, r)
        if ref.encode() != fast.encode():
            raise RuntimeError("windowed_mul: commit mismatch vs reference")
    for _ in range(6):
        k = rng.randrange(1, L)
        if G.mul(k).encode() != _GMUL.mul(k).encode():
            raise RuntimeError("windowed_mul: [k]G mismatch vs reference")


_verify()
