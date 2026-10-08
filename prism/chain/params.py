"""Consensus parameters — spec.md v1.0 §4.1/§4.2, Decisions D1 and D5.

Units: the base unit is the *shard* (1 PRSM = 10^8 shards). All integer
economics in this module are denominated in shards.

Changing any value here is a consensus-breaking change and requires a
version-bit vote (§4.2 fork policy) plus an RFC.
"""
from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------- supply ---
TOTAL_SUPPLY_PRSM = 21_000_000                      # hard cap before tail (D1)
SHARDS_PER_PRSM = 10**8
TOTAL_SUPPLY_SHARDS = TOTAL_SUPPLY_PRSM * SHARDS_PER_PRSM

BLOCK_TIME_SECONDS = 120                            # §4.1
BLOCKS_PER_YEAR = (365 * 24 * 3600) // BLOCK_TIME_SECONDS   # 157,680
HALVING_YEARS = 2                                   # §4.1 emission curve
HALVING_INTERVAL_BLOCKS = HALVING_YEARS * BLOCKS_PER_YEAR   # 315,360

# RFC-0001 RESOLVED (2026-10-08, founder sign-off): the binding invariant is
# "halving every 2 years at a 120 s target cadence" = 315,360 blocks. The
# 787,750 figure in earlier spec drafts was Monero's number under its legacy
# 60 s block time and does not apply to Prism. spec.md §4.1 has been amended
# accordingly; this constant is now spec-exact.

TAIL_ANNUAL_RATE_BPS = 60                           # ≈0.6%/yr forever (D1)
DEV_FUND_SHARE_BPS = 500                            # 5% of each block reward
# Dev fund = carve-out of FUTURE emission only. Zero premine (D1). Genesis
# allocates nothing to team/foundation.

# ------------------------------------------------------------- difficulty ---
DIFF_WINDOW_BLOCKS = 60                             # retarget window (§4.1)
DIFF_CLAMP_NUM, DIFF_CLAMP_DEN = 101, 100           # ±1% per-retarget clamp
TIMESTAMP_TOLERANCE_BLOCKS = 2                      # §4.2

# ---------------------------------------------------------------- blocks ---
SOFT_TARGET_SIZE_MIN_BYTES = 2 * 1024 * 1024        # §4.1 dynamic size
SOFT_TARGET_SIZE_MAX_BYTES = 4 * 1024 * 1024
MEDIAN_LAST_N_FOR_SIZE = 10                         # median-last-10 scaling
SPEND_MATURITY_BLOCKS = 10                          # §4.1 locking

# ------------------------------------------------------------------- tx ---
RING_SIZE_MIN = 16                                  # §5.1
RING_SIZE_TARGET = 32
BASE_FEE_SHARDS = 10_000                            # 0.0001 PRSM (§ key feat 3)
PRIORITY_FEE_MAX_MULTIPLE = 10                      # capped at 10x base fee

# ------------------------------------------------------------ denylist ---
DENYLIST_WEEKLY_BLOCKS = (7 * 24 * 3600) // BLOCK_TIME_SECONDS   # 5,040 blocks
COUNCIL_SEATS = 7                                   # Decision D5
COUNCIL_QUORUM = 5                                  # ≥5-of-7 sigs per root
COUNCIL_TERM_MONTHS = 18                            # staggered terms

# ------------------------------------------------------------------ pow ---
FORK_VOTE_WINDOW_BLOCKS = 181                       # §4.2 version-bit voting
DANDELION_EPOCH_BLOCKS = 64                         # §4.2
DANDELION_EMBARGO_HOPS = (5, 8)                     # randomized range


@dataclass(frozen=True)
class NetworkParams:
    """Per-network overrides layered on the constants above."""
    name: str
    coinbase_maturity: int = SPEND_MATURITY_BLOCKS
    diff_window: int = DIFF_WINDOW_BLOCKS
    target_block_time: int = BLOCK_TIME_SECONDS
    genesis_timestamp: int = 0
    pow_placeholder: bool = True   # True until librandomx FFI lands (Phase 1)


MAINNET = NetworkParams(name="prism1", genesis_timestamp=1_762_000_000,
                        pow_placeholder=False)
REFRACTION_TESTNET = NetworkParams(  # §13 launch checklist: "Refraction"
    name="refraction",
    coinbase_maturity=2,
    diff_window=15,
    target_block_time=30,          # fast iteration on testnet
    genesis_timestamp=1_759_000_000,
    pow_placeholder=True,          # CPU-faucet PoW until RandomX FFI ships
)

NETWORKS = {p.name: p for p in (MAINNET, REFRACTION_TESTNET)}
