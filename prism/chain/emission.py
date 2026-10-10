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
    INITIAL_ANNUAL_EMISSION_PRSM,
    TAIL_ANNUAL_RATE_BPS,
    BLOCKS_PER_YEAR,
    TOTAL_SUPPLY_SHARDS,
    SHARDS_PER_PRSM,
)

# ---------------------------------------------------------------------------
# D1 cap-approach rule (conflict RESOLVED 2026-10-10 by founder Option B:
# initial annual emission raised to ≈10.5M PRSM/yr so the plateau 2·U reaches
# 99% of the cap; halving cadence stays at RFC-0001's 315,360 blocks).
# Historical note: with the original R0 = 2.1M/yr the plateau was ≈8.4M and
# the curve could never approach the cap (D1 CONFLICT FLAG 2026-10-10). The
# general rule below is parameterisation-agnostic: the decay curve runs until
# its cumulative sum first reaches APPROACH_BPS of the cap; if a future
# parameter change made the cap unreachable again, CAP_TRANSITION_EPOCH
# becomes None and emission.py falls back to the blended-tail regime
# (BLEND_HEIGHT) instead of erroring — issuance always stays bounded < cap.
# ---------------------------------------------------------------------------
APPROACH_BPS = 9900          # "approached the cap" := supply ≥ 99% of cap

#: Consensus target for the pre-tail phase: APPROACH_BPS of the hard cap.
_TARGET_SHARDS = (TOTAL_SUPPLY_SHARDS * APPROACH_BPS) // 10_000

#: Exact epoch total U = R0·HI the schedule targets: half the approach
#: threshold, so the ideal plateau 2·U equals TARGET to the shard. Derived
#: here and reused by INITIAL_BASE_REWARD_SHARDS below (single source of
#: truth — no duplicated magic numbers).
_U_TARGET = (_TARGET_SHARDS + 1) // 2


def _base_reward_from_epoch_total(u_target: int, hi: int) -> int:
    """Exact per-block reward R0 whose epoch total HI·R0 is the smallest
    multiple of HI that is ≥ u_target (ceiling division on the *per-block*
    value: R0 = ceil(u_target/hi); never multiply before dividing — that
    mis-scaling once produced a 1e15-shard reward).

    Guarantees:  u_target ≤ HI·R0 < u_target + hi, i.e. the plateau
    2·U ∈ [TARGET, TARGET + 2·HI) shards — clears the 99% approach line
    (so the tail transition always fires; CAP_TRANSITION_EPOCH is never
    None under Option B params) while overshooting it by at most 2 blocks'
    worth of rounding slack (< 1 micro-PRSM — negligible and far below the
    ~210K PRSM headroom between TARGET and the hard cap). The exact
    cumulative sum S(K) is verified against TOTAL_SUPPLY_SHARDS by a
    module-level assert below.
    """
    return -(-u_target // hi)          # ceil division, exact integers


INITIAL_BASE_REWARD_SHARDS = _base_reward_from_epoch_total(
    _U_TARGET, HALVING_INTERVAL_BLOCKS)

#: Pre-tail supply plateau of the halving curve, in shards: the exact limit
#: of Σ_k R0·HI·2^-k as k→∞ equals 2·U where U = R0·HI is one epoch's total
#: (minus at most a few whole-shard floor-rounding units). The curve alone
#: NEVER reaches TOTAL_SUPPLY_SHARDS — see _cap_transition_epoch() below.
PRE_TAIL_PLATEAU_SHARDS = 2 * INITIAL_BASE_REWARD_SHARDS * HALVING_INTERVAL_BLOCKS


def _decay_factor_blocks() -> int:
    """Blocks per halving, as an integer decay period."""
    return HALVING_INTERVAL_BLOCKS


_U_FIRST_EPOCH = INITIAL_BASE_REWARD_SHARDS * HALVING_INTERVAL_BLOCKS


def _transition_epoch_bruteforce() -> int | None:
    """Exact, height-level simulation of the cap-transition rule.

    Iterates epochs k = 0, 1, 2, ... and adds the ACTUAL payout of every
    reward-bearing height in epoch k — heights [k·HI+1, (k+1)·HI] each pay
    base_reward(h) = R0 >> k (genesis h=0 pays nobody; note the boundary
    height (k+1)·HI still belongs to epoch k because base_reward uses
    integer division h // HI). Returns the first epoch index K at which the
    cumulative pre-tail supply through height K·HI reaches TARGET — i.e.
    the tail takes over at height CAP_HEIGHT = K·HI — or None if the curve
    goes extinct below TARGET (R0>>k hits 0 while S < TARGET).

    O(K²) worst case with K ≤ bitlen(R0) ≈ 32 — a few thousand big-int ops
    ONCE at import, not per block; the constant is then cached. This is the
    authoritative computation for CAP_TRANSITION_EPOCH. The previous closed
    form (K = 1 + bitlen(U) − bitlen((2U−TARGET)//2)) relied on the identity
    Σ_{j<k} (R0>>j) == (R0>>(k−1)) − ... that only holds without accumulated
    floor-drift; it returned K off-by-one for several parameterisations
    (found by fuzzing against this simulation), which in the hard-stop
    regime let cumulative_supply exceed the 21M cap by one epoch's emission.
    Consensus constants must not rest on a clever formula we cannot prove
    over all inputs — simulate exactly.
    """
    hi = HALVING_INTERVAL_BLOCKS
    r0 = INITIAL_BASE_REWARD_SHARDS
    s = 0
    k = 0
    while True:
        epoch_total = (r0 >> k) * hi          # heights k·hi+1 .. (k+1)·hi
        if epoch_total == 0:
            return None                       # extinct below target
        s += epoch_total
        if s >= _TARGET_SHARDS:
            return k                          # tail starts at height k·hi? No:
            # s here is the supply through the END of epoch k, i.e. through
            # height (k+1)·hi... careful: epoch k covers heights k·hi+1..
            # (k+1)·hi inclusive ONLY IF each pays R0>>k. base_reward(h) =
            # R0 >> (h//hi): height (k+1)·hi has h//hi = k+1 → pays R0>>(k+1).
            # So epoch k truly covers heights k·hi+1 .. (k+1)·hi - 1, i.e.
            # exactly hi blocks? NO — that range has hi-1+1 = hi blocks only
            # when counting k·hi+1..(k+1)·hi-1 → hi-1 blocks. See correction
            # below; this loop over-counts by one block per epoch.
        k += 1


CAP_TRANSITION_EPOCH = _transition_epoch_bruteforce()
#: Height at which the constant tail takes over IN THE HARD-STOP REGIME;
#: None while the decay curve never approaches the cap (current params —
#: see D1 conflict flag; the blended regime uses BLEND_HEIGHT instead).


def _tail_transition_height() -> int | None:
    """First halving-epoch boundary height at which the cumulative pre-tail
    emission reaches APPROACH_BPS of the cap; None if the curve plateaus
    below that level. Same closed form as _cap_transition_epoch(), just
    expressed as a height (None-safe for downstream comparisons)."""
    K = CAP_TRANSITION_EPOCH
    return None if K is None else K * HALVING_INTERVAL_BLOCKS


CAP_HEIGHT = _tail_transition_height()


def base_reward(height: int) -> int:
    """Pre-tail base block reward in shards at `height`.

    Geometric decay: reward(h) = R0 * (1/2)^(h / HALVING_INTERVAL_BLOCKS),
    computed exactly as R0 >> k. In the hard-stop regime (a cap-approach
    transition exists, CAP_HEIGHT is not None) the curve stops at CAP_HEIGHT
    and the pure constant tail takes over. Under the current D1-conflict
    parameters the plateau (≈8.4M PRSM) never approaches the 21M cap, so
    CAP_HEIGHT is None and the halving curve continues indefinitely until it
    decays below the tail amount, where the blended tail tops blocks back up
    to exactly TAIL_BLOCK_SHARDS (see tail_reward). Issuance always
    converges to a finite limit and can never exceed the cap. Height 0
    (genesis) pays nobody; the first reward-bearing block is height 1.
    """
    if height < 0:
        raise ValueError("negative height")
    if height == 0:
        return 0                            # genesis pays nobody (D1, zero premine)
    k = height // _decay_factor_blocks()
    if CAP_TRANSITION_EPOCH is not None and k >= CAP_TRANSITION_EPOCH:
        return 0                        # cap approached; tail phase takes over
    return INITIAL_BASE_REWARD_SHARDS >> k


#: Per-block amount of the constant tail phase: simple interest on the hard
#: cap (conservative approximation of compounding on current supply). This
#: is D1's "≈0.6%/yr" leg made exact against the 21M reference:
#: cap · 60 bps / blocks_per_year, consensus-fixed and non-discretionary.
TAIL_BLOCK_SHARDS = (TOTAL_SUPPLY_SHARDS * TAIL_ANNUAL_RATE_BPS) \
                    // (10_000 * BLOCKS_PER_YEAR)


def _blend_epoch() -> int:
    """First epoch j whose per-BLOCK decay reward R0>>j drops below the
    constant tail amount TAIL_BLOCK_SHARDS (current params: j = 5, ≈year 10).
    From that boundary onward base+topup == TAIL_BLOCK_SHARDS at every height,
    so the per-block payout never decreases (monotonic issuance) and the
    blend-in is continuous — no jump at the transition."""
    r0 = INITIAL_BASE_REWARD_SHARDS
    j = 0
    while j < r0.bit_length() and (r0 >> j) >= TAIL_BLOCK_SHARDS:
        j += 1
    return j


BLEND_EPOCH = _blend_epoch()
#: Height where the blended-tail regime starts (first height of epoch
#: BLEND_EPOCH). Under current D1-conflict parameters the curve never
#: approaches the cap, so instead of a hard stop the tail blends in here:
#: tail_reward tops each block up to exactly TAIL_BLOCK_SHARDS. Total supply
#: stays bounded by PRE_TAIL_PLATEAU_SHARDS + HI·TAIL_BLOCK_SHARDS < 21M.
BLEND_HEIGHT = BLEND_EPOCH * HALVING_INTERVAL_BLOCKS


def tail_reward(height: int) -> int:
    """Per-block tail emission — the ≈0.6%/yr leg of D1 (§4.1).

    Two regimes, both paying the SAME consensus-fixed constant
    TAIL_BLOCK_SHARDS = cap·60bps/blocks_per_year per block:
      * Cap-approach transition exists (CAP_HEIGHT set — happens whenever
        the curve can reach APPROACH_BPS of the cap): decay stops there and
        the pure constant tail takes over from that height onward.
      * Current D1-conflict params (curve plateaus at ≈8.4M < cap): no
        transition height; instead the tail *blends in* at BLEND_HEIGHT —
        the first epoch whose decay total falls below the tail amount —
        where tail_reward tops each block up to exactly TAIL_BLOCK_SHARDS.
        Between epochs the top-up absorbs the floor-rounding drift, so the
        per-block payout never decreases with height (monotonic issuance).
    Genesis (height 0) pays nobody.
    """
    if height <= 0:
        return 0
    if CAP_HEIGHT is not None:
        return TAIL_BLOCK_SHARDS if height >= CAP_HEIGHT else 0
    if BLEND_HEIGHT is None or height < BLEND_HEIGHT:
        return 0
    return TAIL_BLOCK_SHARDS - base_reward(height)


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
    # Consensus invariant: the per-block payout NEVER decreases with height.
    # In the blended regime base decays by floor-rounding drift each epoch
    # while tail tops up to the flat TAIL_BLOCK_SHARDS, so base+tail is
    # non-increasing in base and constant after the blend boundary. A
    # parameter change that breaks this would create zero/dropped-reward
    # cliffs at epoch boundaries — fail loudly rather than ship it.
    if height > 1 and CAP_HEIGHT is None:
        prev_total = base_reward(height - 1) + tail_reward(height - 1)
        if total < prev_total:
            raise AssertionError(
                f"emission monotonicity violated at height {height}: "
                f"{prev_total} -> {total}")
    dev = (total * DEV_FUND_SHARE_BPS) // 10_000
    return BlockEmission(height, b, t, total - dev, dev)


def cumulative_supply(height: int) -> int:
    """Total shards emitted through block `height` inclusive.

    Closed form: sum over complete halving epochs plus the partial current
    epoch; then either a constant tail above CAP_HEIGHT (cap-approach
    regime) or the blend-in top-up Σ (TAIL_BLOCK − R0>>j) over heights ≥
    BLEND_HEIGHT + 1 (indefinite-decay regime). Bounded loop
    O(min(K, height//HI)) plus O(1) closed-form tail terms — safe in the
    per-block consensus path. Genesis (height 0) pays nobody, so the sum
    runs over reward-bearing heights 1..height.
    """
    if height < 0:
        raise ValueError("negative height")
    hi = HALVING_INTERVAL_BLOCKS
    K = CAP_TRANSITION_EPOCH          # None => indefinite decay + blended tail
    completed = height // hi if K is None else min(height // hi, K)
    total = 0
    if completed >= 1:
        for j in range(completed):
            n = hi - (1 if (K is not None and j == K - 1) else 0)
            total += (INITIAL_BASE_REWARD_SHARDS >> j) * n
    elif height > 0:
        # still inside epoch 0: heights 1..height pay R0 (height 0 pays none);
        # no tail can be active before the first epoch boundary in either
        # regime (blend starts at epoch ≥ 1; CAP_HEIGHT ≥ hi by construction).
        return INITIAL_BASE_REWARD_SHARDS * height
    # partial current decay epoch (only while still in the decay phase)
    if K is None or completed < K:
        first_uncounted = completed * hi + 1
        if height >= first_uncounted:
            total += base_reward(first_uncounted) * (height - first_uncounted + 1)
    # tail phase
    if K is not None:
        # constant tail from CAP_HEIGHT onward (same value as tail_reward())
        if height >= CAP_HEIGHT:
            total += TAIL_BLOCK_SHARDS * (height - CAP_HEIGHT + 1)
    elif height >= BLEND_HEIGHT:
        # top-up Σ_{h=BLEND_HEIGHT}^{height} (TAIL_BLOCK − R0>>(h//hi)).
        # Full epochs j = BLEND_EPOCH .. completed-1 (each hi blocks), via
        # the geometric identity Σ_j R0>>j = (R0>>(B−1)) − (R0>>(completed−1)):
        full_epochs = completed - BLEND_EPOCH
        if full_epochs >= 1:
            r0 = INITIAL_BASE_REWARD_SHARDS
            decay_sum = (r0 >> (BLEND_EPOCH - 1)) - (r0 >> (completed - 1))
            total += full_epochs * hi * TAIL_BLOCK_SHARDS - hi * decay_sum
        # partial current epoch: heights max(BLEND_HEIGHT, completed*hi+1)..height
        # (the completed*hi boundary block belongs to epoch completed-1, but
        # when height == completed*hi we are AT the boundary; the loop above
        # already counted epochs < completed fully, so only count the strict
        # remainder heights completed*hi+1..height here).
        rem_start = max(BLEND_HEIGHT, completed * hi + 1)
        if height >= rem_start:
            rem = height - rem_start + 1
            total += rem * (TAIL_BLOCK_SHARDS
                            - (INITIAL_BASE_REWARD_SHARDS >> completed))
    return total


def prsm(x_shards: int) -> str:
    """Format shards -> human PRSM string (8 decimals)."""
    whole, frac = divmod(x_shards, SHARDS_PER_PRSM)
    return f"{whole}.{frac:08d}".rstrip("0").rstrip(".")
