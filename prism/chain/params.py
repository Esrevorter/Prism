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
# D1 CONFLICT RESOLUTION (founder decision 2026-10-10, Option B):
# "raise the initial annual emission so the halving curve reaches 21M."
#
# The conflict: D1 says "halving curve until the 21M cap is approached, then
# constant ≈0.6%/yr tail". A pure geometric halving curve has finite plateau
# S∞ = 2·U where U = R0·HI is one epoch's total issuance; it can only ever
# "approach" the cap if S∞ ≥ APPROACH threshold. Under the old parameters
# (R0 ≈ 2.1M PRSM/yr, halving every 315,360 blocks = 2 years) the plateau
# was 2U ≈ 8.4M PRSM < 20.79M (99% of cap) — unreachable, so the curve ran
# forever without transitioning (D1 CONFLICT FLAG raised 2026-10-10).
# An intermediate revision first tried shortening the halving interval
# (option (c), HI = 780,517 blocks); the founder subsequently overrode that
# with Option B, which keeps the RFC-0001 2-year cadence and instead raises
# the launch emission rate.
#
# Derivation (exact integer math, Option B):
#   plateau S∞ = 2·U = 2·R0·HI must reach TARGET = CAP·APPROACH_BPS/10⁴
#   with HI = 315,360 (2 yr @ 120 s, RFC-0001) and TARGET = 20.79M PRSM:
#     R0 ≥ TARGET / (2·HI) = 32,955.7... shards/block
#          ≈ 10.4998M PRSM per calendar year — the founder's "~10.5M/yr"
#          figure (= cap/2). Policy constant below: 10_500_000 PRSM/yr.
#   emission.py realises it exactly in whole shards/block via ceiling
#   division on the epoch target U = TARGET/2:
#     R0 = ceil(TARGET / (2·HI)) = 32,956 shards/block, so the exact epoch
#     total HI·R0 clears TARGET/2 and the plateau 2·U ≥ TARGET always holds
#     (transition guaranteed to fire; overshoot < 1 micro-PRSM).
#   Calendar-year equivalent: 32,956 × 157,680 = 10,500,328.08 PRSM/yr —
#   within 328 PRSM/yr (0.003%) of the 10.5M policy figure.
# With these constants the curve crosses 99% of the cap at epoch K = 7
# (height 2,207,520 ≈ year 14); cumulative pre-tail supply there is
# ≈20,790,000 PRSM (< 21M by construction — the 99% approach threshold
# leaves ~210K PRSM of headroom), after which the pure constant tail
# (TAIL_BLOCK_SHARDS = cap·60bps/blocks_per_year, ≈0.6%/yr on the 21M
# reference) takes over forever. Pre-tail supply never exceeds
# TOTAL_SUPPLY_SHARDS, and the blended-tail fallback in emission.py remains
# as a safety net should any future parameterisation make the cap
# unreachable again.
# spec.md §4.1/D1/§13/§14 amended accordingly (RFC process, 2026-10-10).
HALVING_YEARS_LABEL = "2 (315,360 blocks @ 120 s)"  # informational only
# RFC-0001 RESOLVED (2026-10-08): halving every 2 years at the 120 s target
# cadence = 315,360 blocks (the 787,750 figure in early drafts was Monero's
# number under its legacy 60 s block time and does not apply to Prism).
# Option B restores this cadence; the cap-reachability burden moves entirely
# onto the initial emission rate (INITIAL_ANNUAL_EMISSION_PRSM below).
HALVING_INTERVAL_BLOCKS = 315_360                   # 2 years @ 120 s (RFC-0001)

#: Founder-policy initial annual emission at launch, in PRSM (Option B,
#: 2026-10-10: cap/2 = 10.5M PRSM/yr). Chosen so the halving plateau
#: 2·U = 2·R0·HI reaches 99% of the 21M cap (exact minimum ≈ 10.4998M/yr).
#: The consensus per-block reward is derived from this figure in
#: emission.py (ceil to whole shards); realised rate lands within 0.003%.
INITIAL_ANNUAL_EMISSION_PRSM = TOTAL_SUPPLY_PRSM // 2   # 10,500,000 PRSM/yr

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
