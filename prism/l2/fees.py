"""Fee predictability hardening — spec §10.1 (Phase 3, L1 deliverable).

Principle 5 of §2: "Fee uncertainty is a UX bug." This module makes the fee
policy *enforceable* rather than merely advertised:

  * Base fee is FIXED at 0.0001 PRSM (10_000 shards) — no estimation, ever.
  * Priority fee is an integer multiple of base; total fee must land on
    N·BASE_FEE with 1 <= N <= PRIORITY_FEE_MAX_MULTIPLE (hard cap 10x).
    Transactions outside that lattice are **non-standard**: they may never
    enter a block (§10.1 "transactions above cap are non-standard").
  * Block-packing policy: within the priority band, blocks pack by priority
    density (fee per byte); dynamic block size absorbs bursts so base-fee
    transactions clear within <= 2 blocks at normal load (§10.1).

Everything here is pure and deterministic — the mempool/packer plugs into
the node later; the acceptance gate is the consensus-relevant surface.

Reference implementation: readable, stdlib-only, tested in
prism/tests/test_l2_fees.py.
"""
from __future__ import annotations

from dataclasses import dataclass

# --------------------------------------------------------------------------
# Policy constants — imported from the single source of truth so this module
# can never drift from consensus params (spec §4.1 / §10.1).
# --------------------------------------------------------------------------
from ..chain.params import BASE_FEE_SHARDS, PRIORITY_FEE_MAX_MULTIPLE

#: Worst-case confirmation target for base-fee txs at normal load (§10.1).
BASE_FEE_CLEAR_BLOCKS_MAX = 2


class FeePolicyError(ValueError):
    """Raised when a fee violates the §10.1 policy (non-standard tx)."""


def priority_multiple(fee_shards: int) -> int:
    """Exact integer multiple N such that fee == N * BASE_FEE, or raise.

    A fee that is not a whole multiple of base is rejected outright: the
    spec's fee lattice is part of what makes fees *predictable* — a user
    shown "0.00017 PRSM" could never reproduce it from the published table.
    """
    if fee_shards < BASE_FEE_SHARDS:
        raise FeePolicyError(
            f"fee {fee_shards} below fixed base fee {BASE_FEE_SHARDS}")
    if fee_shards % BASE_FEE_SHARDS != 0:
        raise FeePolicyError(
            f"fee {fee_shards} is not an integer multiple of base "
            f"{BASE_FEE_SHARDS}; priority fees are whole multiples only")
    return fee_shards // BASE_FEE_SHARDS


def validate_fee(fee_shards: int) -> int:
    """Consensus/mempool acceptance gate. Returns the fee unchanged when
    standard; raises FeePolicyError otherwise (§10.1 hard-cap enforcement).
    """
    n = priority_multiple(fee_shards)
    if n > PRIORITY_FEE_MAX_MULTIPLE:
        raise FeePolicyError(
            f"priority fee {n}x base exceeds hard cap "
            f"{PRIORITY_FEE_MAX_MULTIPLE}x — transaction is non-standard")
    return fee_shards


def is_standard_fee(fee_shards: int) -> bool:
    """Non-raising predicate used by relay/deny checks."""
    try:
        validate_fee(fee_shards)
        return True
    except FeePolicyError:
        return False


@dataclass(frozen=True)
class FeeQuote:
    """What the wallet shows before signing. Deterministic by construction:
    same amount + tier => same quote. No oracle, no estimation window.
    """
    base_shard: int
    priority_shard: int
    tier: str

    @property
    def total_shard(self) -> int:
        return self.base_shard + self.priority_shard

    @property
    def multiple(self) -> int:
        return self.total_shard // BASE_FEE_SHARDS


#: The three tiers the v1 UI exposes (§10.1: base + bounded priority band).
_TIERS = {"base_only": 0, "priority_low": 1, "priority_max": PRIORITY_FEE_MAX_MULTIPLE - 1}


def quote(tier: str = "base_only") -> FeeQuote:
    """Pre-sign fee quote for a named tier. Unknown tiers raise — the UI
    must never silently fall back to a different price (§2 principle 5)."""
    if tier not in _TIERS:
        raise FeePolicyError(f"unknown fee tier {tier!r}")
    return FeeQuote(base_shard=BASE_FEE_SHARDS,
                    priority_shard=_TIERS[tier] * BASE_FEE_SHARDS,
                    tier=tier)


# ------------------------------------------------------ block packing ------

@dataclass(frozen=True)
class TxView:
    """Minimal mempool view the packer needs: id, serialized size, fee."""
    tx_id: str
    size_bytes: int
    fee_shards: int


def priority_density(tx: TxView) -> float:
    """fee-per-byte ranking key (§10.1 'blocks pack by priority density')."""
    if tx.size_bytes <= 0:
        raise ValueError("tx size must be positive")
    return tx.fee_shards / tx.size_bytes


def pack_block(txs: list[TxView], *, block_size_bytes: int) -> tuple[list[TxView], list[TxView]]:
    """Greedy priority-density packing into one block.

    Returns (included, deferred). Deferred transactions keep their place
    for the next block; because the band is bounded (<= 10x) and block size
    is dynamic (2-4 MB), a base-fee tx deferred once under burst load is
    included in the very next block — clearing within BASE_FEE_CLEAR_BLOCKS_MAX
    at normal load is asserted behaviorally in the test suite.

    Tie-break is lexicographic on tx_id so every honest node produces the
    identical packing (determinism > throughput here).
    """
    if block_size_bytes <= 0:
        raise ValueError("block size must be positive")
    ordered = sorted(txs, key=lambda t: (-priority_density(t), t.tx_id))
    included: list[TxView] = []
    deferred: list[TxView] = []
    used = 0
    for tx in ordered:
        # Non-standard fees never get packed, regardless of how attractive
        # the density looks — the acceptance gate runs at pack time too,
        # so a hostile miner cannot smuggle a 12x tx through the packer.
        if not is_standard_fee(tx.fee_shards):
            deferred.append(tx)
            continue
        if used + tx.size_bytes <= block_size_bytes:
            included.append(tx)
            used += tx.size_bytes
        else:
            deferred.append(tx)
    return included, deferred
