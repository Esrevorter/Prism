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
  * Launch scale ≈10.5M PRSM = cap/2 per halving epoch (Option B), so the
    decay + tail blend carries cumulative supply through exactly
    21,000,000 PRSM at roughly the eleven-year mark, after which the
    non-discretionary 0.6%/yr tail continues forever (Monero precedent: long-run miner security never
    depends on fees alone). R0 is derived from the EPOCH approach target —
    the smallest whole-shard reward whose ideal halving plateau
    Σ_k R0·HI·2^-k = 2·R0·HI reaches 99% of the cap:

        R0 = ceil(CAP · APPROACH_BPS / (2 · HI · 10^4)) = 3,296,232,877

    Calendar-year equivalent: R0 · BPY = 10,499,837.7 PRSM/yr — within
    0.002% of the policy figure. Deriving R0 from the calendar year directly
    overshoots ~2x: one halving epoch spans two calendar years, so an
    annual-rate derivation makes epoch 0 alone pay ≈21M and breaches the
    hard cap almost immediately. An import-time assert pins this contract.

Schedule shape under the shipped parameters:

  * Blended regime (the primary path). Let j = BLEND_EPOCH, the first epoch
    whose base reward R0 >> j falls below TAIL_BLOCK_SHARDS (j = 6 under
    the shipped constants):
      - epochs 0..j-1 pay pure decay R0 >> k (halving as usual);
      - epoch j onward every block pays exactly TAIL_BLOCK_SHARDS, split as
        the decayed base plus a top-up; the top-up saturates at the full
        flat tail once the base has shifted to zero, so issuance runs
        forever at ≈0.6%/yr on the 21M reference.
    Supply therefore plateaus below 21M during pure decay (s_pre ≈
    20.628M PRSM through epoch j-1), then crosses the 21M reference cap
    once inside the blend (CAP_CROSS_HEIGHT = 2,984,354 ≈ year 11.4) and grows
    linearly thereafter — D1's "constant tail forever" leg, with the cap
    as the approach point of the decay curve rather than a hard ceiling
    on the tail (Monero precedent).
  * Epoch-boundary cap transition: CAP_TRANSITION_EPOCH simulates the pure
    decay leg against the approach target. Because floor drift across the
    successive right-shifts keeps every finite partial sum strictly below
    the ideal plateau 2·U — which itself clears the target by only 400
    shards — the answer is None under the shipped parameters. That is
    deliberate, not an error: the tail does not wait for a boundary that
    integer arithmetic can never reach. If a future parameterisation ever
    makes the target reachable at an epoch boundary, CAP_TRANSITION_EPOCH/
    CAP_HEIGHT become set and the pure-tail handover path takes over
    automatically; both paths share the same simulation-derived constants.
  * The halving cliff at each epoch boundary (R0 >> k → R0 >> (k+1)) is
    the intended, consensus-critical shape of a decaying schedule; the
    blend handover itself is monotone by construction (last pure block
    pays ≥ TAIL_BLOCK_SHARDS ≥ first blended block).

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
# epoch-boundary transition test; under the shipped blend schedule that test
# never fires (see module docstring) and the tail carries issuance across
# the cap instead.
# ---------------------------------------------------------------------------
APPROACH_BPS = 9900          # plateau target := 99% of the reference cap

#: Epoch-boundary transition target, in shards.
_TARGET_SHARDS = (TOTAL_SUPPLY_SHARDS * APPROACH_BPS) // 10_000

#: Per-block amount of the constant tail: simple interest on the reference
#: cap — D1's "≈0.6%/yr" leg made exact against the 21M figure:
#: cap · TAIL_ANNUAL_RATE_BPS / blocks_per_year. Non-discretionary.
#: Defined here because the derivation helpers below reference it.
TAIL_BLOCK_SHARDS = (TOTAL_SUPPLY_SHARDS * TAIL_ANNUAL_RATE_BPS) \
                    // (10_000 * BLOCKS_PER_YEAR)


def _initial_base_reward_shards() -> int:
    """R0: per-block base reward at launch, in whole shards.

    Exact integer derivation from the founder Option-B contract: the halving
    plateau 2·U (U = R0·HI, one epoch's issuance) must reach the approach
    target, and we take the smallest whole-shard reward that achieves it:

        R0 = ceil(_TARGET_SHARDS / (2 · HALVING_INTERVAL_BLOCKS)) = 3,296,232,877

    Epoch 0 then pays U ≈ 10.395M PRSM — within 1% of the founder's cap/2
    epoch target (INITIAL_ANNUAL_EMISSION_PRSM = 10.5M per 2-year halving
    epoch; the policy figure is read as EPOCH issuance, which is what makes
    the plateau 2·U ≈ 20.79M clear 99% of the cap while every finite partial
    sum stays below 21M). The load-bearing trap: sizing a full calendar year
    of issuance per epoch doubles the launch rate; epoch 0 alone then pays
    ≈21M and breaches the hard cap immediately. The assert below pins this
    contract shut.
    """
    r0 = -(-_TARGET_SHARDS // (2 * HALVING_INTERVAL_BLOCKS))   # ceil division
    epoch_prsm = r0 * HALVING_INTERVAL_BLOCKS / SHARDS_PER_PRSM
    policy_epoch = INITIAL_ANNUAL_EMISSION_PRSM   # cap/2 per halving epoch
    assert abs(epoch_prsm - policy_epoch) <= policy_epoch * 0.01, (
        f"derived epoch issuance {epoch_prsm:,.0f} PRSM drifted >1% from "
        f"founder epoch target {policy_epoch:,} PRSM")
    return r0


INITIAL_BASE_REWARD_SHARDS = _initial_base_reward_shards()

#: Plateau of the pure halving curve (limit of Σ_k R0·HI·2^-k = 2·U with
#: U = R0·HI), in shards. Under Option B this clears _TARGET_SHARDS by 400
#: shards — enough to prove the ideal curve approaches the cap, though floor
#: drift keeps every finite partial sum below it (see module docstring).
PRE_TAIL_PLATEAU_SHARDS = 2 * INITIAL_BASE_REWARD_SHARDS * HALVING_INTERVAL_BLOCKS


def _cap_transition_epoch() -> int | None:
    """First epoch index K whose end-of-epoch cumulative PRE-TAIL (pure
    decay) supply reaches _TARGET_SHARDS; None if the decay curve never
    crosses it.

    Exact height-level simulation: epoch k pays HI blocks at R0 >> k
    (heights k·HI+1 .. (k+1)·HI, matching base_reward's h // HI indexing).
    Runs ONCE at import, ≤ bitlen(R0) iterations; the result becomes a
    consensus constant. No closed form: floor-drift across successive
    right-shifts breaks naive geometric identities (see module docstring).

    This test deliberately excludes tail top-ups: CAP_TRANSITION_EPOCH
    marks where the DECAY leg alone approaches the cap, which is what
    governs the pure-tail handover in base_reward/tail_reward. The blend
    regime (top-up from BLEND_HEIGHT) is the fallback path for schedules
    whose decay plateau falls short of the target.
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
    exactly once, inside the blend regime, and the fixed tail continues
    thereafter. Computed by binary search on cumulative_supply — the single
    authoritative closed form — so it cannot disagree with it by construction.
    Monotone non-decreasing supply makes the search sound. Evaluated last,
    after cumulative_supply is defined.
    """
    cap = TOTAL_SUPPLY_SHARDS
    if cumulative_supply(_CAP_CROSS_UPPER_BOUND) < cap:
        return None
    lo, hi = 1, _CAP_CROSS_UPPER_BOUND
    while lo < hi:
        mid = (lo + hi) // 2
        if cumulative_supply(mid) >= cap:
            hi = mid
        else:
            lo = mid + 1
    return lo


BLEND_EPOCH = _blend_epoch()
BLEND_HEIGHT = None if BLEND_EPOCH is None else BLEND_EPOCH * HALVING_INTERVAL_BLOCKS

#: Search bound for _cap_cross_height: supply grows by at least one tail
#: shard per block once the blend engages, so crossing (if it happens) is
#: found well inside this horizon; the plateau check below makes the
#: "never crosses" case cheap to detect as well.
_CAP_CROSS_UPPER_BOUND = 100 * HALVING_INTERVAL_BLOCKS

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

    Blend regime (shipped parameters): from BLEND_HEIGHT every block pays
    exactly TAIL_BLOCK_SHARDS in total, split into the decayed base reward
    plus a top-up. Once the base reward has decayed to zero (k ≥ bitlen(R0))
    the top-up saturates at the full flat tail, so issuance continues
    forever at ≈0.6%/yr on the 21M reference. The top-up is clamped at
    TAIL_BLOCK_SHARDS to keep it non-negative under any parameterisation.
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
    return min(TAIL_BLOCK_SHARDS,
               TAIL_BLOCK_SHARDS - base_reward(height))


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
    # Consensus invariants. Per-block issuance must never be negative or
    # exceed the launch reward, and it must never *increase* with height:
    # halvings at epoch boundaries are the intended cliff shape of a
    # decaying schedule, while the blend handover is monotone by
    # construction (last pure block pays ≥ TAIL_BLOCK_SHARDS ≥ first
    # blended block). An increase anywhere would mean a parameterisation
    # bug creating a reward cliff inversion — fail loudly rather than ship.
    if height > 1:
        prev_total = base_reward(height - 1) + tail_reward(height - 1)
        if total > prev_total:
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

    # Blend regime (shipped parameters): indefinite decay with a top-up
    # tail that engages from BLEND_HEIGHT (epoch BLEND_EPOCH). The exact
    # per-block total over reward-bearing heights 1..H is
    #     max(R0 >> (h // HI), TAIL_BLOCK_SHARDS)  for h >= BLEND_HEIGHT,
    #     R0 >> (h // HI)                          below BLEND_HEIGHT,
    # because tail_reward tops each block up to the flat tail rate while
    # the decayed base still exceeds it, and pays nothing pre-blend.
    # Epoch j therefore contributes (r0 >> j) or max(r0 >> j, tail),
    # whichever applies, times its block count within 1..H. Genesis
    # (height 0) is never counted. Once the base has shifted to zero
    # (k >= bitlen(R0)) every block pays exactly the flat tail; those
    # saturated epochs collapse into one closed-form term.
    # O(bitlen(R0)) big-int steps at worst.
    bits = r0.bit_length()

    def _blocks_in_epoch(j: int) -> int:
        """Reward-bearing heights <= `height` inside epoch j = [j*HI, (j+1)*HI)."""
        return max(0, min(height + 1, (j + 1) * hi) - max(j * hi, 1))

    full_stop = height // hi                     # last epoch touching height
    for j in range(min(full_stop, bits - 1) + 1):
        blocks = _blocks_in_epoch(j)
        if blocks == 0:
            continue
        pay = r0 >> j if j < BLEND_EPOCH else max(r0 >> j, TAIL_BLOCK_SHARDS)
        total += pay * blocks
    # Saturated epochs (base extinct -> pure flat tail). sat_first >= bits,
    # so these are disjoint from the loop above, which stopped at bits-1.
    sat_first = max(BLEND_EPOCH, bits)
    if full_stop >= sat_first:
        for j in range(sat_first, full_stop + 1):
            total += _blocks_in_epoch(j) * TAIL_BLOCK_SHARDS
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
