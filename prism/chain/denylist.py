"""Denylist accumulator — spec.md v1.0 §5.3(1), Decision D5.

The sanctioned-output set is a *hash-chain accumulator* over output tags:
    acc_0 = GENESIS_ACC
    acc_{i+1} = H(acc_i || tag_i)          (append-only, order-committed)

Each week the Compliance Council (7 seats, ≥5-of-7 signatures) publishes a
new root; miners embed the latest accepted root in every block header's
`denylist_root` field (§6.1). Provenance circuits (§5.3) prove non-membership
against the accumulator via witness inclusion paths.

Design notes:
- Append-only + hash-chained ⇒ anyone can replay the full list and verify
  the root; no council trust needed for verification, only for *ordering*.
- Removals are tombstone entries (tag re-listed with `remove` flag committed
  into the chain), never silent edits — preserves auditability.
- Verifiers may pin third-party lists instead (D5: council roots optional).
"""
from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass, field

GENESIS_ACC = hashlib.sha3_256(b"PRISM-DENYLIST-ACC-V1").digest()


@dataclass(frozen=True)
class Entry:
    """One accumulator delta. tag = 32-byte output tag (stealth pubkey hash)."""
    tag: bytes
    remove: bool = False

    def commit_bytes(self) -> bytes:
        if len(self.tag) != 32:
            raise ValueError("tag must be 32 bytes")
        return self.tag + b"\x01" if self.remove else self.tag + b"\x00"


def step(acc: bytes, entry: Entry) -> bytes:
    """Fold one entry into the accumulator."""
    return hashlib.sha3_256(b"PRISM-DENY-V1" + acc + entry.commit_bytes()).digest()


def fold(acc: bytes, entries: list[Entry]) -> bytes:
    for e in entries:
        acc = step(acc, e)
    return acc


@dataclass
class SignedRoot:
    """Weekly council-signed root announcement (off-chain artifact that
    miners reference by embedding the root in headers)."""
    epoch: int                 # weekly sequence number
    root: bytes                # 32-byte accumulator after this week's entries
    signer_pubkeys: list[bytes] = field(default_factory=list)  # council seats
    signatures: list[bytes] = field(default_factory=list)      # Ed25519-style

    def message(self) -> bytes:
        return struct.pack("<Q", self.epoch) + b"PRISM-DL-ROOT-V1" + self.root

    def quorum_ok(self, seats: int = 7, quorum: int = 5) -> bool:
        """Structural check only — real signature verification lands with
        crypto/ (Phase 1 task). Also rejects duplicate signers."""
        uniq = set(self.signer_pubkeys)
        if len(uniq) != len(self.signer_pubkeys):
            return False
        if len(self.signatures) != len(self.signer_pubkeys):
            return False
        return seats == 7 and len(uniq) >= quorum


class DenylistStore:
    """Replayable store of weekly deltas; computes roots deterministically."""

    def __init__(self) -> None:
        self._weeks: list[tuple[int, list[Entry]]] = []
        self._acc = GENESIS_ACC

    def apply_week(self, epoch: int, entries: list[Entry]) -> bytes:
        if self._weeks and epoch <= self._weeks[-1][0]:
            raise ValueError("epochs must strictly increase")
        self._acc = fold(self._acc, entries)
        self._weeks.append((epoch, list(entries)))
        return self._acc

    @property
    def current_root(self) -> bytes:
        return self._acc

    def membership_witness(self, tag: bytes) -> tuple[bool, list[bytes]]:
        """Brute-scan witness for Phase 1 (fine at testnet scale).
        Replaced by indexed Merkle mountain range in Phase 2.

        Returns (listed_now, fold_path). Tombstones are last-write-wins:
        a tag re-listed with remove=True ends up NOT listed.
        """
        acc = GENESIS_ACC
        path: list[bytes] = []
        listed = False
        for _epoch, entries in self._weeks:
            for e in entries:
                if e.tag == tag:
                    listed = not e.remove
                path.append(acc)
                acc = step(acc, e)
        return listed, path
