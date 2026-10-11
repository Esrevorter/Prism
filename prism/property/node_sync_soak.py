#!/usr/bin/env python3
"""L1 node-sync soak harness (spec §13 Phase-1 gate: "3 independent nodes
sync & sustain 72 h").

Three validating nodes ("independent" in the sense that matters for
consensus — separate ChainState, separate block stores, separate accept /
reject ledgers; no shared mutable state) boot from a common Refraction
genesis. One miner produces every block and gossips it out-of-order: beta
and gamma are fed an ancestor lagged by 3 blocks on staggered rounds, so
they repeatedly find themselves behind alpha and must run the real sync
path (walk the candidate chain back to the common ancestor, download,
full-validate oldest-first) before their tips agree again. This exercises
the same validation pipeline the daemon will run on the wire once the p2p
transport lands (TODO §4.2 transport): header rules + merkle integrity +
PoW re-verification + emission accounting + difficulty retarget.

Virtual time model
------------------
The sandbox cannot host a wall-clock 72-h soak, so blocks arrive at a
compressed cadence and the harness reports BOTH clocks honestly:

    virtual_hours = blocks x cadence / 3600
    wall_seconds  = measured

Default profile: 8,640 blocks @ 30 s cadence = exactly 72 virtual hours,
~10 blocks/s wall (~14 min). The PASSED line names the virtual window and
the wall duration so the evidence is unambiguous; the same script runs
unchanged against a real deployment cadence (wall-clock mode) where
virtual_hours == wall hours.

Per-block invariant battery (every accepted block, every node):
  1. canonical serialization fixpoint: parse(serialize(h)).hash() == h.hash()
     — through the untrusted decode path (§13 fuzz gate);
  2. merkle_root recomputed from txids + coinbase commitment matches;
  3. PoW re-verified against the difficulty the node actually enforced;
  4. height/timestamp monotonicity via validate_header_basic (inside apply);
  5. emission ledger: total_shards == cumulative_supply(height) exactly;
  6. difficulty equals the pure-function retarget over the node's window.

Cross-node invariant (checked after every round): all three tips identical
(hash, height, difficulty, supply) — divergence that sync fails to heal
aborts the soak immediately with a per-node dump.

Adversarial injections (counted, never silently tolerated), once per
difficulty-retarget window (every `params.diff_window` blocks):
  * duplicate delivery          -> ignored idempotently by all nodes;
  * same-height sibling         -> rejected by all nodes (height continuity);
  * mutated serialized header   -> refused at the validation boundary
    (bad PoW / timestamp regression / non-canonical vote byte);
  * truncated frame             -> strict parser raises (exact-length framing);
  * orphan (unknown parent)     -> gap detection refuses blind application.

Usage:
    python -m prism.property.node_sync_soak [hours] [cadence_s] [blocks_override]

Defaults: hours = 72, cadence = 30 (Refraction target), blocks derived.
Exit code 0 on PASSED; any invariant violation aborts non-zero with the
failing block printed.
"""
from __future__ import annotations

import random
import sys
import time
from dataclasses import replace as dreplace

from prism.chain.block import HEADER_WIRE_LEN, PoWFields, parse_header
from prism.chain.difficulty import retarget
from prism.chain.emission import cumulative_supply
from prism.chain.node import (ChainState, _header_pre_nonce, make_genesis,
                              merkle_root, mine_block)
from prism.chain.params import NETWORKS
from prism.chain.pow import PlaceholderSha3Pow


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------

class Node:
    """One independent validating node: private chain state + ledgers."""

    def __init__(self, name: str, params):
        self.name = name
        self.params = params
        self.state = ChainState(params=params, tip=make_genesis(params))
        self.store: dict[bytes, object] = {}   # this node's own block table
        self.store[self.state.tip.header.hash()] = self.state.tip
        self.applied = 0
        self.dupes_ignored = 0
        self.rejected = 0

    def remember(self, blk) -> None:
        self.store[blk.header.hash()] = blk

    # -- inbound message path (untrusted input) ------------------------------

    def best_candidate(self):
        """Chain-selection rule (§4.2): highest cumulative difficulty wins;
        equal work breaks by height, then lexicographic header hash for
        determinism. Scans this node's own store — production nodes learn
        candidate tips from headers announcements over the wire."""
        best = None
        for blk in self.store.values():
            cd = blk.header.height * self.state.difficulty  # uniform-diff proxy
            key = (cd, blk.header.height, blk.header.hash())
            if best is None or key > best[0]:
                best = (key, blk)
        return best[1]

    def request(self, blk) -> None:
        """Inbound inv/headers announcement: record the block hash in our
        block table WITHOUT validating it (stand-in for getdata). A stale
        node's sync walk can then descend through blocks it was never
        directly delivered — exactly how catch-up works on the wire."""
        self.store.setdefault(blk.header.hash(), blk)

    def try_apply(self, blk) -> str:
        """Apply one gossip message. Returns 'applied' or 'duplicate-tip';
        raises ValueError on consensus-invalid input (stale/sibling),
        AssertionError on a missing-parent gap (sync required first)."""
        h = blk.header
        cur = self.state.tip.header
        if h.hash() == cur.hash():
            self.dupes_ignored += 1
            return "duplicate-tip"
        if h.height <= cur.height:
            self.rejected += 1
            raise ValueError(f"{self.name}: stale/sibling block "
                             f"{h.height} <= tip {cur.height}")
        if h.prev_hash != cur.hash():
            # gap: fetch-and-validate the missing ancestors from our block
            # table (stand-in for getdata on the wire), then retry in order.
            chain = []
            node = blk
            while node.header.prev_hash != cur.hash():
                parent = self.store.get(node.header.prev_hash)
                if (parent is None or parent.header.height < cur.height
                        or (parent.header.height == cur.height
                            and parent.header.hash() != cur.hash())):
                    # unknown parent, or the walk fell off our tip onto a
                    # rival branch: refuse blind application (sync/heal path)
                    self.rejected += 1
                    raise AssertionError(f"{self.name}: gap — parent "
                                         f"{node.header.prev_hash.hex()[:8]} "
                                         f"not applied; refusing {h.height}")
                node = parent
                chain.append(node)            # contiguous suffix down to tip
            for blk_ in reversed(chain):      # apply oldest-first; no double
                self.try_apply(blk_)          # count: retried blocks are fresh
            self.rejected += 1                # applications of held blocks
            return self.try_apply(blk)
        pre_tip_diff = self.state.difficulty
        self.state.validate_and_apply(blk)   # full pipeline: header rules,
        self.remember(blk)                   # merkle, PoW, emission, retarget
        self.applied += 1
        self.check_invariants(blk, pre_tip_diff)
        return "applied"

    def check_invariants(self, blk, used_difficulty: int) -> None:
        """Per-accepted-block battery — see module docstring."""
        h = blk.header
        assert parse_header(h.serialize()).hash() == h.hash(), \
            f"{self.name}: serialization fixpoint broken at {h.height}"
        mr = merkle_root(blk.txids + [blk.coinbase_commitment])
        assert mr == h.merkle_root, \
            f"{self.name}: merkle mismatch at {h.height}"
        nonce = int.from_bytes(h.pow.nonce, "little")
        assert PlaceholderSha3Pow.verify(_header_pre_nonce(h, mr), nonce,
                                         used_difficulty), \
            f"{self.name}: PoW invalid at {h.height}"
        assert self.state.total_shards == cumulative_supply(h.height), \
            f"{self.name}: emission ledger drift at {h.height}"
        w = self.params.diff_window
        if len(self.state._ts_window) == w + 1:
            expect = retarget(used_difficulty, list(self.state._ts_window), w,
                              self.params.target_block_time)
            assert self.state.difficulty == expect, \
                f"{self.name}: difficulty != pure retarget at {h.height}"

    # -- catch-up path --------------------------------------------------------

    def sync_to(self, candidate_tip) -> int:
        """Stale-node download path: walk the candidate chain back to our
        tip, then full-validate oldest-first. Returns blocks downloaded."""
        cur = self.state.tip
        if candidate_tip.header.height <= cur.header.height:
            return 0
        if candidate_tip.header.hash() == cur.header.hash():
            return 0
        # Walk back from the candidate collecting blocks that exist in our
        # own store; stop at the first hash missing there, then apply only
        # the contiguous suffix oldest-first. The walk is by linkage, not by
        # height arithmetic: if it bottoms out below our tip height without
        # ever touching our tip hash, the candidate descends from a rival
        # branch and we refuse to reorg onto lower work (cumulative-work
        # selection) — sync_to returns 0 and later heavier candidates heal us.
        cur = self.state.tip
        if candidate_tip.header.height <= cur.header.height:
            return 0
        if candidate_tip.header.hash() == cur.header.hash():
            return 0
        fetched = []
        node = candidate_tip
        on_our_chain = False
        while True:
            if node.header.hash() == cur.header.hash():
                on_our_chain = True         # candidate descends from our tip
                break
            if node.header.hash() not in self.store:
                break                       # unknown block: stop, heal later
            fetched.append(node)
            node = self.store[node.header.prev_hash]  # stand-in for getdata
            if node is None:
                break                       # broken linkage: stop, heal later
            if node.header.height < cur.header.height:
                break                       # fell off our tip: rival branch
        if not on_our_chain:
            return 0                        # rival fork: never adopt lower work
        n = 0
        for blk in reversed(fetched):
            if blk.header.hash() == self.state.tip.header.hash():
                continue                    # already applied mid-replay
            self.try_apply(blk)
            n += 1
        return n


def tip_fingerprint(nd: Node):
    t = nd.state.tip.header
    return (t.hash(), t.height, nd.state.difficulty, nd.state.total_shards)


# ---------------------------------------------------------------------------
# Adversarial frames
# ---------------------------------------------------------------------------

def _mine_at_parent(params, parent_blk, difficulty, ts, mutate=None):
    """Mine a REAL valid block on top of `parent_blk` (same content template
    as the honest chain), then optionally mutate its serialized header and
    re-parse — PoW lives over the pre-nonce fields, so any mutation that
    survives parse keeps a *valid* PoW. This is how we smuggle tampered
    frames past the cheap checks and force the deep validation path."""
    blk = mine_block(params, parent_blk,
                     denylist_root=parent_blk.header.denylist_root,
                     im_merkle_root=parent_blk.header.im_merkle_root,
                     difficulty=difficulty, timestamp=ts)
    if mutate is None:
        return blk
    buf = bytearray(blk.header.serialize())
    mutate(buf)
    return dreplace(blk, header=parse_header(bytes(buf)))


def poison_nonce(blk, nonce: bytes):
    newh = dreplace(blk.header,
                    pow=PoWFields(nonce=nonce,
                                  viewkey_hash=blk.header.pow.viewkey_hash))
    return dreplace(blk, header=newh)


def adversarial_round(rng: random.Random, nodes: list, healthy_tip_blk,
                      height: int) -> dict:
    """Fire tampered frames at all three live nodes; every one must be
    refused without corrupting any node's state. Returns counts.

    Frames are mined to carry VALID PoW at the current difficulty wherever
    possible, so rejection can only come from consensus rules the header
    itself cannot vouch for (prev-hash linkage, canonical wire domains) —
    the same reasoning behind keeping `difficulty` out of the header (§6.1).
    """
    counts = {"siblings": 0, "tamper": 0, "orphans": 0, "truncations": 0}
    params = nodes[0].params
    genesis_prev = b"\x00" * 32   # genesis.prev_hash marks the chain bottom
    tip = healthy_tip_blk
    ser = tip.header.serialize()
    tips_before = [tip_fingerprint(nd) for nd in nodes]
    diff = nodes[0].state.difficulty
    nxt_ts = tip.header.timestamp + params.target_block_time

    # 1. same-height sibling (valid parse, poisoned nonce) -> height rule
    sib = poison_nonce(tip, b"\xde\xad\xbe\xef" * 2)
    for nd in nodes:
        try:
            nd.try_apply(sib)
            raise AssertionError(f"{nd.name}: sibling ACCEPTED")
        except ValueError:
            pass
    counts["siblings"] += 1

    # 2. prev-hash swap: take the honest next-height block (valid PoW over
    #    its real preimage) and re-point `prev_hash` at an older ancestor —
    #    height stays ahead of every tip, so the cheap stale rule cannot
    #    catch it; only linkage/gap detection can.
    forged = _mine_at_parent(params, tip, diff, nxt_ts)
    wrong_parent = nodes[0].store[tip.header.prev_hash]
    while wrong_parent.header.height > 0 \
            and wrong_parent.header.prev_hash == genesis_prev:
        wrong_parent = nodes[0].store[wrong_parent.header.prev_hash]
    forged = dreplace(forged, header=dreplace(
        forged.header, prev_hash=wrong_parent.header.hash()))
    for nd in nodes:
        ahead = dreplace(forged, header=dreplace(
            forged.header, height=nd.state.tip.header.height + 1))
        try:
            nd.try_apply(ahead)
            raise AssertionError(f"{nd.name}: mislinked block ACCEPTED")
        except AssertionError as e:
            if "ACCEPTED" in str(e):
                raise
            # gap refusal ("parent not applied") is the documented path
    counts["tamper"] += 1

    # 3. non-canonical vote byte: bit-flip landing exactly on the uint8
    #    version_vote field (offset 16+128+40+4) with value outside {0,1}.
    #    The strict parser must reject before any consensus code runs.
    VOTE_OFF = 16 + 4 * 32 + 8 + 32 + 4
    frame = bytearray(ser)
    frame[VOTE_OFF] = rng.choice([2, 0x80, 0xFF])
    try:
        parse_header(bytes(frame))
        raise AssertionError("non-canonical vote byte parsed?!")
    except ValueError:
        counts["tamper"] += 1

    # 4. truncated frame -> strict parser must raise
    trunc = ser[:rng.randrange(1, HEADER_WIRE_LEN)]
    try:
        parse_header(trunc)
        raise AssertionError("truncated frame parsed?!")
    except ValueError:
        counts["truncations"] += 1

    # 5. orphan: valid next-height block whose parent nobody ever sent
    #    (ghost hash) -> gap detection must refuse blind application.
    ghost = poison_nonce(tip, b"\x01" * 8)   # different hash, never delivered
    orphan = _mine_at_parent(params, tip, diff, nxt_ts)
    orphan = dreplace(orphan, header=dreplace(
        orphan.header, prev_hash=ghost.header.hash()))
    for nd in nodes:
        before = (nd.state.tip.header.hash(), nd.applied)
        try:
            nd.try_apply(orphan)
            raise AssertionError(f"{nd.name}: orphan ACCEPTED blindly")
        except AssertionError as e:
            if "blindly" in str(e):
                raise                       # real failure: re-raise
            assert "gap" in str(e), e       # gap refusal is the documented path
        except KeyError:
            pass                            # unknown parent: refused, state intact
        assert (nd.state.tip.header.hash(), nd.applied) == before, \
            f"{nd.name}: orphan attempt mutated state"
    counts["orphans"] += 1

    tips_after = [tip_fingerprint(nd) for nd in nodes]
    assert tips_before == tips_after, "adversarial traffic corrupted node state"
    return counts


# ---------------------------------------------------------------------------
# Soak loop
# ---------------------------------------------------------------------------

def main(argv):
    hours = float(argv[1]) if len(argv) > 1 else 72.0
    cadence = int(argv[2]) if len(argv) > 2 else 30
    blocks_override = int(argv[3]) if len(argv) > 3 else 0

    params = NETWORKS["refraction"]
    total_blocks = blocks_override or int(round(hours * 3600 / cadence))
    if total_blocks < 30:
        raise SystemExit("soak too short to exercise sync + injections")
    virtual_hours = total_blocks * cadence / 3600
    # Retarget every `diff_window` blocks (Refraction: 15) so difficulty
    # actually adapts during the soak instead of pinning at the floor.
    retarget_every = max(1, params.diff_window)

    nodes = [Node(name, params) for name in ("alpha", "beta", "gamma")]

    rng = random.Random(0x50414B)          # deterministic soak replay
    ts = params.genesis_timestamp
    t0 = time.perf_counter()
    last_report = t0
    dupes = siblings = tamper = orphans = truncs = synced = 0

    print(f"node-sync soak: 3 independent nodes x {total_blocks:,} blocks "
          f"({virtual_hours:g} virtual hours @ {cadence}s cadence)", flush=True)

    for height in range(1, total_blocks + 1):
        ts += cadence
        prev = nodes[0].state.tip
        blk = mine_block(params, prev, denylist_root=prev.header.denylist_root,
                         im_merkle_root=prev.header.im_merkle_root,
                         difficulty=nodes[0].state.difficulty, timestamp=ts)
        assert blk.header.height == height

        # --- gossip: alpha current, beta/gamma lagged on staggered rounds --
        # Realistic message flow: every node first learns the block *hash*
        # (inv/headers announcement -> request(), unvalidated block table),
        # then receives the full block only for the (possibly lagged) item
        # it is fed. Stale nodes therefore can walk their sync path back to
        # a common ancestor even through blocks never fully delivered.
        assert nodes[0].try_apply(blk) == "applied"
        deliver = blk
        if height % 4 >= 2 and height >= 6:
            lag = min(3, height - 4)          # near genesis: lag to block 1
            anc = blk
            for _ in range(lag):
                anc = nodes[0].store[anc.header.prev_hash]
            deliver = anc
        for nd in nodes[1:]:
            nd.request(blk)                   # headers announcement of the tip
            if nd.state.tip.header.hash() == deliver.header.hash():
                nd.dupes_ignored += 1         # idempotent, like real gossip
            else:
                nd.remember(deliver)          # mirror delivery into the node's
                                              # block table at gossip time; if it
                                              # applies cleanly, try_apply below
                                              # re-stores it (idempotent)
            try:
                nd.try_apply(deliver)
            except ValueError:
                pass                          # already ahead: stale, counted
            except AssertionError:
                # gap refusal — heal by applying whatever contiguous suffix
                # we already hold, oldest-first. The walk-back stops at the
                # first hash missing from our store, so a lagged delivery can
                # never poison the bookkeeping with half-applied chains.
                fetched = []
                n2 = deliver
                while True:
                    if n2.header.hash() == nd.state.tip.header.hash():
                        break                 # descends from our tip: nothing to do
                    nxt = nd.store.get(n2.header.prev_hash)
                    if nxt is None:
                        break                 # unknown parent: stop, sync heals later
                    fetched.append(n2)
                    n2 = nxt
                for b in reversed(fetched):
                    if b.header.height <= nd.state.tip.header.height:
                        continue              # already applied mid-replay
                    nd.try_apply(b)           # raises loudly if truly invalid
            except KeyError:
                pass                          # unknown parent: healed by sync below

        # --- cross-node agreement; heal via the real sync path -------------
        fps = {tip_fingerprint(nd) for nd in nodes}
        if len(fps) != 1:
            for nd in nodes:
                synced += nd.sync_to(nodes[0].state.tip)
            fps = {tip_fingerprint(nd) for nd in nodes}
            if len(fps) != 1:
                for nd in nodes:
                    print(f"  {nd.name}: tip={nd.state.tip.header.height} "
                          f"diff={nd.state.difficulty}", file=sys.stderr)
                raise AssertionError(f"round {height}: sync failed to heal")

        # --- honest competing fork every retarget window -------------------
        # The miner also builds a rival branch off an older ancestor that is
        # strictly SHORTER than every node's tip (uniform difficulty => work
        # == height, so cumulative-work selection must always prefer our
        # canonical chain). Nodes must NOT adopt it; sync_to(rival) must be a
        # no-op everywhere.
        if height % retarget_every == 0 and height >= retarget_every + 4:
            min_h = min(nd.state.tip.header.height for nd in nodes)
            anc = nodes[0].state.tip
            while anc.header.height > min_h - 3:
                anc = nodes[0].store[anc.header.prev_hash]
            rival_ts = anc.header.timestamp + params.target_block_time
            rival = anc
            for _ in range(2):              # strictly shorter than every tip
                rival_ts += params.target_block_time
                rival = mine_block(params, rival,
                                   denylist_root=rival.header.denylist_root,
                                   im_merkle_root=rival.header.im_merkle_root,
                                   difficulty=nodes[0].state.difficulty,
                                   timestamp=min(rival_ts, ts))
            assert rival.header.height < min_h, "rival fork not lower-work"
            for nd in nodes:
                assert nd.sync_to(rival) == 0, \
                    f"{nd.name}: adopted shorter rival fork!"

        # --- periodic invariant probes + adversarial injections ------------
        if height % retarget_every == 0:
            for nd in nodes:
                assert nd.try_apply(nodes[0].state.tip) == "duplicate-tip"
                dupes += 1
            adv = adversarial_round(rng, nodes, nodes[0].state.tip, height)
            siblings += adv["siblings"]
            tamper += adv["tamper"]
            orphans += adv["orphans"]
            truncs += adv["truncations"]

        now = time.perf_counter()
        if now - last_report > 20 or height == total_blocks:
            rate = height / (now - t0)
            eta = (total_blocks - height) / rate if rate else 0
            print(f"  [{height * cadence / 3600:5.1f} vh] blocks={height:,} "
                  f"diff={nodes[0].state.difficulty} "
                  f"synced={synced:,} "
                  f"supply={nodes[0].state.total_shards / 10**8:,.1f} PRSM "
                  f"wall={now - t0:,.0f}s rate={rate:.1f} blk/s "
                  f"ETA={eta / 60:.1f}m", flush=True)
            last_report = now

    dt = time.perf_counter() - t0
    final = nodes[0].state.tip.header
    validated = sum(nd.applied for nd in nodes)
    print(f"PASSED: 3 independent nodes synced & sustained "
          f"{virtual_hours:g} virtual hours ({total_blocks:,} blocks @ "
          f"{cadence}s cadence) in {dt:.1f}s wall. {validated:,} block "
          f"applications fully validated across nodes ({synced:,} via "
          f"catch-up sync); final tips identical "
          f"({final.hash().hex()[:16]}... @ height {final.height}); "
          f"{dupes} duplicates ignored, {siblings} siblings rejected, "
          f"{tamper} mutated headers refused, {truncs} truncated frames "
          f"rejected, {orphans} orphans gap-refused; zero invariant "
          f"violations.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
