"""Chain CLI — Phase 1 skeleton.

Usage:
  python3 -m chain.cli genesis   --network refraction
  python3 -m chain.cli emit      --heights 0,100,315360
  python3 -m chain.cli mine      --blocks 5            (placeholder PoW)
  python3 -m chain.cli params
"""
from __future__ import annotations

import argparse
import json
import sys

from . import __version__, SPEC_VERSION
from .emission import (CAP_HEIGHT, CAP_TRANSITION_EPOCH,
                     PRE_TAIL_PLATEAU_SHARDS, cumulative_supply, emission_at,
                     prsm)
from .node import ChainState, make_genesis, mine_block
from .params import NETWORKS, REFRACTION_TESTNET
from .pow import PlaceholderSha3Pow, assert_miner_backend


def _header_dict(b) -> dict:
    h = b.header
    return {
        "height": h.height,
        "hash": h.hash().hex(),
        "timestamp": h.timestamp,
        "prev_hash": h.prev_hash.hex(),
        "merkle_root": h.merkle_root.hex(),
        "im_merkle_root": h.im_merkle_root.hex(),
        "denylist_root": h.denylist_root.hex(),
        "version": {"major": h.version_major, "minor": h.version_minor,
                    "vote": h.version_vote},
        "size_bytes": h.size_bytes,
    }


def _d1_status() -> str:
    """Human-readable status of the D1 emission-curve conflict flag.

    With the spec-pinned constants the halving curve plateaus at ~8.4M PRSM
    and never approaches the 21M cap, so no tail transition is scheduled;
    see prism/chain/emission.py (D1 CONFLICT FLAG) for the three candidate
    resolutions awaiting a founder decision."""
    if CAP_HEIGHT is None:
        return ("OPEN-FLAG: decay curve plateaus at %s PRSM (< 21M cap); "
                "no tail transition scheduled — D1 resolution required"
                % prsm(PRE_TAIL_PLATEAU_SHARDS))
    return ("tail takes over at height %d (epoch %d)"
            % (CAP_HEIGHT, CAP_TRANSITION_EPOCH))


def cmd_genesis(a):
    p = NETWORKS[a.network]
    g = make_genesis(p)
    print(json.dumps({
        "network": p.name,
        "spec_version": SPEC_VERSION,
        "impl_version": __version__,
        "genesis": _header_dict(g),
        "premine_shards": 0,   # Decision D1 — asserted, not assumed
    }, indent=2))


def cmd_emit(a):
    rows = []
    for h in [int(x) for x in a.heights.split(",")]:
        e = emission_at(h)
        rows.append({
            "height": h,
            "base_prsm": prsm(e.base_shards),
            "tail_prsm": prsm(e.tail_shards),
            "miner_prsm": prsm(e.miner_shares),
            "devfund_prsm": prsm(e.dev_fund_shares),
            "cumulative_supply_prsm": prsm(cumulative_supply(h)),
            "d1_status": _d1_status(),
        })
    print(json.dumps(rows, indent=2))


def cmd_mine(a):
    """Mine N placeholder-PoW blocks at difficulty 1 and validate them.

    Timestamps advance by one target interval per block (synthetic clock) so
    the demo works regardless of the genesis timestamp's distance from now.
    """
    p = NETWORKS[a.network]
    assert_miner_backend(PlaceholderSha3Pow, p)  # raises on mainnet
    state = ChainState(params=p, tip=make_genesis(p))
    out = [_header_dict(state.tip)]
    ts = p.genesis_timestamp
    for _ in range(a.blocks):
        ts += p.target_block_time
        blk = mine_block(p, state.tip, denylist_root=state.tip.header.denylist_root,
                         im_merkle_root=state.tip.header.im_merkle_root,
                         difficulty=1, timestamp=ts)
        state.validate_and_apply(blk)
        out.append(_header_dict(blk))
    print(json.dumps({"tip_height": state.tip.header.height,
                      "total_emitted_prsm": prsm(state.total_shards),
                      "difficulty": state.difficulty,
                      "blocks": out}, indent=2))


def cmd_params(a):
    from . import params as P
    keys = [k for k in dir(P) if k.isupper()]
    print(json.dumps({k: getattr(P, k) for k in keys
                      if not isinstance(getattr(P, k), type(lambda: 0))},
                     indent=2, default=str))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="chain.cli", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("genesis"); g.add_argument("--network", default="refraction",
                                                  choices=list(NETWORKS)); g.set_defaults(fn=cmd_genesis)
    e = sub.add_parser("emit");    e.add_argument("--heights", required=True); e.set_defaults(fn=cmd_emit)
    m = sub.add_parser("mine");    m.add_argument("--blocks", type=int, default=3)
    m.add_argument("--network", default="refraction", choices=list(NETWORKS)); m.set_defaults(fn=cmd_mine)
    p = sub.add_parser("params");  p.set_defaults(fn=cmd_params)

    args = ap.parse_args(argv)
    args.fn(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
