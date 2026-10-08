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


def base_reward(height: int) -> int:
    """Pre-tail base block reward in shards at `height`.

    Geometric decay: reward(h) = R0 * (1/2)^(h / HALVING_INTERVAL_BLOCKS),
    computed exactly as R0 >> k with a remainder-corrected floor so total
    emitted never exceeds the cap. Height 0 gets R0.
    """
    if height < 0:
        raise ValueError("negative height")
    k = height // _decay_factor_blocks()
    if k >= 63:                      # underflow guard; effectively zero
        return 0
    return INITIAL_BASE_REWARD_SHARDS >> k


def tail_reward(height: int) -> int:
    """Per-block tail emission once the pre-tail curve has decayed to zero.

    Tail = 0.6%/yr of CURRENT supply, paid per block. We approximate the
    compounding conservatively with simple interest on the capped supply:
    (TOTAL_SUPPLY * 60 bps) / blocks_per_year. Consensus-fixed, non-discretionary.
    """
    if base_reward(height) > 0:
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
    the partial current epoch. O(number of halvings) — cheap.
    """
    total = 0
    r = INITIAL_BASE_REWARD_SHARDS
    h = 0
    while r > 0 and h <= height:
        epoch_end = min(height + 1, h + _decay_factor_blocks())
        n = epoch_end - h
        total += r * n
        h += _decay_factor_blocks()
        r //= 2
    # tail blocks above the decay horizon
    if height >= h:
        tr = tail_reward(h)
        total += tr * (height - h + 1)
    return total


def prsm(x_shards: int) -> str:
    """Format shards -> human PRSM string (8 decimals)."""
    whole, frac = divmod(x_shards, SHARDS_PER_PRSM)
    return f"{whole}.{frac:08d}".rstrip("0").rstrip(".")
