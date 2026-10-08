"""Adaptive difficulty — spec.md v1.0 §4.1/§4.2 (DCR-style retarget).

Retargets every `window` blocks over the last `window+1` timestamps, with a
±1% per-retarget clamp to blunt timestamp-smoothing attacks. Integer-only
arithmetic; no floats in consensus code.
"""
from __future__ import annotations

from .params import DIFF_CLAMP_NUM, DIFF_CLAMP_DEN


def retarget(prev_diff: int, timestamps: list[int], window: int,
             target_seconds: int) -> int:
    """Compute next-window difficulty from block timestamps.

    Args:
        prev_diff: difficulty for the just-completed window (>0).
        timestamps: len == window + 1, oldest..newest, of the window boundary
            blocks (Monero/WooDowdy convention).
        window: retarget interval in blocks.
        target_seconds: desired inter-block time.

    Raises:
        ValueError on malformed input or non-monotonic timestamps.
    """
    if prev_diff <= 0:
        raise ValueError("prev_diff must be positive")
    if len(timestamps) != window + 1:
        raise ValueError(f"need exactly {window + 1} timestamps")
    if any(b <= a for a, b in zip(timestamps, timestamps[1:])):
        raise ValueError("timestamps must be strictly increasing")

    elapsed = timestamps[-1] - timestamps[0]
    ideal = window * target_seconds

    # Clamp the adjustment to ±1% so a hostile window can't swing diff hard.
    lo = (ideal * DIFF_CLAMP_DEN) // DIFF_CLAMP_NUM
    hi = (ideal * DIFF_CLAMP_NUM) // DIFF_CLAMP_NUM
    elapsed = max(lo, min(hi, elapsed))

    new_diff = (prev_diff * ideal) // elapsed
    return max(1, new_diff)


class DifficultyWindow:
    """Tracks timestamps across a retarget window (node-side helper)."""

    def __init__(self, window: int):
        self.window = window
        self._ts: list[int] = []

    def feed(self, timestamp: int) -> None:
        self._ts.append(timestamp)
        if len(self._ts) > self.window + 1:
            self._ts.pop(0)

    def ready(self) -> bool:
        return len(self._ts) == self.window + 1

    def timestamps(self) -> list[int]:
        return list(self._ts)
