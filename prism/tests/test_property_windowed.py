"""Differential test: windowed fixed-base mul vs the reference ladder.

Runs prism.property.windowed_mul (width-4 window tables) against
prism.crypto.pedersen.commit / Point.mul (bitwise double-and-add) on
random inputs, including adversarial scalars (0, 1, L-1, multiples of
the cofactor, values near 2^64).  This is what licenses using the fast
path for the >=10^6 scaled property run required by spec §5.1.
"""
from __future__ import annotations

import random

from prism.crypto.edwards import IDENTITY_POINT
from prism.crypto.field import L
from prism.crypto.pedersen import G, H, commit as ref_commit
from prism.crypto.edwards import encode
from prism.property.windowed_mul import WindowedMul, commit_fast


def _eq(a, b) -> bool:
    return encode(a) == encode(b)


def test_windowed_vs_reference_commits():
    rng = random.Random(0xD1FF)
    for _ in range(200):
        v = rng.randrange(0, 1 << 64)
        r = rng.randrange(0, L)
        assert _eq(commit_fast(v, r), ref_commit(v, r))


def test_windowed_adversarial_scalars():
    vals = [0, 1, 2, L - 1, L, L + 1, (1 << 64) - 1, 1 << 64,
            8, 8 * ((L - 1) // 8), L // 2]
    for v in vals:
        assert _eq(WindowedMul(H).mul(v), H.mul(v)), f"H mismatch at {v}"
        assert _eq(WindowedMul(G).mul(v), G.mul(v)), f"G mismatch at {v}"


def test_windowed_zero_and_order():
    assert commit_fast(0, 0).is_identity()
    # scalar reduced mod L: [L]P == identity
    assert WindowedMul(G).mul(L).is_identity()
    assert WindowedMul(G).mul(-1 % L).add(G).is_identity()


def test_random_group_law_agreement():
    """Random sums of commitments agree between both paths."""
    rng = random.Random(0xC0DE)
    for _ in range(60):
        n = rng.randint(2, 5)
        pairs = [(rng.randrange(1, 1 << 40), rng.randrange(1, L))
                 for _ in range(n)]
        ref = IDENTITY_POINT
        fast = IDENTITY_POINT
        for v, r in pairs:
            ref = ref.add(ref_commit(v, r))
            fast = fast.add(commit_fast(v, r))
        assert _eq(ref, fast)
