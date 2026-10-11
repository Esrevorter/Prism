"""PoW integration boundary — spec.md v1.0 §4.2 (RandomX).

PLACEHOLDER MODULE: implements the *ABI* around RandomX so the rest of the
node code (block validation, difficulty, mining loop) can be built and tested
now, with librandomx dropped in later behind `RandomXBackend`.

Phase-1 placeholder algorithm ("sha3-diff"): hash must be below a target
derived from difficulty. This is deliberately NOT ASIC-resistant and MUST be
rejected for mainnet by `assert_miner_backend()`.

RandomX specifics preserved in the ABI:
- per-epoch seed → `viewkey_hash` (header field), epoch = 64 blocks
  (§4.2 Dandelion epochs are separate; RandomX key epochs follow Monero's
  ~every-750-blocks pattern, parameterized here as RX_KEY_EPOCH_BLOCKS).
- dataset init CPU-only, light-client verification mode supported.
"""
from __future__ import annotations

import hashlib

RX_KEY_EPOCH_BLOCKS = 750


def viewkey_for_height(height: int, chain_seed: bytes) -> bytes:
    """RandomX 'view key' = deterministic seed per key epoch."""
    epoch = height // RX_KEY_EPOCH_BLOCKS
    return hashlib.sha3_256(
        b"PRISM-RX-SEED-V1" + epoch.to_bytes(8, "little") + chain_seed
    ).digest()


class PlaceholderSha3Pow:
    """Temporary dev/test PoW. sha3(header_without_nonce||nonce) < target."""

    name = "sha3-diff (PLACEHOLDER — never mainnet)"

    @staticmethod
    def target_from_difficulty(diff: int) -> int:
        # 2^256 / diff, integer floor; diff>=1
        return (1 << 256) // max(1, diff)

    @classmethod
    def verify(cls, header_prehash: bytes, nonce: int, diff: int) -> bool:
        h = int.from_bytes(
            hashlib.sha3_256(header_prehash + nonce.to_bytes(8, "little")).digest(),
            "big",
        )
        return h < cls.target_from_difficulty(diff)

    @classmethod
    def mine(cls, header_prehash: bytes, diff: int, max_tries: int = 5_000_000):
        """Returns nonce or None. Trivially slow at high difficulty — fine
        for unit tests, useless in production (that's the point of the stub)."""
        target = cls.target_from_difficulty(diff)
        for n in range(max_tries):
            h = int.from_bytes(
                hashlib.sha3_256(header_prehash + n.to_bytes(8, "little")).digest(),
                "big",
            )
            if h < target:
                return n
        return None


class RandomXBackend:
    """FFI boundary for real librandomx (Phase 1 hardhat task).

    Wiring plan: ctypes/cffi against monero-project/randomx-cpp-tests build;
    same verify/mine surface as PlaceholderSha3Pow.
    """

    name = "randomx"

    def verify(self, header_prehash: bytes, nonce: int, diff: int) -> bool:
        raise NotImplementedError("librandomx FFI not yet linked")

    def mine(self, header_prehash: bytes, diff: int):
        raise NotImplementedError("librandomx FFI not yet linked")


def assert_miner_backend(backend, network_params) -> None:
    """Guard rail: mainnet refuses placeholder PoW (spec §13 security gate)."""
    if backend.name.startswith("sha3-diff") and not network_params.pow_placeholder:
        raise RuntimeError(
            f"{network_params.name} requires real RandomX; placeholder PoW blocked"
        )
