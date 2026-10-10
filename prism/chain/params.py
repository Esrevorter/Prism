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
# D1 CONFLICT RESOLUTION (founder decision 2026-10-10, option (c)):
# "shorten the halving interval."
#
# The conflict: D1 says "halving curve until the 21M cap is approached, then
# constant ≈0.6%/yr tail". A pure geometric halving curve has finite plateau
# S∞ = 2·U where U = R0·HI is one epoch's total issuance; it can only ever
# "approach" the cap if S∞ ≥ APPROACH threshold. Under the old parameters
# (per-block reward R0 ≈ 2.1M PRSM/yr equivalent, halving every 2 years) the
# plateau was 2U ≈ 8.4M PRSM < 20.79M (99% of cap) — unreachable, so the
# curve would run forever and never transition (see git history, D1 CONFLICT
# FLAG raised 2026-10-10).
#
# Resolution chosen by the founder: shorten the halving interval. The other
# half of option (c) — implicit in the goal "halving curve reaches the cap" —
# is that the per-BLOCK reward must stay at its spec value (R0 = 2.1M
# PRSM/yr equivalent, i.e. 1,331,811,263 shards/block). Halving more often
# then stretches the curve over more calendar time, raising the epoch-total
# plateau S∞ = 2·R0·HI toward the cap. Derivation (exact integer math):
#   need  S∞ = 2·R0·HI ≥ TARGET = CAP · APPROACH_BPS / 10⁴ = 20.79M PRSM
#   =>    HI ≥ TARGET / (2·R0) = 780,516.000… blocks
#   smallest whole-block HI satisfying it: 780,517 (= 4.9560… yr, ~14.87 mo).
# With HI = 780,517 the curve crosses 99% of the cap at epoch K = 6
# (height 4,683,102 ≈ year 29.7): cumulative pre-tail supply there is
# 20,790,000.00000196 PRSM ≥ target, after which the pure constant tail
# (TAIL_BLOCK_SHARDS, ≈0.6%/yr on the 21M reference) takes over forever.
# Supply never exceeds TOTAL_SUPPLY_SHARDS before the tail leg, and the
# blended-tail fallback in emission.py remains as a safety net should any
# future parameterisation make the cap unreachable again.
# spec.md §4.1/D1/§13/§14 amended accordingly (RFC process, 2026-10-10).
HALVING_YEARS_LABEL = "≈4.956 (14.87 months)"       # informational only
HALVING_INTERVAL_BLOCKS = 780_517                   # D1-c: exact minimum HI
                                                    # whose plateau 2·R0·HI
                                                    # reaches 99% of the cap

# History: RFC-0001 RESOLVED (2026-10-08) pinned "halving every 2 years at a
# 120 s target cadence" = 315,360 blocks (the 787,750 figure in early drafts
# was Monero's number under its legacy 60 s block time). That interval made
# D1's "halving curve → 21M cap + tail" unsatisfiable (plateau ≈ 8.4M < 99%
# cap); on 2026-10-10 the founder resolved the conflict via option (c):
# shorten the halving interval. NOTE ON WORDING: relative to the 2-year
# RFC-0001 draft this interval is longer in calendar terms but far shorter
# *per unit of emitted supply* — each halving now releases only ≈1.05M PRSM
# (vs 4.2M), i.e. the emission schedule is compressed into finer steps so
# the cap becomes reachable. See block comment above for the derivation.

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
