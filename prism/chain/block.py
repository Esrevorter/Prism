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

    def __post_init__(self):
        # Canonical-domain gate (fuzz fixpoint, §13 'chain parsing'): the wire
        # field is a single byte that must be exactly 0 or 1. Python's bool()
        # coercion would happily map e.g. vote byte 2 → True, so re-serializing
        # yields a DIFFERENT blob and breaks the parse→serialize round-trip
        # invariant. Non-canonical votes therefore never enter the domain:
        #   * via parse_header — the raw byte is checked first and rejected as
        #     a ValueError (untrusted network input MUST NOT coerce silently);
        #   * via direct construction — normalized here so every constructed
        #     header satisfies serialize(parse(x)) == x for exact-length input.
        if isinstance(self.version_vote, bool):
            return
        if self.version_vote in (0, 1):
            object.__setattr__(self, "version_vote", bool(self.version_vote))
            return
        raise ValueError(
            f"version_vote must be 0/1 (uint8 canonical domain), "
            f"got {self.version_vote!r}")

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


# Fixed wire length: <QQ (16) + 4*32 hashes + pow (8+32) + <HHB (5) + <I (4)
HEADER_WIRE_LEN = 16 + 4 * 32 + 8 + 32 + 5 + 4


def parse_header(buf: bytes) -> BlockHeader:
    """Strict inverse of BlockHeader.serialize() — the untrusted-network
    decode path for the §13 fuzz gate ('chain parsing').

    Total-function contract: returns a BlockHeader or raises ValueError; no
    silent truncation, no trailing-byte acceptance, exact-length framing.
    """
    buf = bytes(buf)  # normalise bytearray/memoryview → immutable bytes
    if len(buf) != HEADER_WIRE_LEN:
        raise ValueError(
            f"header must be exactly {HEADER_WIRE_LEN} bytes, got {len(buf)}")
    height, timestamp = struct.unpack_from("<QQ", buf, 0)
    off = 16
    prev_hash = buf[off:off + 32]
    merkle_root = buf[off + 32:off + 64]
    im_merkle_root = buf[off + 64:off + 96]
    denylist_root = buf[off + 96:off + 128]
    off += 128
    powf = PoWFields(nonce=buf[off:off + 8], viewkey_hash=buf[off + 8:off + 40])
    off += 40
    version_major, version_minor, vote = struct.unpack_from("<HHB", buf, off)
    off += 5
    size_bytes = struct.unpack_from("<I", buf, off)[0]
    # Canonicalization gate (§13 fuzz fixpoint): the wire byte must be exactly
    # 0 or 1. Silently bool()-coercing a stray value (e.g. 2) would make
    # serialize(parse(buf)) != buf — a hidden malleability channel. We instead
    # accept only canonical votes and fold every other byte into the documented
    # rejection path (ValueError), so parse→serialize is a strict fixpoint on
    # its whole domain while same-size mutations never desync field boundaries.
    if vote not in (0, 1):
        raise ValueError(
            f"non-canonical version_vote byte: {vote} (must be 0/1)")
    return BlockHeader(height=height, timestamp=timestamp,
                       prev_hash=prev_hash, merkle_root=merkle_root,
                       im_merkle_root=im_merkle_root,
                       denylist_root=denylist_root, pow=powf,
                       version_major=version_major,
                       version_minor=version_minor,
                       version_vote=vote, size_bytes=size_bytes)


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
