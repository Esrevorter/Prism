"""Chain assembly: genesis + block validation pipeline (Phase 1).

Wires params/emission/difficulty/block/pow/denylist into a minimal but
real consensus check sequence. Tx-level rules (RingCT, key images) plug in
when crypto/ lands — marked TODO(§5.1).
"""
from __future__ import annotations

import dataclasses
import hashlib
from dataclasses import dataclass, field

from .block import BlockHeader, PoWFields, ZERO32, validate_header_basic
from .difficulty import retarget
from .emission import emission_at
from .params import NetworkParams, DENYLIST_WEEKLY_BLOCKS
from .pow import PlaceholderSha3Pow, viewkey_for_height


def merkle_pair(a: bytes, b: bytes) -> bytes:
    return hashlib.sha3_256(b"PRISM-MK-V1" + a + b).digest()


def merkle_root(leaves: list[bytes]) -> bytes:
    """Standard odd-duplicate-last pair tree; empty set → domain H of zeros."""
    if not leaves:
        return hashlib.sha3_256(b"PRISM-MK-EMPTY").digest()
    level = list(leaves)
    while len(level) > 1:
        if len(level) % 2:
            level.append(level[-1])
        level = [merkle_pair(a, b) for a, b in zip(level[0::2], level[1::2])]
    return level[0]


@dataclass
class Block:
    header: BlockHeader
    txids: list[bytes] = field(default_factory=list)   # excludes coinbase
    coinbase_commitment: bytes = ZERO32                # miner+devfund outputs

    def merkle(self) -> bytes:
        return merkle_root(self.txids + [self.coinbase_commitment])


def _header_pre_nonce(h: BlockHeader, merkle: bytes) -> bytes:
    """Serialize header fields with the given merkle root and WITHOUT the
    nonce — this is the PoW hash preimage (§4.2)."""
    import struct
    pow_no_nonce = h.pow.viewkey_hash
    return struct.pack("<QQ", h.height, h.timestamp) + \
        h.prev_hash + merkle + h.im_merkle_root + h.denylist_root + \
        pow_no_nonce + struct.pack("<HHB", h.version_major, h.version_minor,
                                   int(h.version_vote)) + struct.pack("<I", h.size_bytes)


def mine_block(params: NetworkParams, prev: Block, denylist_root: bytes,
               im_merkle_root: bytes, difficulty: int,
               txids: list[bytes] | None = None,
               timestamp: int | None = None) -> Block:
    """CPU-mine one block with the placeholder backend (testnet/dev only).

    The header's merkle_root is filled in as part of assembly, so PoW runs
    over the final content — no chicken-and-egg.
    """
    from time import time
    import struct
    txids = txids or []
    ts = timestamp if timestamp is not None else int(time())
    cb = _coinbase_commitment(prev.header.height + 1, params)
    base = BlockHeader(
        height=prev.header.height + 1,
        timestamp=ts,
        prev_hash=prev.header.hash(),
        merkle_root=ZERO32,  # placeholder; recomputed below
        im_merkle_root=im_merkle_root,
        denylist_root=denylist_root,
        pow=PoWFields(nonce=bytes(8),
                      viewkey_hash=viewkey_for_height(prev.header.height + 1,
                                                      prev.header.hash())),
        version_major=1, version_minor=0, version_vote=False,
        size_bytes=0,
    )
    mr = merkle_root(txids + [cb])
    h = dataclasses.replace(base, merkle_root=mr)
    pre = _header_pre_nonce(h, mr)
    nonce = PlaceholderSha3Pow.mine(pre, difficulty)
    if nonce is None:
        raise RuntimeError("no nonce found within budget — lower difficulty")
    powf = PoWFields(nonce=struct.pack("<Q", nonce), viewkey_hash=h.pow.viewkey_hash)
    hh = dataclasses.replace(h, pow=powf)
    return Block(header=hh, txids=txids, coinbase_commitment=cb)


def _coinbase_commitment(height: int, params: NetworkParams) -> bytes:
    """Domain-separated commitment to (miner reward, dev-fund carve-out).

    Real Pedersen output commitments land with crypto/ (TODO §5.1); this
    binds the emission schedule into the merkle tree deterministically.
    """
    em = emission_at(height)
    payload = (b"PRISM-COINBASE-V1" + str(height).encode()
               + str(em.miner_shares).encode() + str(em.dev_fund_shares).encode()
               + params.name.encode())
    return hashlib.sha3_256(payload).digest()


def make_genesis(params: NetworkParams, denylist_root: bytes = ZERO32) -> Block:
    """Zero-premine genesis (D1): no value output; only marker fields.

    Height 0 is a synthetic block: reward at height 0 goes to nobody (Monero
    precedent), so supply starts at 0 and first spendable coinbase is height 1.
    """
    header = BlockHeader(
        height=0,
        timestamp=params.genesis_timestamp,
        prev_hash=ZERO32,
        merkle_root=hashlib.sha3_256(
            f"PRISM GENESIS {params.name}".encode()).digest(),
        im_merkle_root=merkle_root([]),
        denylist_root=denylist_root,
        pow=PoWFields(nonce=bytes(8), viewkey_hash=viewkey_for_height(0, ZERO32)),
        version_major=1, version_minor=0, version_vote=False,
        size_bytes=0,
    )
    return Block(header=header, txids=[], coinbase_commitment=ZERO32)


@dataclass
class ChainState:
    params: NetworkParams
    tip: Block
    difficulty: int = 1
    _ts_window: list[int] = field(default_factory=list)
    total_shards: int = 0  # tracked supply (genesis contributes 0)

    def validate_and_apply(self, blk: Block) -> None:
        p = self.params
        validate_header_basic(blk.header, self.tip.header,
                              target_seconds=p.target_block_time)
        # merkle integrity
        mr = merkle_root(blk.txids + [blk.coinbase_commitment])
        if mr != blk.header.merkle_root:
            raise ValueError("merkle root mismatch")
        # PoW
        pre = _header_pre_nonce(blk.header, mr)
        nonce = int.from_bytes(blk.header.pow.nonce, "little")
        backend = PlaceholderSha3Pow  # swapped by RandomXBackend via FFI later
        if not backend.verify(pre, nonce, self.difficulty):
            raise ValueError("bad PoW")
        # emission accounting (§4.1 cap enforcement comes with RingCT sums)
        em = emission_at(blk.header.height)
        self.total_shards += em.total_new_shards
        # apply
        self.tip = blk
        self._ts_window.append(blk.header.timestamp)
        if len(self._ts_window) > p.diff_window + 1:
            self._ts_window.pop(0)
        if len(self._ts_window) == p.diff_window + 1:
            self.difficulty = retarget(self.difficulty, self._ts_window,
                                       p.diff_window, p.target_block_time)

    def next_denylist_epoch(self) -> int:
        return self.tip.header.height // DENYLIST_WEEKLY_BLOCKS
