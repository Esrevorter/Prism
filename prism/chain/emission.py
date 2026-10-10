"""Emission schedule — spec.md v1.0 §4.1, Decision D1.

Model: discrete geometric decay (Monero-style base reward), then a
consensus-fixed tail of ≈0.6%/yr once the pre-tail supply approaches 21M.
Zero premine; dev fund is a fixed carve-out of each block reward.

RFC-0001 (RESOLVED 2026-10-08): halving interval = 315,360 blocks
(2 years at the 120 s target cadence). The ~787,750-block figure in early
spec drafts was Monero's number under its legacy 60 s block time and does
not apply to Prism. spec.md §4.1 has been amended; code and spec are now
exact on this constant.

All values are exact integers (shards). No floats in consensus code.
"""
from __future__ import annotations

from dataclasses import dataclass

from .params import (
    DEV_FUND_SHARE_BPS,
    HALVING_INTERVAL_BLOCKS,
    TAIL_ANNUAL_RATE_BPS,
    BLOCKS_PER_YEAR,
    TOTAL_SUPPLY_SHARDS,
    SHARDS_PER_PRSM,
)

# Initial annual emission target: ~2.1M PRSM/yr at launch so the bulk of the
# 21M cap is emitted over the first several halvings (base_reward * blocks/yr).
INITIAL_BASE_REWARD_SHARDS = (2_100_000 * SHARDS_PER_PRSM) // BLOCKS_PER_YEAR


def _decay_factor_blocks() -> int:
    """Blocks per halving, as an integer decay period."""
    return HALVING_INTERVAL_BLOCKS


# Cap-aware transition height: the first halving-epoch boundary at which the
# cumulative pre-tail emission has reached/approached the 21M hard cap; from
# there on, the constant tail takes over. Closed-form derivation (no search
# loop, so O(1) and safe inside the per-block consensus path):
#
#   Heights are 1-indexed for rewards (genesis pays nobody), so epoch k
#   covers heights [k*HI + 1, (k+1)*HI] and pays R0 >> k per block. The
#   cumulative sum through the end of epoch K-1 is  S(K) = U * (2 - 2^(1-K)),
#   where U = R0 * HI (the first epoch's total) and 2*U = 2S(∞) is the
#   supremum of the geometric series. The smallest K with S(K) >= CAP obeys
#       U >> (K-1)  <=  (2U - CAP) / 2      (integer division, exact bound)
#   so K = 1 + bitlen(U) - bitlen(2U - CAP) when 2U > CAP, else K = 1.
#
# Note: R0 is floor-rounded to whole shards, so U < CAP/2 and the closed-form
# transition epoch is 1 here — i.e. the decay curve alone never overshoots the
# cap and the tail begins immediately after the first halving interval. If a
# future parameter change ever makes 2U > CAP (curve would run past the cap),
# this formula stops the curve early instead.
_U_FIRST_EPOCH = INITIAL_BASE_REWARD_SHARDS * HALVING_INTERVAL_BLOCKS
_TWO_U = 2 * _U_FIRST_EPOCH


def _cap_transition_epoch() -> int:
    if _TWO_U <= TOTAL_SUPPLY_SHARDS:      # decay sum alone stays under cap
        return 1                            # tail starts at the first boundary
    slack = (_TWO_U - TOTAL_SUPPLY_SHARDS) // 2
    return 1 + _U_FIRST_EPOCH.bit_length() - slack.bit_length()


CAP_TRANSITION_EPOCH = _cap_transition_epoch()
#: First height at which the pre-tail curve has reached/approached the cap and
#: the constant tail takes over (spec §4.1 "until the 21M cap is approached").
CAP_HEIGHT = CAP_TRANSITION_EPOCH * HALVING_INTERVAL_BLOCKS


def base_reward(height: int) -> int:
    """Pre-tail base block reward in shards at `height`.

    Geometric decay: reward(h) = R0 * (1/2)^(h / HALVING_INTERVAL_BLOCKS),
    computed exactly as R0 >> k. The curve stops at the cap-approach boundary
    (CAP_HEIGHT): beyond it the base reward is 0 and the consensus-fixed tail
    takes over, so total issuance converges toward — and never runs away past
    — the 21M hard cap (spec §4.1, Decision D1). Height 0 (genesis) pays
    nobody; the first reward-bearing block is height 1.
    """
    if height < 0:
        raise ValueError("negative height")
    if height == 0:
        return 0                            # genesis pays nobody (D1, zero premine)
    k = height // _decay_factor_blocks()
    if k >= CAP_TRANSITION_EPOCH:    # cap approached; tail phase takes over
        return 0
    return INITIAL_BASE_REWARD_SHARDS >> k


def tail_reward(height: int) -> int:
    """Per-block tail emission once the pre-tail curve has reached the cap.

    Tail = 0.6%/yr of CURRENT supply, paid per block. We approximate the
    compounding conservatively with simple interest on the capped supply:
    (TOTAL_SUPPLY * 60 bps) / blocks_per_year. Consensus-fixed, non-discretionary.
    Genesis (height 0) pays nobody.
    """
    if height <= 0:
        return 0
    if height < CAP_HEIGHT:
        return 0
    return (TOTAL_SUPPLY_SHARDS * TAIL_ANNUAL_RATE_BPS) // (10_000 * BLOCKS_PER_YEAR)


@dataclass(frozen=True)
class BlockEmission:
    height: int
    base_shards: int
    tail_shards: int
    miner_shares: int     # after dev-fund carve-out
    dev_fund_shares: int

    @property
    def total_new_shards(self) -> int:
        return self.base_shards + self.tail_shards


def emission_at(height: int) -> BlockEmission:
    b, t = base_reward(height), tail_reward(height)
    total = b + t
    dev = (total * DEV_FUND_SHARE_BPS) // 10_000
    return BlockEmission(height, b, t, total - dev, dev)


def cumulative_supply(height: int) -> int:
    """Total shards emitted through block `height` inclusive.

    Closed form for the decay phase: sum over complete halving epochs plus
    the partial current epoch, then a constant tail above CAP_HEIGHT.
    O(CAP_TRANSITION_EPOCH) — cheap and bounded. Genesis (height 0) pays
    nobody, so the sum runs over reward-bearing heights 1..height.
    """
    if height < 0:
        raise ValueError("negative height")
    hi = HALVING_INTERVAL_BLOCKS
    # base_reward(h) pays R0 >> (h // hi) for 1 <= h < CAP_HEIGHT, so the
    # reward-bearing heights of decay epoch j are [j*hi + 1, (j+1)*hi] — all
    # hi blocks — except the final epoch K-1, whose block at height K*hi ==
    # CAP_HEIGHT is tail-only (base_reward returns 0 there). Genesis (height
    # 0) pays nobody. completed = number of fully finished decay epochs.
    completed = min(height // hi, CAP_TRANSITION_EPOCH)
    total = 0
    if completed >= 1:
        for j in range(completed):
            n = hi - (1 if j == CAP_TRANSITION_EPOCH - 1 else 0)
            total += (INITIAL_BASE_REWARD_SHARDS >> j) * n
    elif height > 0:
        # still inside epoch 0: heights 1..height pay R0 (height 0 pays none)
        return INITIAL_BASE_REWARD_SHARDS * height
    # partial current decay epoch (only while still in the decay phase)
    if completed < CAP_TRANSITION_EPOCH:
        first_uncounted = completed * hi + 1
        if height >= first_uncounted:
            total += base_reward(first_uncounted) * (height - first_uncounted + 1)
    # constant tail from CAP_HEIGHT onward (same value as tail_reward())
    if height >= CAP_HEIGHT:
        tr = tail_reward(CAP_HEIGHT)
        total += tr * (height - CAP_HEIGHT + 1)
    return total


def prsm(x_shards: int) -> str:
    """Format shards -> human PRSM string (8 decimals)."""
    whole, frac = divmod(x_shards, SHARDS_PER_PRSM)
    return f"{whole}.{frac:08d}".rstrip("0").rstrip(".")
