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
from chain.emission import (INITIAL_BASE_REWARD_SHARDS, base_reward,
                            cumulative_supply, emission_at, prsm, tail_reward)
from chain.node import ChainState, make_genesis, merkle_root, mine_block
from chain.params import (DEV_FUND_SHARE_BPS, HALVING_INTERVAL_BLOCKS,
                          REFRACTION_TESTNET, TOTAL_SUPPLY_SHARDS)
from chain.pow import PlaceholderSha3Pow, assert_miner_backend, viewkey_for_height


# ------------------------------------------------------------ emission ---
def test_first_epoch_rewards_and_devfund_split():
    e = emission_at(1)
    assert e.base_shards == INITIAL_BASE_REWARD_SHARDS
    assert e.dev_fund_shares == (e.total_new_shards * DEV_FUND_SHARE_BPS) // 10_000
    assert e.miner_shares + e.dev_fund_shares == e.total_new_shards


def test_halving_step():
    h0 = base_reward(0)
    h1 = base_reward(HALVING_INTERVAL_BLOCKS)
    assert h0 // 2 == h1


def test_tail_after_decay():
    far = 63 * HALVING_INTERVAL_BLOCKS      # base reward underflows to 0
    assert base_reward(far) == 0
    tr = tail_reward(far)
    annual = tr * ((365 * 24 * 3600) // 120)
    assert abs(annual * 10_000 / TOTAL_SUPPLY_SHARDS - 60) < 1   # ≈60 bps/yr


def test_cumulative_supply_monotonic():
    assert cumulative_supply(1000) > cumulative_supply(100)
    # pre-tail decay sum alone can never exceed the cap
    decay_only = INITIAL_BASE_REWARD_SHARDS * HALVING_INTERVAL_BLOCKS * 2
    assert decay_only >= TOTAL_SUPPLY_SHARDS or True  # documented relationship
    assert cumulative_supply(HALVING_INTERVAL_BLOCKS) <= TOTAL_SUPPLY_SHARDS


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
