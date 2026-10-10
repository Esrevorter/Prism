#!/usr/bin/env python3
"""Scaled-up spend-to-self property run (spec §5.1 / Phase-1 RingCT gate).

Acceptance criterion: "spend-to-self loop passes property tests >= 10^6
iterations."  The in-suite pytest test (test_crypto.py::
test_spend_to_self_property) covers 64 seeds inside the normal run; this
script runs the SAME four-phase invariant for N iterations across worker
processes using a fast, differential-tested group path.

Per iteration ("one iteration" == one full 4-phase case, like one pytest
seed):
  1. POSITIVE  - random valid tx (values AND blinds cancel) =>
                 SUM(commit(ins)) - SUM(commit(outs)) == identity.
  2. NEGATIVE  - only values cancel (blind perturbed)      => NOT identity.
  3. NEGATIVE  - only blinds cancel (value perturbed)      => NOT identity.
  4. NEGATIVE  - neither cancels                            => NOT identity.

Group arithmetic uses prism.property.windowed_mul (width-4 fixed-base
windows), which is differentially verified against the pure-Python
reference ladder in prism/tests/test_property_windowed.py and at import
time.  Additionally, every `cross`-th iteration (default 256) re-checks
the POSITIVE phase with the SLOW reference commit() itself, so the real
production code path is exercised ~3900 times during a 10^6 run while
keeping total runtime feasible on this sandbox.

Usage:
    python -m prism.property.scaled_spend_to_self [N] [workers] [cross]
Defaults: N = 1_000_000, workers = cpu_count, cross = 256.

Exit code 0 on success; any assertion violation aborts non-zero with the
failing seed printed.
"""
from __future__ import annotations

import multiprocessing as mp
import os
import random
import sys
import time

from prism.crypto.edwards import encode
from prism.crypto.field import L
from prism.crypto.pedersen import commit as ref_commit, parse_commitment
from prism.property.windowed_mul import commit_fast


def _commit_sum(pairs, fn):
    total = fn(pairs[0][0], pairs[0][1])
    for v, r in pairs[1:]:
        total = total.add(fn(v, r))
    return total


def _balances(ins, outs):
    values_cancel = sum(v for v, _ in ins) == sum(v for v, _ in outs)
    blinds_cancel = (sum(r % L for _, r in ins) % L
                     == sum(r % L for _, r in outs) % L)
    return values_cancel, blinds_cancel


def run_case(seed: int, cross: int) -> None:
    """One property iteration - same invariant structure as pytest test."""
    rng = random.Random(seed)

    # ---- positive: random valid spend-to-self tx -------------------------
    n_in = rng.randint(1, 3)
    in_vals = [rng.randrange(1, 1 << 40) for _ in range(n_in)]
    in_masks = [rng.randrange(1, L) for _ in range(n_in)]
    total_val = sum(in_vals)
    total_mask = sum(in_masks) % L

    n_out = rng.randint(1, 3)
    out_vals = []
    remaining = total_val
    for _ in range(n_out - 1):
        v = rng.randrange(0, remaining + 1)
        out_vals.append(v)
        remaining -= v
    out_vals.append(remaining)
    out_masks = [rng.randrange(1, L) for _ in range(n_out - 1)]
    out_masks.append((total_mask - sum(out_masks)) % L)

    ins = list(zip(in_vals, in_masks))
    outs = list(zip(out_vals, out_masks))
    vc, bc = _balances(ins, outs)
    assert vc and bc, f"seed {seed}: construction sanity failed"
    assert _commit_sum(ins, commit_fast).sub(
        _commit_sum(outs, commit_fast)).is_identity(), \
        f"seed {seed}: valid tx failed balance invariant"

    if seed % cross == 0:
        # slow-path cross-check with the PRODUCTION reference commit()
        s_ref = _commit_sum(ins, ref_commit).sub(_commit_sum(outs, ref_commit))
        assert s_ref.is_identity(), f"seed {seed}: reference-ladder imbalance"
        enc_in = encode(_commit_sum(ins, commit_fast))
        enc_out = encode(_commit_sum(outs, commit_fast))
        assert parse_commitment(enc_in) == parse_commitment(enc_out), \
            f"seed {seed}: wire round-trip mismatch"

    # ---- negative: only VALUES cancel (blinds perturbed) ------------------
    bad_mask = rng.randrange(1, L)
    outs_blind_break = ([(v, r) for (v, r) in outs[:-1]]
                        + [(outs[-1][0], (outs[-1][1] + bad_mask) % L)])
    vc, bc = _balances(ins, outs_blind_break)
    assert vc and not bc, f"seed {seed}: blind-break balances mis-classified"
    assert not _commit_sum(ins, commit_fast).sub(
        _commit_sum(outs_blind_break, commit_fast)).is_identity(), \
        f"seed {seed}: blind-break unexpectedly balanced"

    # ---- negative: only BLINDS cancel (values perturbed) ------------------
    delta = rng.randrange(1, 1 << 20)
    outs_value_break = [(outs[0][0] + delta, outs[0][1])] + outs[1:]
    vc, bc = _balances(ins, outs_value_break)
    assert not vc and bc, f"seed {seed}: value-break balances mis-classified"
    assert not _commit_sum(ins, commit_fast).sub(
        _commit_sum(outs_value_break, commit_fast)).is_identity(), \
        f"seed {seed}: value-break unexpectedly balanced"

    # ---- negative: NEITHER cancels ----------------------------------------
    outs_both_break = [(outs[0][0] + delta, (outs[0][1] + bad_mask) % L)] + outs[1:]
    vc, bc = _balances(ins, outs_both_break)
    assert not vc and not bc, f"seed {seed}: both-break balances mis-classified"
    assert not _commit_sum(ins, commit_fast).sub(
        _commit_sum(outs_both_break, commit_fast)).is_identity(), \
        f"seed {seed}: both-break unexpectedly balanced"


def _worker(args):
    lo, hi, cross = args
    for seed in range(lo, hi):
        run_case(seed, cross)
    return hi - lo


def main(argv):
    n = int(argv[1]) if len(argv) > 1 else 1_000_000
    workers = int(argv[2]) if len(argv) > 2 else (os.cpu_count() or 1)
    cross = int(argv[3]) if len(argv) > 3 else 256
    chunk = (n + workers - 1) // workers
    ranges = [(i * chunk, min((i + 1) * chunk, n), cross)
              for i in range(workers)]
    ranges = [(lo, hi, c) for lo, hi, c in ranges if lo < hi]

    print(f"scaled spend-to-self property run: {n:,} iterations x 4 phases, "
          f"{len(ranges)} worker(s), reference cross-check every {cross}",
          flush=True)
    t0 = time.perf_counter()
    with mp.Pool(len(ranges)) as pool:
        done = sum(pool.map(_worker, ranges))
    dt = time.perf_counter() - t0
    assert done == n, f"only {done} of {n} iterations completed"
    print(f"PASSED: {done:,} iterations in {dt:.1f}s "
          f"({done / dt:,.0f} it/s). No invariant violations.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
