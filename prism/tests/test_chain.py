"""Phase-1 unit tests. Run: python3 -m pytest  (from prism/)"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from chain.block import (BlockHeader, PoWFields, ZERO32, parse_header,
                         validate_header_basic)
from chain.denylist import GENESIS_ACC, DenylistStore, Entry, SignedRoot, fold
from chain.difficulty import DifficultyWindow, retarget
from chain.emission import (BLEND_EPOCH, BLEND_HEIGHT, CAP_CROSS_HEIGHT,
                            CAP_HEIGHT, CAP_TRANSITION_EPOCH,
                            INITIAL_BASE_REWARD_SHARDS, PRE_TAIL_PLATEAU_SHARDS,
                            TAIL_BLOCK_SHARDS, base_reward,
                            cumulative_supply, emission_at, prsm, tail_reward)
from chain.node import ChainState, make_genesis, merkle_root, mine_block
from chain.params import (BLOCKS_PER_YEAR, DEV_FUND_SHARE_BPS,
                          HALVING_INTERVAL_BLOCKS, REFRACTION_TESTNET,
                          TAIL_ANNUAL_RATE_BPS, TOTAL_SUPPLY_SHARDS)
from chain.pow import PlaceholderSha3Pow, assert_miner_backend, viewkey_for_height


# ------------------------------------------------------------ emission ---
def test_first_epoch_rewards_and_devfund_split():
    e = emission_at(1)
    assert e.base_shards == INITIAL_BASE_REWARD_SHARDS
    assert e.dev_fund_shares == (e.total_new_shards * DEV_FUND_SHARE_BPS) // 10_000
    assert e.miner_shares + e.dev_fund_shares == e.total_new_shards


def test_halving_step():
    h0 = base_reward(1)                          # first reward-bearing block
    assert h0 == INITIAL_BASE_REWARD_SHARDS      # genesis (h=0) pays nobody
    assert base_reward(0) == 0                   # zero premine (D1)
    if CAP_TRANSITION_EPOCH is None or CAP_TRANSITION_EPOCH >= 2:
        # exact halving at an epoch step *within* the decay phase: two blocks
        # one interval apart, both strictly before the cap transition (if any).
        k = 0 if CAP_TRANSITION_EPOCH is None else CAP_TRANSITION_EPOCH - 2
        h_a = base_reward(k * HALVING_INTERVAL_BLOCKS + 1)
        h_b = base_reward((k + 1) * HALVING_INTERVAL_BLOCKS + 1)
        assert h_a > 0 and h_a // 2 == h_b       # R0 >> k halves to R0 >> (k+1)
    else:
        # Cap approached after a single decay epoch: the "halving" boundary
        # coincides with the cap transition — decay stops there and the tail
        # takes over.
        assert CAP_TRANSITION_EPOCH == 1
        assert base_reward(HALVING_INTERVAL_BLOCKS - 1) == INITIAL_BASE_REWARD_SHARDS
        assert base_reward(CAP_HEIGHT) == 0


def test_genesis_pays_nobody():
    """D1: zero premine — height 0 is a synthetic block; supply starts at 0."""
    assert base_reward(0) == 0
    assert tail_reward(0) == 0
    assert emission_at(0).total_new_shards == 0
    assert cumulative_supply(0) == 0


def test_d1_option_b_contract_current_parameters():
    """Documentation-as-test for the D1 resolution (founder decision
    2026-10-10, Option B): launch epoch issuance ≈10.5M PRSM (cap/2) with
    the RFC-0001 2-year halving cadence, so the decay + tail blend carries
    cumulative supply through exactly 21,000,000 PRSM and the fixed
    ≈0.6%/yr tail continues forever (Monero precedent — the cap is the
    decay curve's approach point, not a ceiling on the non-discretionary
    tail leg). Contract that must hold under the shipped constants:
      * plateau 2·U clears the 99% approach target but stays under the cap
      * no epoch-boundary transition exists (integer floor drift keeps
        every finite pure-decay sum below target): CAP_TRANSITION_EPOCH /
        CAP_HEIGHT are None and the blend regime governs
      * the blend handover is monotone (no reward cliff inversion)
      * supply crosses the 21M reference exactly once, inside the blend
      * far in the future every block still pays exactly the flat tail
    """
    assert PRE_TAIL_PLATEAU_SHARDS >= (TOTAL_SUPPLY_SHARDS * 9900) // 10_000
    assert PRE_TAIL_PLATEAU_SHARDS < TOTAL_SUPPLY_SHARDS   # ideal plateau < cap
    assert CAP_TRANSITION_EPOCH is None
    assert CAP_HEIGHT is None
    assert BLEND_EPOCH is not None and BLEND_EPOCH >= 1
    # Monotone handover: last pure-decay block pays ≥ the tail it blends into.
    assert base_reward(BLEND_HEIGHT - 1) >= TAIL_BLOCK_SHARDS
    # Blend engages exactly where the decayed base drops below the tail.
    assert INITIAL_BASE_REWARD_SHARDS >> BLEND_EPOCH < TAIL_BLOCK_SHARDS
    # From the blend onward every block pays exactly TAIL_BLOCK_SHARDS.
    for h in (BLEND_HEIGHT, BLEND_HEIGHT + 1, (BLEND_EPOCH + 3) * HALVING_INTERVAL_BLOCKS):
        assert emission_at(h).total_new_shards == TAIL_BLOCK_SHARDS
    # Cap crossing is pinned by the closed form and happens inside the blend.
    assert CAP_CROSS_HEIGHT is not None and CAP_CROSS_HEIGHT > BLEND_HEIGHT
    assert cumulative_supply(CAP_CROSS_HEIGHT) >= TOTAL_SUPPLY_SHARDS
    assert cumulative_supply(CAP_CROSS_HEIGHT - 1) < TOTAL_SUPPLY_SHARDS
    # Far future: base extinct, flat tail forever; supply strictly grows.
    far = 100 * HALVING_INTERVAL_BLOCKS
    assert base_reward(far) == 0                   # R0 >> 100 == 0
    assert tail_reward(far) == TAIL_BLOCK_SHARDS
    assert cumulative_supply(far) > cumulative_supply(CAP_CROSS_HEIGHT)


def test_cumulative_supply_matches_per_block_emission_under_decay():
    """closed-form sum == naive per-block accumulation across several epoch
    boundaries while the decay curve runs, plus the blend-entry epochs
    where the top-up tail engages (Option B shipped parameters)."""
    naive = 0
    limit = (BLEND_EPOCH + 2) * HALVING_INTERVAL_BLOCKS + 1 if \
        BLEND_EPOCH is not None else 3 * HALVING_INTERVAL_BLOCKS + 2
    for h in range(1, limit + 1):
        naive += emission_at(h).total_new_shards
        assert naive == cumulative_supply(h), h


def test_cap_transition_boundary_hygiene():
    """Property hygiene for ANY parameterisation (the boundary itself does
    not exist under current D1-conflict params — see flag test above):
    whenever a tail block exists, the decay/tail handover has no gap or
    overlap and the tail rate matches D1's ≈0.6%/yr."""
    tr = (TOTAL_SUPPLY_SHARDS * TAIL_ANNUAL_RATE_BPS) // (10_000 * BLOCKS_PER_YEAR)
    annual = tr * BLOCKS_PER_YEAR
    assert abs(annual * 10_000 / TOTAL_SUPPLY_SHARDS - 60) < 1   # ≈60 bps/yr
    if CAP_HEIGHT is None:
        # indefinite decay: every reward-bearing block pays base > 0
        assert emission_at(1).total_new_shards > 0
        assert emission_at(10 * HALVING_INTERVAL_BLOCKS).total_new_shards > 0
        return
    assert base_reward(CAP_HEIGHT - 1) > 0        # last decay block pays base
    assert tail_reward(CAP_HEIGHT - 1) == 0       # ...and no tail yet
    assert base_reward(CAP_HEIGHT) == 0           # first tail block: no base
    assert tail_reward(CAP_HEIGHT) > 0            # ...only the fixed tail
    # every block still emits something (no zero-reward gap at the boundary)
    assert emission_at(CAP_HEIGHT - 1).total_new_shards > 0
    assert emission_at(CAP_HEIGHT).total_new_shards > 0


def test_cumulative_supply_monotonic():
    assert cumulative_supply(1000) > cumulative_supply(100)
    assert cumulative_supply(HALVING_INTERVAL_BLOCKS) <= TOTAL_SUPPLY_SHARDS


def test_pre_tail_supply_never_exceeds_cap():
    """The pure-decay leg (before the tail blend engages) stays under the
    hard cap at every height — Option B's convergence guarantee. Tail
    inflation beyond CAP_CROSS_HEIGHT is the intended D1 behaviour (fixed
    ≈0.6%/yr forever), not a bug."""
    limit = CAP_HEIGHT if CAP_HEIGHT is not None else BLEND_HEIGHT
    assert cumulative_supply(limit - 1) <= TOTAL_SUPPLY_SHARDS


def test_prsm_formatting():
    assert prsm(10**8) == "1"
    assert prsm(1) == "0.00000001"


# ---------------------------------------------------------- difficulty ---
def test_retarget_neutral_window():
    ts = [i * 120 for i in range(61)]
    assert retarget(1000, ts, 60, 120) == 1000


def test_retarget_fast_blocks_raise_diff():
    ts = [i * 60 for i in range(61)]        # blocks twice as fast
    assert retarget(1000, ts, 60, 120) > 1000


def test_retarget_clamped():
    ts = [i for i in range(61)]              # absurdly fast window
    d = retarget(1000, ts, 60, 120)
    assert d <= 1000 * 101 // 100 + 1        # ±1% clamp holds


def test_retarget_rejects_bad_input():
    with pytest.raises(ValueError):
        retarget(0, list(range(61)), 60, 120)
    with pytest.raises(ValueError):
        retarget(1000, [5] * 61, 60, 120)   # non-monotonic


def test_window_helper():
    w = DifficultyWindow(3)
    for t in (0, 120, 240):
        w.feed(t)
    assert not w.ready()
    w.feed(360)
    assert w.ready() and w.timestamps() == [0, 120, 240, 360]


# ---------------------------------------------------------------- pow ---
def test_placeholder_pow_roundtrip():
    pre = b"x" * 100
    n = PlaceholderSha3Pow.mine(pre, 1)
    assert n is not None and PlaceholderSha3Pow.verify(pre, n, 1)


def test_mainnet_refuses_placeholder_pow():
    from chain.params import MAINNET
    with pytest.raises(RuntimeError):
        assert_miner_backend(PlaceholderSha3Pow, MAINNET)


def test_viewkey_epochs_differ():
    vk_a = viewkey_for_height(0, ZERO32)
    vk_b = viewkey_for_height(750, ZERO32)
    assert vk_a != vk_b and len(vk_a) == 32


# -------------------------------------------------------------- block ---
def _hdr(**kw):
    base = dict(height=1, timestamp=1000, prev_hash=ZERO32, merkle_root=ZERO32,
                im_merkle_root=ZERO32, denylist_root=ZERO32, pow=PoWFields(),
                version_major=1, version_minor=0, version_vote=False, size_bytes=10)
    base.update(kw)
    return BlockHeader(**base)


def test_header_serialization_stable_and_hash_domain_separated():
    h = _hdr()
    assert h.serialize() == _hdr().serialize()
    assert len(h.hash()) == 32
    assert h.hash() != hashlib.sha3_256(h.serialize()).digest()


def test_version_vote_canonical_domain_gate():
    """§13 fuzz fixpoint regression: version_vote is uint8 with legal {0,1}.

    * direct construction normalizes 0/1 ints to bool (fixpoint preserved);
    * any other value — int or truthy object — is rejected at construction;
    * parse_header rejects non-canonical vote bytes via ValueError instead of
      bool()-coercing them (silent coercion would break serialize(parse(b))==b).
    """
    assert _hdr(version_vote=0).version_vote is False
    assert _hdr(version_vote=1).version_vote is True
    for bad in (2, 33, 255, -1):
        with pytest.raises(ValueError):
            _hdr(version_vote=bad)
    # wire-level: flip the vote byte to a non-canonical value → clean reject
    buf = bytearray(_hdr(version_vote=False).serialize())
    vote_off = 16 + 4 * 32 + 8 + 32 + 4          # after <HH of the version pair
    assert buf[vote_off] == 0
    buf[vote_off] = 33
    with pytest.raises(ValueError):
        parse_header(bytes(buf))
    # canonical 1 still round-trips exactly
    buf[vote_off] = 1
    hd = parse_header(bytes(buf))
    assert hd.version_vote is True and hd.serialize() == bytes(buf)


def test_denylist_root_is_consensus_field():
    """D5: header must carry a 32-byte root; wrong length rejected."""
    with pytest.raises(ValueError):
        _hdr(denylist_root=b"\x01" * 31).serialize()


def test_validate_header_rules():
    parent = _hdr(height=0, timestamp=1000)
    validate_header_basic(_hdr(height=1, timestamp=1120), parent)
    with pytest.raises(ValueError):
        validate_header_basic(_hdr(height=2, timestamp=1120), parent)     # height skip
    with pytest.raises(ValueError):
        validate_header_basic(_hdr(height=1, timestamp=999), parent)      # time back
    with pytest.raises(ValueError):
        validate_header_basic(_hdr(height=1, timestamp=10_000), parent)   # too future


def test_merkle_empty_vs_single_differ():
    assert merkle_root([]) != merkle_root([bytes(32)])


# ----------------------------------------------------------- full chain ---
def test_mine_and_validate_chain_refraction():
    p = REFRACTION_TESTNET
    state = ChainState(params=p, tip=make_genesis(p))
    assert state.tip.header.height == 0
    ts = p.genesis_timestamp + p.target_block_time          # synthetic clock
    for _ in range(4):
        blk = mine_block(p, state.tip, denylist_root=GENESIS_ACC,
                         im_merkle_root=state.tip.header.im_merkle_root,
                         difficulty=1, timestamp=ts)
        state.validate_and_apply(blk)
        ts += p.target_block_time
    assert state.tip.header.height == 4
    assert state.total_shards == sum(
        emission_at(h).total_new_shards for h in range(1, 5))


def test_tampered_block_rejected():
    p = REFRACTION_TESTNET
    state = ChainState(params=p, tip=make_genesis(p))
    blk = mine_block(p, state.tip, denylist_root=GENESIS_ACC,
                     im_merkle_root=ZERO32, difficulty=1,
                     timestamp=p.genesis_timestamp + p.target_block_time)
    bad = type(blk)(header=blk.header, txids=[b"\xff" * 32],
                    coinbase_commitment=blk.coinbase_commitment)
    with pytest.raises(ValueError):
        state.validate_and_apply(bad)


# ------------------------------------------------------------ denylist ---
def test_accumulator_replay_matches_store():
    store = DenylistStore()
    entries = [Entry(hashlib.sha3_256(str(i).encode()).digest()) for i in range(10)]
    root = store.apply_week(1, entries)
    assert root == fold(GENESIS_ACC, entries)
    listed, path = store.membership_witness(entries[3].tag)
    assert listed and len(path) == 10


def test_tombstone_last_write_wins():
    store = DenylistStore()
    tag = hashlib.sha3_256(b"bad-actor").digest()
    store.apply_week(1, [Entry(tag)])
    store.apply_week(2, [Entry(tag, remove=True)])
    listed, _ = store.membership_witness(tag)
    assert not listed


def test_signed_root_quorum():
    keys = [bytes([i]) * 32 for i in range(7)]
    r5 = SignedRoot(epoch=1, root=GENESIS_ACC, signer_pubkeys=keys[:5],
                    signatures=[b"s"] * 5)
    r4 = SignedRoot(epoch=1, root=GENESIS_ACC, signer_pubkeys=keys[:4],
                    signatures=[b"s"] * 4)
    dup = SignedRoot(epoch=1, root=GENESIS_ACC,
                     signer_pubkeys=keys[:4] + [keys[0]], signatures=[b"s"] * 5)
    other_root = SignedRoot(epoch=1, root=hashlib.sha3_256(b"other").digest(),
                            signer_pubkeys=keys[:5], signatures=[b"s"] * 5)
    assert r5.quorum_ok()
    assert not r4.quorum_ok()
    assert not dup.quorum_ok()          # duplicate signer rejected
    assert r5.message() != other_root.message()   # message binds the root


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
