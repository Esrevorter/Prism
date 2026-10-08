"""Block header & canonical serialization — spec.md v1.0 §6.1.

Canonical wire format: field-ordered concatenation (as listed below), each
field little-endian where numeric. Header hash = keccak-style domain-hashed
serialization (SHA3-256 from stdlib, domain-separated). The `denylist_root`
32-byte field is consensus-required per Decision D5 (Compliance Council
weekly accumulator root; see chain/denylist.py).
"""
from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass, field

ZERO32 = b"\x00" * 32


@dataclass(frozen=True)
class PoWFields:
    """RandomX proof fields embedded in the header (§6.1 pow object)."""
    nonce: bytes = field(default_factory=lambda: bytes(8))     # uint64 LE
    viewkey_hash: bytes = ZERO32    # RandomX key: sha3 of (height//epoch) seed
    # difficulty target is derived by the node from consensus state, not
    # stored per-block (prevents target-lowering tricks); kept out of header.

    def serialize(self) -> bytes:
        assert len(self.nonce) == 8 and len(self.viewkey_hash) == 32
        return self.nonce + self.viewkey_hash


@dataclass(frozen=True)
class BlockHeader:
    height: int                 # uint64
    timestamp: int              # uint64 unix seconds
    prev_hash: bytes            # 32
    merkle_root: bytes          # 32  (txs + miner reward)
    im_merkle_root: bytes       # 32  (key image accumulator)
    denylist_root: bytes        # 32  (D5: weekly council-signed accumulator)
    pow: PoWFields
    version_major: int          # uint16
    version_minor: int          # uint16
    version_vote: bool          # uint8 (0/1) — fork signaling (§4.2)
    size_bytes: int             # uint32 actual serialized block size

    def serialize(self) -> bytes:
        for name, v in (("prev_hash", self.prev_hash),
                        ("merkle_root", self.merkle_root),
                        ("im_merkle_root", self.im_merkle_root),
                        ("denylist_root", self.denylist_root)):
            if len(v) != 32:
                raise ValueError(f"{name} must be 32 bytes")
        head = struct.pack(
            "<QQ", self.height, self.timestamp
        )
        hashes = self.prev_hash + self.merkle_root + self.im_merkle_root + self.denylist_root
        ver = struct.pack("<HHB", self.version_major, self.version_minor,
                          int(self.version_vote))
        size = struct.pack("<I", self.size_bytes)
        return head + hashes + self.pow.serialize() + ver + size

    def hash(self) -> bytes:
        """Domain-separated header hash (block id)."""
        return hashlib.sha3_256(b"PRISM-BLOCK-V1" + self.serialize()).digest()


def validate_header_basic(h: BlockHeader, parent: BlockHeader,
                          tolerance_blocks: int = 2,
                          target_seconds: int = 120) -> None:
    """Consensus-lite checks from §4.2: monotonic height, timestamp bounds.

    Full validation (PoW, merkle, ring signatures) lives in the block
    validator once crypto/ lands; this guards the cheap invariants now.
    """
    if h.height != parent.height + 1:
        raise ValueError(f"height discontinuity: {parent.height} -> {h.height}")
    max_future = tolerance_blocks * target_seconds
    if h.timestamp <= parent.timestamp:
        raise ValueError("non-monotonic timestamp")
    if h.timestamp > parent.timestamp + max_future + target_seconds:
        raise ValueError("timestamp too far in future")
