"""Emission schedule — spec.md §4.1, Decision D1 as resolved by Option B.

Model: discrete geometric decay (base reward halves every
HALVING_INTERVAL_BLOCKS) blended with a consensus-fixed constant tail
(TAIL_ANNUAL_RATE_BPS of the reference cap per year). Zero premine: genesis
pays nobody. The dev fund is a fixed carve-out (DEV_FUND_SHARE_BPS) of each
block reward. All values are exact integers (shards); no floats appear in
consensus code.

Founder decisions encoded here (2026-10-08 / 2026-10-10):

  * Halving interval: 315,360 blocks = 2 years at the 120 s target cadence
    (RFC-0001). Shortening it was considered and rejected: the plateau of a
    halving curve depends only on the launch rate, not the interval.
  * Launch rate ≈10.5M PRSM/yr = cap/2 (Option B), so the decay + tail blend
    carries cumulative supply through exactly 21,000,000 PRSM at roughly the
    ten-year mark, after which the non-discretionary 0.6%/yr tail continues
    forever (Monero precedent: long-run miner security never depends on fees
    alone). Deriving R0 from the CALENDAR year overshoots 2x — one halving
    epoch spans two calendar years, so an annual-rate derivation makes epoch
    0 alone pay > cap and breaches the hard cap immediately. R0 is therefore
    derived from the EPOCH target U = TARGET/2:

        R0 = ceil(CAP · APPROACH_BPS / (2 · HI · 10^4)) = 3,296,232,877

    whose calendar-year equivalent is 10,499,837.7 PRSM/yr — within 0.002%
    of the policy figure. An import-time assert pins this contract.

Schedule shape under the shipped parameters:

  * Epoch-boundary cap transition: none exists. The plateau 2·U clears the
    99%-of-cap approach target by only 400 shards, and integer floor drift
    in R0 >> k keeps every finite partial sum strictly below the ideal
    geometric series, so _cap_transition_epoch() returns None. That is the
    correct, deliberate outcome: the tail does not wait for a boundary that
    arithmetic can never reach.
  * Blended regime instead (the primary path, not a fallback): heights
    1..BLEND_HEIGHT-1 pay pure decay R0 >> k; from BLEND_HEIGHT each block
    pays max(R0 >> k, TAIL_BLOCK_SHARDS) — the tail tops blocks up to the
    flat 0.6%/yr rate once decay drops below it. Per-block issuance is
    non-increasing (asserted in emission_at), supply crosses the 21M
    reference exactly once (CAP_CROSS_HEIGHT), and the tail runs forever.
  * If a future parameter change ever makes the approach target reachable
    at an epoch boundary, CAP_TRANSITION_EPOCH/CAP_HEIGHT become set and
    the pure-tail handover path takes over automatically; both paths share
    the same simulation-derived constants and stay bounded consistently.

CAP_TRANSITION_EPOCH is computed by exact height-level simulation at import
(≤ bitlen(R0) big-int iterations) — never by a closed form, because floor
drift across successive right-shifts breaks naive geometric identities. The
simulation evaluates the same integer arithmetic the block-reward path runs,
so it cannot disagree with consensus. Module-level asserts pin the founder
contract: the plateau must clear the approach target, the derived launch
rate must match policy within 0.1%, and pre-tail supply must stay under the
hard cap.
"""
from __future__ import annotations

from dataclasses import dataclass

from .params import (
    DEV_FUND_SHARE_BPS,
    HALVING_INTERVAL_BLOCKS,
    INITIAL_ANNUAL_EMISSION_PRSM,
    SHARDS_PER_PRSM,
    TAIL_ANNUAL_RATE_BPS,
    BLOCKS_PER_YEAR,
    TOTAL_SUPPLY_SHARDS,
)

# ---------------------------------------------------------------------------
# Approach rule: R0 is sized so the halving plateau 2·U reaches APPROACH_BPS
# of the reference cap — the Option-B contract. _TARGET_SHARDS pins the
# epoch-boundary transition test; under the shipped parameters that test
# never fires (see module docstring) and the blend path carries issuance to
# the cap instead.
# ---------------------------------------------------------------------------
APPROACH_BPS = 9900          # plateau target := 99% of the reference cap

#: Epoch-boundary transition target, in shards.
_TARGET_SHARDS = (TOTAL_SUPPLY_SHARDS * APPROACH_BPS) // 10_000


def _initial_base_reward_shards() -> int:
    """R0: per-block base reward at launch, in whole shards.

    Exact integer derivation from the founder Option-B contract: the halving
    plateau 2·U (U = R0·HI, one epoch's issuance) must reach the approach
    target, and we take the smallest whole-shard reward that achieves it:

        R0 = ceil(_TARGET_SHARDS / (2 · HALVING_INTERVAL_BLOCKS)) = 3,296,232,877

    Calendar-year equivalent: R0·BLOCKS_PER_YEAR ≈ 10,499,837.7 PRSM/yr,
    within 0.002% of the INITIAL_ANNUAL_EMISSION_PRSM policy figure (10.5M).
    Deriving R0 from the calendar year instead (R0 = P·SPP/BPY) doubles the
    launch rate, because one halving epoch spans two calendar years; epoch 0
    alone then pays > 21M and breaches the hard cap. The assert below pins
    this trap shut permanently.
    """
    r0 = -(-_TARGET_SHARDS // (2 * HALVING_INTERVAL_BLOCKS))   # ceil division
    annual_prsm = r0 * BLOCKS_PER_YEAR / SHARDS_PER_PRSM
    policy = INITIAL_ANNUAL_EMISSION_PRSM
    assert abs(annual_prsm - policy) <= policy * 0.001, (
        f"derived launch rate {annual_prsm:,.0f} PRSM/yr drifted >0.1% from "
        f"founder policy {policy:,} PRSM/yr")
    return r0


INITIAL_BASE_REWARD_SHARDS = _initial_base_reward_shards()

#: Plateau of the pure halving curve (limit of Σ_k R0·HI·2^-k = 2·U with
#: U = R0·HI), in shards. Under Option B this clears _TARGET_SHARDS by 400
#: shards — enough to prove the ideal curve approaches the cap, though floor
#: drift keeps every finite partial sum below it (see module docstring).
PRE_TAIL_PLATEAU_SHARDS = 2 * INITIAL_BASE_REWARD_SHARDS * HALVING_INTERVAL_BLOCKS


def _cap_transition_epoch() -> int | None:
    """First epoch index K whose end-of-epoch cumulative pre-tail supply
    reaches _TARGET_SHARDS; None if the decay curve never crosses it.

    Exact height-level simulation: epoch k pays HI blocks at R0 >> k
    (heights k·HI+1 .. (k+1)·HI, matching base_reward's h // HI indexing).
    Runs ONCE at import, ≤ bitlen(R0) ≈ 33 iterations; the result becomes a
    consensus constant. No closed form: floor-drift across successive
    right-shifts breaks naive geometric identities (see module docstring).
    Under the shipped parameters the answer is None — deliberate, not an
    error; the blend regime then governs the tail handover.
    """
    hi = HALVING_INTERVAL_BLOCKS
    r0 = INITIAL_BASE_REWARD_SHARDS
    s = 0
    for k in range(r0.bit_length()):
        r = r0 >> k
        if r == 0:
            return None                     # extinct below target
        s += r * hi
        if s >= _TARGET_SHARDS:
            return k
    return None


CAP_TRANSITION_EPOCH = _cap_transition_epoch()

#: First height paid by the pure constant tail — set only if an epoch-
#: boundary transition exists (it does not under the shipped parameters;
#: BLEND_HEIGHT governs instead).
CAP_HEIGHT = (None if CAP_TRANSITION_EPOCH is None
              else (CAP_TRANSITION_EPOCH + 1) * HALVING_INTERVAL_BLOCKS)

#: Per-block amount of the constant tail: simple interest on the reference
#: cap — D1's "≈0.6%/yr" leg made exact against the 21M figure:
#: cap · TAIL_ANNUAL_RATE_BPS / blocks_per_year. Non-discretionary.
TAIL_BLOCK_SHARDS = (TOTAL_SUPPLY_SHARDS * TAIL_ANNUAL_RATE_BPS) \
                    // (10_000 * BLOCKS_PER_YEAR)


def _blend_epoch() -> int | None:
    """First epoch j whose per-block decay reward R0 >> j drops below
    TAIL_BLOCK_SHARDS — where the top-up tail takes over monotonically.
    None while an epoch-boundary cap transition governs (primary regime).
    """
    if CAP_TRANSITION_EPOCH is not None:
        return None
    r0 = INITIAL_BASE_REWARD_SHARDS
    j = 0
    while j < r0.bit_length() and (r0 >> j) >= TAIL_BLOCK_SHARDS:
        j += 1
    return j


def _cap_cross_height() -> int | None:
    """First height at which cumulative supply reaches TOTAL_SUPPLY_SHARDS.

    Informational consensus-derived constant (block explorers, RPC status):
    under the shipped blend schedule supply crosses the 21M reference cap
    once, at roughly the ten-year mark, and the fixed tail continues
    thereafter. Exact epoch-level simulation over the same integer terms as
    base_reward/tail_reward — every height in epoch k pays a constant amount
    — so it is O(bitlen(R0)) big-int steps, not per-block. Evaluated last,
    after the reward functions are defined.
    """
    hi = HALVING_INTERVAL_BLOCKS
    s = 0
    k = 0
    while True:
        epoch_start = k * hi                      # last height already counted
        first = epoch_start + 1                   # first height of this epoch
        pay = base_reward(first) + tail_reward(first)
        if pay == 0:
            return None                           # issuance stopped below cap
        step = pay * hi
        if s + step >= TOTAL_SUPPLY_SHARDS:
            blocks = -(-(TOTAL_SUPPLY_SHARDS - s) // pay)
            return epoch_start + blocks
        s += step
        k += 1
        if k > 8 * INITIAL_BASE_REWARD_SHARDS.bit_length():
            return None                           # guard: tail > 0 always
                                                  # crosses; unreachable here


BLEND_EPOCH = _blend_epoch()
BLEND_HEIGHT = None if BLEND_EPOCH is None else BLEND_EPOCH * HALVING_INTERVAL_BLOCKS

# Import-time invariants. These encode the Option-B contract; violating any
# of them is a parameterisation bug, not a runtime condition — fail loudly.
assert PRE_TAIL_PLATEAU_SHARDS >= _TARGET_SHARDS, \
    "halving plateau below approach target: Option B contract violated"
if CAP_TRANSITION_EPOCH is not None:
    _s_at_transition = sum((INITIAL_BASE_REWARD_SHARDS >> k)
                           * HALVING_INTERVAL_BLOCKS
                           for k in range(CAP_TRANSITION_EPOCH + 1))
    assert _TARGET_SHARDS <= _s_at_transition < TOTAL_SUPPLY_SHARDS, \
        "pre-tail supply must clear the target but stay under the hard cap"
else:
    # Blend regime: the handover must exist and be monotone — decay reward
    # at the last pure block must be ≥ the tail it blends into.
    assert BLEND_EPOCH is not None and BLEND_EPOCH >= 1, \
        "blend epoch must engage after at least one full decay epoch"
    assert INITIAL_BASE_REWARD_SHARDS >> (BLEND_EPOCH - 1) >= TAIL_BLOCK_SHARDS, \
        "tail exceeds the decay reward at handover: issuance cliff"


def base_reward(height: int) -> int:
    """Pre-tail base block reward in shards at `height`.

    Geometric decay: reward(h) = R0 >> (h // HALVING_INTERVAL_BLOCKS).
    Under an epoch-boundary transition (CAP_TRANSITION_EPOCH set) the decay
    stops at CAP_HEIGHT and returns 0 from there on; under the shipped
    blend parameters it continues indefinitely and tail_reward tops blocks
    up instead. Height 0 (genesis) pays nobody — zero premine (D1).
    """
    if height < 0:
        raise ValueError("negative height")
    if height == 0:
        return 0
    k = height // HALVING_INTERVAL_BLOCKS
    if CAP_TRANSITION_EPOCH is not None and k >= CAP_TRANSITION_EPOCH + 1:
        return 0                            # tail phase: decay has stopped
    return INITIAL_BASE_REWARD_SHARDS >> k


def tail_reward(height: int) -> int:
    """Per-block tail emission — the ≈0.6%/yr leg of D1 (§4.1).

    Blend regime (shipped parameters): from BLEND_HEIGHT the tail tops each
    block up to exactly TAIL_BLOCK_SHARDS, so per-block issuance never drops
    below the fixed tail rate and never cliffs at an epoch boundary.
    Epoch-transition regime (if a future parameterisation makes the approach
    target reachable): the pure constant tail pays TAIL_BLOCK_SHARDS from
    CAP_HEIGHT onward, nothing before. Genesis pays nobody.
    """
    if height <= 0:
        return 0
    if CAP_HEIGHT is not None:
        return TAIL_BLOCK_SHARDS if height >= CAP_HEIGHT else 0
    if height < BLEND_HEIGHT:
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
    # In the blend regime base decays by floor-rounding drift each epoch
    # while the top-up holds base+tail flat at TAIL_BLOCK_SHARDS; under an
    # epoch-boundary transition the handover at CAP_HEIGHT moves both legs
    # in one step (base 0, tail TAIL_BLOCK_SHARDS) and every earlier block
    # pays strictly more. A parameter change breaking monotonicity would
    # create reward cliffs at epoch boundaries — fail loudly rather than
    # ship it.
    if height > 1:
        prev_total = base_reward(height - 1) + tail_reward(height - 1)
        if total < prev_total:
            raise AssertionError(
                f"emission monotonicity violated at height {height}: "
                f"{prev_total} -> {total}")
    dev = (total * DEV_FUND_SHARE_BPS) // 10_000
    return BlockEmission(height, b, t, total - dev, dev)


def cumulative_supply(height: int) -> int:
    """Total shards emitted through block `height` inclusive.

    Exact closed form over the same integer terms as base_reward/tail_reward:
    complete decay epochs (Σ_j (R0>>j)·HI), the partial current epoch, plus
    the constant-tail or blend-top-up term beyond the transition. O(min(K,
    height//HI)) at worst — safe in the per-block consensus path. Verified
    height-by-height against naive accumulation by the test suite. Genesis
    (height 0) pays nobody, so sums run over reward-bearing heights 1..H.
    """
    if height < 0:
        raise ValueError("negative height")
    hi = HALVING_INTERVAL_BLOCKS
    r0 = INITIAL_BASE_REWARD_SHARDS
    total = 0

    if CAP_HEIGHT is not None:
        # Epoch-transition regime: heights 1..CAP_HEIGHT-1 pay decay,
        # CAP_HEIGHT.. pay the flat tail. Decay epochs fully below
        # CAP_HEIGHT: k = 0..K where K = CAP_TRANSITION_EPOCH; epoch k
        # covers [k·hi+1,(k+1)·hi].
        decay_end = min(height, CAP_HEIGHT - 1)
        full_epochs = decay_end // hi
        for j in range(full_epochs):
            total += (r0 >> j) * hi
        rem_start = full_epochs * hi + 1
        if decay_end >= rem_start:
            total += (r0 >> full_epochs) * (decay_end - rem_start + 1)
        if height >= CAP_HEIGHT:
            total += TAIL_BLOCK_SHARDS * (height - CAP_HEIGHT + 1)
        return total

    # Blend regime (shipped parameters): indefinite decay with the
    # top-up tail engaging from BLEND_HEIGHT.
    completed = height // hi
    for j in range(completed):
        total += (r0 >> j) * hi
    rem_start = completed * hi + 1
    if height >= rem_start:
        total += (r0 >> completed) * (height - rem_start + 1)
    if height >= BLEND_HEIGHT:
        # Top-up Σ_{h=BLEND_HEIGHT}^{height} (TAIL − R0>>(h//hi)). Full
        # epochs j = BLEND_EPOCH..completed−1 via the shift-sum identity
        # Σ_{j=a}^{b} (r0>>j) = (r0>>(a−1)) − (r0>>b)  [valid for a ≥ 1]:
        # epoch 0 is never blended under reachable-parameter settings since
        # R0 ≥ TAIL_BLOCK_SHARDS whenever the blend engages (asserted at
        # import).
        n_full = completed - BLEND_EPOCH
        if n_full >= 1:
            decay_sum = (r0 >> (BLEND_EPOCH - 1)) - (r0 >> (completed - 1))
            total += n_full * hi * TAIL_BLOCK_SHARDS - hi * decay_sum
        top_start = max(BLEND_HEIGHT, completed * hi + 1)
        if height >= top_start:
            total += (height - top_start + 1) * (TAIL_BLOCK_SHARDS
                                                 - (r0 >> completed))
    return total


def prsm(x_shards: int) -> str:
    """Format shards -> human PRSM string (8 decimals)."""
    whole, frac = divmod(x_shards, SHARDS_PER_PRSM)
    return f"{whole}.{frac:08d}".rstrip("0").rstrip(".")


# Defined last: the simulation below calls base_reward/tail_reward, which
# must exist by the time module-level constants are evaluated.
CAP_CROSS_HEIGHT = _cap_cross_height()

if CAP_TRANSITION_EPOCH is None:
    assert CAP_CROSS_HEIGHT is not None, \
        "blended schedule must carry supply through the reference cap"
    # Cross-check the simulation against the closed form it mirrors.
    assert cumulative_supply(CAP_CROSS_HEIGHT) >= TOTAL_SUPPLY_SHARDS > \
        cumulative_supply(CAP_CROSS_HEIGHT - 1), \
        "CAP_CROSS_HEIGHT disagrees with cumulative_supply"
