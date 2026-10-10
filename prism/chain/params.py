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
#: Calendar-year block count at the 120 s target cadence:
#: 365 × 24 × 3600 / 120 = 31,536,000 / 120 = 262,800 blocks/year.
BLOCKS_PER_YEAR = (365 * 24 * 3600) // BLOCK_TIME_SECONDS   # 262,800
# D1 CONFLICT RESOLUTION (founder decision 2026-10-10, Option B):
# "shorten the halving interval" was evaluated and rejected — the plateau of
# a halving curve, S∞ = 2·U (U = R0·HI, one epoch's issuance), depends only
# on the launch rate, not the interval. Option B therefore keeps the RFC-0001
# 2-year cadence and raises the launch rate so the decay + tail schedule
# carries cumulative supply through exactly 21,000,000 PRSM.
#
# Derivation (exact integer math; verified by height-level simulation in
# tests/test_chain.py, no closed-form shortcuts):
#   * One halving epoch spans TWO calendar years (HI = 315,360 blocks at the
#     120 s cadence; BPY = 262,800 blocks/year), so the founder's "≈10.5M
#     PRSM/yr = cap/2" policy figure is read as EPOCH-scale guidance and
#     implemented through a 99% approach target: R0 is the smallest whole-
#     shard reward whose ideal halving plateau 2·U reaches 99% of the cap —
#         R0 = ceil(CAP · 9900 / (2 · HI · 10^4)) = 3,296,232,877 shards/block
#     giving epoch-0 issuance U ≈ 10.395M PRSM (within 1% of the cap/2
#     policy figure) and a calendar-year rate of ≈5.2M PRSM/yr at launch,
#     halving every two years thereafter.
#   * Plateau: S∞ = 2·U ≈ 20.79M PRSM clears the 99% approach target but
#     stays under the cap; floor drift across successive right-shifts keeps
#     every finite partial sum strictly below it, so pure decay can never
#     breach 21M (asserted at import).
#   * Tail handover: because the integer decay sum never reaches the
#     approach target, no epoch-boundary transition exists
#     (CAP_TRANSITION_EPOCH/CAP_HEIGHT are None) and the blend rule in
#     emission.py takes over at epoch j = 7 (first epoch whose decayed base
#     falls below TAIL_BLOCK_SHARDS); every block from BLEND_HEIGHT pays
#     exactly TAIL_BLOCK_SHARDS. Supply crosses the 21M reference inside
#     blended epoch 7 (CAP_CROSS_HEIGHT = 2,984,354, year ≈ 11.4) and
#     continues at the fixed ≈0.6%/yr tail forever
#     (Monero precedent: long-run security never depends on fees alone;
#     the cap is the decay curve's approach point, not a ceiling on the
#     non-discretionary tail leg of D1).
# spec.md §4.1/D1/§13/§14 amended accordingly (RFC process, 2026-10-10).
HALVING_YEARS_LABEL = "2 (315,360 blocks @ 120 s)"  # informational only
# RFC-0001 RESOLVED (2026-10-08): halving every 2 years at the 120 s target
# cadence = 315,360 blocks (the 787,750 figure in early drafts was Monero's
# number under its legacy 60 s block time and does not apply to Prism).
# Option B restores this cadence; the cap-reachability burden moves entirely
# onto the initial emission rate (INITIAL_ANNUAL_EMISSION_PRSM below).
HALVING_INTERVAL_BLOCKS = 315_360                   # 2 years @ 120 s (RFC-0001)

#: Founder-policy launch emission scale, in PRSM (Option B, 2026-10-10:
#: cap/2 = 10.5M). Read as EPOCH-scale guidance: the consensus per-block
#: reward R0 is sized so epoch-0 issuance lands within 1% of this figure
#: and the halving plateau 2·U reaches the 99% approach target of the cap
#: (see derivation above). emission.py pins the contract with import-time
#: asserts; realised drift < 1%.
INITIAL_ANNUAL_EMISSION_PRSM = TOTAL_SUPPLY_PRSM // 2   # 10,500,000 PRSM

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
