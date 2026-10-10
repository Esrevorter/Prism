"""Emission schedule — spec.md v1.0 §4.1, Decision D1 (Option B).

Model: discrete geometric decay (Monero-style base reward) until the
cumulative pre-tail supply first reaches APPROACH_BPS of the 21M hard cap;
from that epoch boundary a consensus-fixed constant tail of ≈0.6%/yr runs
forever. Zero premine; the dev fund is a fixed carve-out of each block
reward. All values are exact integers (shards) — no floats in consensus
code.

Constants (founder decisions):
  * Halving interval: 315,360 blocks = 2 years at the 120 s target cadence
    (RFC-0001, resolved 2026-10-08; the 787,750-block figure in early drafts
    was Monero's number under a legacy 60 s cadence and does not apply).
  * Initial annual emission: 10,500,000 PRSM/yr = cap/2 (Option B, resolved
    2026-10-10). A pure halving curve plateaus at 2·U where U = R0·HI is one
    epoch's total issuance, so it can only "approach" the cap if 2·U clears
    the approach threshold. Under the original 2.1M/yr launch rate the
    plateau was ≈4.2M PRSM and the transition could never fire (D1 conflict,
    flagged 2026-10-10); Option B raises the launch rate to cap/2 so the
    plateau 2·U ≈ 21M crosses the 99% line. Shortening the halving interval
    was considered and rejected — it changes the decay shape without raising
    the plateau, which depends only on the annual issuance rate.

Block-height conventions:
  * Height 0 (genesis) pays nobody — zero premine, asserted by tests.
  * Epoch k covers reward-bearing heights [k·HI + 1 .. (k+1)·HI] and pays
    R0 >> k per block (base_reward uses integer division h // HI, so the
    boundary height (k+1)·HI belongs to epoch k).
  * CAP_TRANSITION_EPOCH K is the first epoch whose END-of-epoch cumulative
    supply reaches the approach target; the tail takes over at
    CAP_HEIGHT = (K+1)·HI.

CAP_TRANSITION_EPOCH is computed by exact height-level simulation at import
(≤ ~32 big-int iterations for any sane parameterisation) — never by a closed
form. An earlier closed form silently mis-derived R0 (confusing calendar-year
with epoch-year units, yielding a 5.2M/yr launch rate instead of 10.5M) and
then returned None; the simulation cannot be fooled because it evaluates the
same integer arithmetic the block path will run. A module-level assert pins
the derived R0 to the founder policy figure so unit-scaling regressions fail
loudly at import rather than shipping as silent consensus drift.

Safety fallback: if a future parameter change made the cap unreachable
again (curve extinct below the target), CAP_TRANSITION_EPOCH would be None
and emission_at falls back to the blended-tail regime (BLEND_HEIGHT): the
tail tops each block up to exactly TAIL_BLOCK_SHARDS once the decay reward
drops below it. Issuance then still converges below the cap. The current
Option-B parameters take the primary regime; the fallback exists so
issuance can never diverge or exceed the cap under any parameterisation.
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
# Approach rule: the decay phase ends when cumulative supply first reaches
# APPROACH_BPS of the hard cap. The remaining headroom (~1% of cap under the
# default) guarantees S(K) < TOTAL_SUPPLY_SHARDS for every pre-tail height.
# ---------------------------------------------------------------------------
APPROACH_BPS = 9900          # "cap approached" := supply >= 99% of cap

#: Consensus target for the end of the pre-tail phase, in shards.
_TARGET_SHARDS = (TOTAL_SUPPLY_SHARDS * APPROACH_BPS) // 10_000


def _initial_base_reward_shards() -> int:
    """R0: per-block base reward at launch, in whole shards.

    Exact integer derivation from the founder policy figure (Option B):

        R0 = ceil(INITIAL_ANNUAL_EMISSION_PRSM · SHARDS_PER_PRSM
                  / BLOCKS_PER_YEAR)

    One calendar year contains BLOCKS_PER_YEAR reward-bearing blocks, so the
    realised launch rate is R0·BLOCKS_PER_YEAR ∈ [policy, policy + BPY) —
    within 1 shard/block of 10.5M PRSM/yr (overshoot ≈ 646 shards/yr).
    Ceiling rather than floor keeps issuance marginally above the policy
    figure; a sub-shard shortfall would otherwise accumulate every block.

    Unit trap this formula prevents (a shipped bug once already): with a
    two-year halving interval an ANNUAL rate must divide by BLOCKS_PER_YEAR,
    never by HALVING_INTERVAL_BLOCKS — doing so halves the launch rate to
    ~5.2M/yr and puts the geometric plateau (~10.4M PRSM) structurally below
    the approach target, so the tail transition can never fire.
    """
    annual_shards = INITIAL_ANNUAL_EMISSION_PRSM * SHARDS_PER_PRSM
    return -(-annual_shards // BLOCKS_PER_YEAR)      # ceil division


INITIAL_BASE_REWARD_SHARDS = _initial_base_reward_shards()

#: Pre-tail plateau of the halving curve (limit of Σ_k R0·HI·2^-k = 2·U with
#: U = R0·HI), in shards. Under Option B this is ≈21.0006M PRSM > TARGET, so
#: the transition provably fires; the plateau itself is NOT a bound on
#: consensus supply — CAP_HEIGHT stops the decay before it matters.
PRE_TAIL_PLATEAU_SHARDS = 2 * INITIAL_BASE_REWARD_SHARDS * HALVING_INTERVAL_BLOCKS


def _cap_transition_epoch() -> int | None:
    """First epoch index K whose end-of-epoch cumulative pre-tail supply
    reaches _TARGET_SHARDS; None if the curve goes extinct below the target.

    Exact height-level simulation: epoch k pays HI blocks at R0 >> k
    (heights k·HI+1 .. (k+1)·HI, matching base_reward's h // HI indexing).
    Runs ONCE at import, ≤ bitlen(R0) ≈ 33 iterations; the result becomes a
    consensus constant. No closed form: floor-drift across successive
    right-shifts breaks naive geometric identities (see module docstring).
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

#: First height paid by the constant tail (None only in the fallback regime).
CAP_HEIGHT = (None if CAP_TRANSITION_EPOCH is None
              else (CAP_TRANSITION_EPOCH + 1) * HALVING_INTERVAL_BLOCKS)

#: Per-block amount of the constant tail phase: simple interest on the hard
#: cap — D1's "≈0.6%/yr" leg made exact against the 21M reference:
#: cap · TAIL_ANNUAL_RATE_BPS / blocks_per_year. Non-discretionary.
TAIL_BLOCK_SHARDS = (TOTAL_SUPPLY_SHARDS * TAIL_ANNUAL_RATE_BPS) \
                    // (10_000 * BLOCKS_PER_YEAR)


def _blend_epoch() -> int | None:
    """Fallback-regime boundary: first epoch j whose per-block decay reward
    R0 >> j drops below TAIL_BLOCK_SHARDS, i.e. where a top-up tail can take
    over monotonically. None while the primary cap-transition regime applies.
    """
    if CAP_TRANSITION_EPOCH is not None:
        return None
    r0 = INITIAL_BASE_REWARD_SHARDS
    j = 0
    while j < r0.bit_length() and (r0 >> j) >= TAIL_BLOCK_SHARDS:
        j += 1
    return j


BLEND_EPOCH = _blend_epoch()
BLEND_HEIGHT = None if BLEND_EPOCH is None else BLEND_EPOCH * HALVING_INTERVAL_BLOCKS

# Import-time invariants. These encode the Option-B contract; violating any
# of them is a parameterisation bug, not a runtime condition — fail loudly.
assert PRE_TAIL_PLATEAU_SHARDS >= _TARGET_SHARDS, \
    "halving plateau below approach target: transition cannot fire"
assert abs(INITIAL_BASE_REWARD_SHARDS * BLOCKS_PER_YEAR
           - INITIAL_ANNUAL_EMISSION_PRSM * SHARDS_PER_PRSM) \
    <= BLOCKS_PER_YEAR, \
    "derived launch rate drifted > 1 shard/block from the founder policy"
if CAP_TRANSITION_EPOCH is not None:
    _s_at_transition = sum((INITIAL_BASE_REWARD_SHARDS >> k)
                           * HALVING_INTERVAL_BLOCKS
                           for k in range(CAP_TRANSITION_EPOCH + 1))
    assert _TARGET_SHARDS <= _s_at_transition < TOTAL_SUPPLY_SHARDS, \
        "pre-tail supply must clear the target but stay under the hard cap"


def base_reward(height: int) -> int:
    """Pre-tail base block reward in shards at `height`.

    Geometric decay: reward(h) = R0 >> (h // HALVING_INTERVAL_BLOCKS).
    In the primary regime the decay stops at CAP_HEIGHT (returns 0 from
    there on; the constant tail takes over). In the fallback regime the
    curve continues indefinitely and the blended tail tops blocks back up
    (see tail_reward). Height 0 (genesis) pays nobody — zero premine (D1).
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

    Primary regime (CAP_HEIGHT set): the pure constant tail pays
    TAIL_BLOCK_SHARDS from CAP_HEIGHT onward, nothing before.
    Fallback regime (unreachable cap): the tail blends in at BLEND_HEIGHT,
    topping each block up to exactly TAIL_BLOCK_SHARDS, so issuance stays
    monotonic and bounded below the plateau < cap. Genesis pays nobody.
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
    # In the blended fallback regime base decays by floor-rounding drift each
    # epoch while the top-up holds base+tail flat at TAIL_BLOCK_SHARDS; in
    # the primary regime the handover at CAP_HEIGHT moves both legs in one
    # step (base 0, tail TAIL_BLOCK_SHARDS) and every earlier block pays
    # strictly more. A parameter change breaking monotonicity would create
    # reward cliffs at epoch boundaries — fail loudly rather than ship it.
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
        # Primary regime: heights 1..CAP_HEIGHT-1 pay decay, CAP_HEIGHT..
        # pay the flat tail. Decay epochs fully below CAP_HEIGHT: k = 0..K
        # where K = CAP_TRANSITION_EPOCH; epoch k covers [k·hi+1,(k+1)·hi].
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

    # Fallback regime: indefinite decay with blended top-up from BLEND_HEIGHT.
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
        # R0 ≥ TAIL_BLOCK_SHARDS whenever the fallback engages.
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
