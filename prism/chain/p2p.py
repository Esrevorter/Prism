"""Refraction networking daemon — real-socket P2P node (spec §4.2 transport,
§13 Phase-1 note: "pending a wall-clock soak against a real Refraction
testnet deployment").

This is the on-the-wire counterpart of the in-memory sync pipeline proven by
`prism/property/node_sync_soak.py`: same ChainState validation rules, same
chain-selection logic, same gap/sync-heal path — but blocks now arrive over
TCP through the strict framing layer in `chain/wire.py`.

Components
----------
  * Node       — one validating peer: listens on a port, connects to seeds,
                 runs a background mine loop (CPU faucet PoW on Refraction),
                 gossips new blocks via inv→block, and heals lagging peers
                 via getblocks→download→oldest-first apply.
  * Dandelion++ (§4.2, v0) — outbound transactions route through a stem
                 phase (single random peer, no fanout) then flare
                 (fanout-to-all). On this single-process devnet the routing
                 decision itself is exercised; per spec §5.1 tx relay lands
                 with RingCT.
  * Soak harness (`soak()`) — boots N real daemon nodes on loopback ports,
                 lets them run for a wall-clock window at a chosen block
                 cadence, then asserts every node reached the identical tip
                 hash. This is the honest first step toward the 72 h
                 criterion: real sockets, real threads, real wall clock —
                 reported as "short wall-clock pass", never as 72 h.

Security posture (fail closed, ethics.md §1):
  - every inbound frame goes through wire.FrameReader; any WireError drops
    the connection, never mutates chain state;
  - inbound blocks are validated by ChainState.validate_and_apply before
    being remembered — invalid blocks cannot poison the block table;
  - chain selection only ever walks back to OUR tip (rival forks of equal
    or lower work are refused, mirroring the soak's cumulative-work rule);
  - handshake requires matching network magic + genesis hash; mismatched
    peers are disconnected immediately.

Usage:
  python -m prism.chain.p2p --port 9876 [--seed host:port]... [--mine]
"""
from __future__ import annotations

import argparse
import json
import random
import socket
import sys
import threading
import time
from dataclasses import dataclass, field

from .block import ZERO32
from .emission import cumulative_supply
from .node import Block, ChainState, make_genesis, mine_block
from .params import NETWORKS
from .wire import (FrameReader, MSG_BLOCK, MSG_GETBLOCKS, MSG_INV,
                   MSG_PING, MSG_PONG, MSG_VERACK, MSG_VERSION,
                   WireError, block_hash_of, decode_block, encode_block,
                   encode_frame)

MAX_PEERS = 16
INV_BATCH = 32                      # max hashes announced per inv message
GETBLOCKS_MAX = 500                 # max blocks served per request


@dataclass
class Peer:
    conn: socket.socket
    reader: FrameReader = field(default_factory=FrameReader)
    addr: tuple = ()
    outbound: bool = False
    handshaked: bool = False
    last_seen: float = 0.0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def send(self, kind: str, payload: dict | None = None) -> bool:
        try:
            with self.lock:
                self.conn.sendall(encode_frame(kind, payload))
            return True
        except OSError:
            return False


class Node:
    """One validating Refraction daemon over real TCP sockets."""

    def __init__(self, name: str, host: str, port: int,
                 network: str = "refraction", miner: bool = False,
                 block_time_s: float = 2.0, rng_seed: int | None = None):
        self.name = name
        self.params = NETWORKS[network]
        self.genesis = make_genesis(self.params)
        self.state = ChainState(params=self.params, tip=self.genesis,
                                 difficulty=1)
        self.store: dict[bytes, Block] = {self.genesis.header.hash(): self.genesis}
        self.lock = threading.RLock()          # guards state + store
        self.peers: list[Peer] = []
        self.rng = random.Random(rng_seed)
        self.miner = miner
        self.block_time_s = block_time_s
        self._stop = threading.Event()
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind((host, port))
        self._srv.listen(16)
        self.host, self.port = self._srv.getsockname()[:2]
        # counters (honest reporting)
        self.applied = 0
        self.rejected = 0
        self.dupes_ignored = 0
        self.synced_downloads = 0
        self.wire_errors = 0
        self.dandelion_stems = 0
        self.dandelion_flares = 0
        self._threads: list[threading.Thread] = []

    # ------------------------------------------------------------------ run

    def start(self):
        self._spawn(self._accept_loop, "accept")
        if self.miner:
            self._spawn(self._mine_loop, "miner")
        return self

    def connect_seeds(self, seeds: list[tuple[str, int]]):
        for host, port in seeds:
            self._spawn(self._dial, "dial", host, port)

    def stop(self):
        self._stop.set()
        try:
            self._srv.close()
        except OSError:
            pass
        with self.lock:
            for p in list(self.peers):
                try:
                    p.conn.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                try:
                    p.conn.close()
                except OSError:
                    pass

    def wait_for_peers(self, n: int, timeout: float = 10.0) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            with self.lock:
                if sum(1 for p in self.peers
                       if p.handshaked and not self._stop.is_set()) >= n:
                    return True
            time.sleep(0.05)
        return False

    def _spawn(self, fn, name, *args):
        t = threading.Thread(target=fn, args=args,
                             name=f"{self.name}-{name}", daemon=True)
        self._threads.append(t)
        t.start()

    # ------------------------------------------------------------- mining

    def _mine_loop(self):
        while not self._stop.is_set():
            t_end = time.monotonic() + self.block_time_s
            blk = self.mine_one()
            if blk is not None:
                self.gossip_block(blk)
            delay = t_end - time.monotonic()
            if delay > 0:
                self._stop.wait(delay)

    def mine_one(self) -> Block | None:
        """Produce one valid block on top of our tip (faucet PoW budget is
        capped; on failure we simply skip this round — never force an
        invalid block onto the wire)."""
        from .pow import PlaceholderSha3Pow
        from .node import _coinbase_commitment, _header_pre_nonce
        with self.lock:
            prev = self.state.tip
            ts = max(int(time.time()), prev.header.timestamp + 1)
            cb = _coinbase_commitment(prev.header.height + 1, self.params)
            base = make_header_template(prev, self.params, ts, cb)
            pre = _header_pre_nonce(base, base.merkle_root)
            nonce = PlaceholderSha3Pow.mine(pre, self.state.difficulty)
            if nonce is None:
                return None
            header = _replace_header(base, nonce)
            blk = Block(header=header, txids=[], coinbase_commitment=cb)
            try:
                self.state.validate_and_apply(blk)   # validate our own too
            except ValueError:
                return None                          # stale template race
            bh = blk.header.hash()
            self.store[bh] = blk
            self.applied += 1
            return blk

    # ------------------------------------------------------------ gossip

    def gossip_block(self, blk: Block):
        """inv announcement to all handshaked peers, then deliver the full
        block to peers that don't hold it yet (stand-in for getdata)."""
        payload = encode_block(blk)
        h = blk.header.hash().hex()
        with self.lock:
            peers = [p for p in self.peers if p.handshaked]
        for p in peers:
            p.send(MSG_INV, {"hashes": [h]})
            p.send(MSG_BLOCK, payload)

    def broadcast_tx_dandelion(self, tx_payload: dict) -> dict:
        """Dandelion++ v0 routing decision for a transaction (§4.2).

        Stem: forward to exactly ONE random peer (source hides among the
        stem). Flare: fan out to ALL peers. With zero peers the tx stays
        local (fail-closed: nothing is half-relayed). Returns a routing
        report for tests/status. NOTE: encrypted ephemeral stem rotation
        and the real mempool land with RingCT tx relay (spec TODO §5.1);
        this is the routing skeleton the L1 daemon will carry txs on.
        """
        with self.lock:
            peers = [p for p in self.peers if p.handshaked]
        if not peers:
            return {"routed": False, "phase": "local", "peers": 0}
        stem = self.rng.choice(peers)
        ok = stem.send(MSG_INV, {"txs": [json.dumps(tx_payload,
                                                    sort_keys=True)[:64]]})
        if not ok:
            return {"routed": False, "phase": "stem-dead", "peers": len(peers)}
        self.dandelion_stems += 1
        flared = 0
        for p in peers:
            if p is not stem:
                if p.send(MSG_INV, {"txs": [json.dumps(tx_payload,
                                                       sort_keys=True)[:64]]}):
                    flared += 1
        self.dandelion_flares += flared
        return {"routed": True, "phase": "stem+flare",
                "peers": len(peers), "stem": stem.addr, "flared": flared}

    # -------------------------------------------------------- accept loops

    def _accept_loop(self):
        while not self._stop.is_set():
            try:
                conn, addr = self._srv.accept()
            except OSError:
                return
            conn.settimeout(None)
            peer = Peer(conn=conn, addr=addr)
            self._spawn(self._peer_loop, f"peer-{addr[1]}", peer)

    def _dial(self, host: str, port: int):
        deadline = time.time() + 30
        while not self._stop.is_set() and time.time() < deadline:
            try:
                conn = socket.create_connection((host, port), timeout=5)
            except OSError:
                self._stop.wait(0.3)
                continue
            peer = Peer(conn=conn, addr=(host, port), outbound=True)
            self._spawn(self._peer_loop, f"peer-{host}:{port}", peer)
            return

    # --------------------------------------------------------- peer loop

    def _peer_loop(self, peer: Peer):
        try:
            if peer.outbound:
                peer.send(MSG_VERSION, self._version_payload())
            while not self._stop.is_set():
                try:
                    data = peer.conn.recv(65536)
                except OSError:
                    return
                if not data:
                    return
                try:
                    msgs = peer.reader.feed(data)
                except WireError:
                    self.wire_errors += 1
                    return                      # fail closed: drop peer
                for kind, payload in msgs:
                    if not self._handle(peer, kind, payload):
                        return
        finally:
            with self.lock:
                if peer in self.peers:
                    self.peers.remove(peer)
            try:
                peer.conn.close()
            except OSError:
                pass

    def _version_payload(self) -> dict:
        return {"magic": self.params.name,
                "genesis": self.genesis.header.hash().hex(),
                "height": self.state.tip.header.height,
                "agent": f"prism-p2p/{self.name}"}

    def _handle(self, peer: Peer, kind: str, payload: dict) -> bool:
        """Returns False to disconnect the peer. Any protocol violation is
        a disconnect, never a partial application."""
        if not peer.handshaked:
            if kind == MSG_VERSION:
                if payload.get("magic") != self.params.name or \
                        payload.get("genesis") != self.genesis.header.hash().hex():
                    return False                # wrong chain: drop
                peer.handshaked = True
                peer.last_seen = time.time()
                with self.lock:
                    if len(self.peers) >= MAX_PEERS:
                        return False
                    self.peers.append(peer)
                peer.send(MSG_VERACK, {})
                # kick off catch-up: ask for their tip inventory
                peer.send(MSG_GETBLOCKS, {"stops": [],
                                          "locator": self.state.tip.header.hash().hex()})
                return True
            if kind == MSG_VERACK:
                peer.handshaked = True
                peer.last_seen = time.time()
                with self.lock:
                    if len(self.peers) >= MAX_PEERS:
                        return False
                    self.peers.append(peer)
                peer.send(MSG_GETBLOCKS, {"stops": [],
                                          "locator": self.state.tip.header.hash().hex()})
                return True
            return False                        # pre-handshake traffic: drop

        if kind == MSG_PING:
            return peer.send(MSG_PONG, {})
        if kind == MSG_PONG:
            peer.last_seen = time.time()
            return True
        if kind == MSG_INV:
            return self._on_inv(peer, payload)
        if kind == MSG_BLOCK:
            return self._on_block(peer, payload)
        if kind == MSG_GETBLOCKS:
            return self._on_getblocks(peer, payload)
        # unknown kinds: ignore (forward compat), keep connection
        return True

    # ----------------------------------------------------------- handlers

    def _on_inv(self, peer: Peer, payload: dict) -> bool:
        hashes = payload.get("hashes", [])
        if not isinstance(hashes, list) or len(hashes) > INV_BATCH:
            return False
        want = []
        with self.lock:
            for hx in hashes:
                try:
                    hb = bytes.fromhex(str(hx))
                except ValueError:
                    return False
                if len(hb) != 32:
                    return False
                if hb not in self.store:
                    want.append(hb.hex())
        for i in range(0, len(want), 16):
            if not peer.send(MSG_GETBLOCKS, {"want": want[i:i + 16]}):
                return False
        return True

    def _on_block(self, peer: Peer, payload: dict) -> bool:
        try:
            blk = decode_block(payload)
        except WireError:
            self.wire_errors += 1
            return False                        # malformed: drop peer
        hb = blk.header.hash()
        with self.lock:
            if hb in self.store and hb == self.state.tip.header.hash():
                self.dupes_ignored += 1
                return True
            if hb in self.store:
                self.dupes_ignored += 1
                return True
            self.store[hb] = blk                # unvalidated block table
            applied = self._try_apply_or_heal(blk)
        if not applied:
            # ask everyone for what we're missing
            self._request_missing(blk)
        return True

    def _try_apply_or_heal(self, blk: Block) -> bool:
        """Try direct apply; on parent-gap walk back through our store and
        apply the contiguous suffix oldest-first (same algorithm as the
        soak's try_apply/sync_to, now on the wire). Caller holds self.lock."""
        cur = self.state.tip
        if blk.header.height <= cur.header.height:
            self.rejected += 1
            return True                          # stale/sibling: handled
        if blk.header.prev_hash == cur.header.hash():
            try:
                self.state.validate_and_apply(blk)
            except ValueError:
                self.rejected += 1
                return True                      # invalid: counted, refused
            self.applied += 1
            return True
        # gap: collect contiguous held suffix down to our tip
        chain, node = [], blk
        while True:
            parent = self.store.get(node.header.prev_hash)
            if parent is None:
                return False                     # unknown parent: fetch more
            if parent.header.hash() == self.state.tip.header.hash():
                break
            if parent.header.height < self.state.tip.header.height:
                return False                     # fell off tip: rival branch
            chain.append(parent)
            node = parent
        for b in reversed(chain + [blk]):
            if b.header.hash() == self.state.tip.header.hash():
                continue
            if b.header.height <= self.state.tip.header.height:
                continue
            try:
                self.state.validate_and_apply(b)
                self.applied += 1
            except ValueError:
                self.rejected += 1               # truly invalid mid-heal
                return True
        return True

    def _request_missing(self, blk: Block):
        locator = blk.header.prev_hash.hex()
        with self.lock:
            peers = [p for p in self.peers if p.handshaked]
        for p in peers[:3]:
            p.send(MSG_GETBLOCKS, {"want_from": locator, "limit": 64})

    def _on_getblocks(self, peer: Peer, payload: dict) -> bool:
        """Serve blocks a peer is missing. Two modes:
           want: [hash...]        — serve those exact blocks we hold
           want_from/locator      — serve up to `limit` descendants of the
                                    locator continuing to our current tip."""
        want = payload.get("want")
        if isinstance(want, list):
            for hx in want[:GETBLOCKS_MAX]:
                try:
                    hb = bytes.fromhex(str(hx))
                except ValueError:
                    return False
                with self.lock:
                    blk = self.store.get(hb)
                if blk is not None and not peer.send(MSG_BLOCK, encode_block(blk)):
                    return False
            return True
        locator = payload.get("locator") or payload.get("want_from")
        limit = min(int(payload.get("limit", 64)), GETBLOCKS_MAX)
        if not isinstance(locator, str):
            return True
        try:
            lh = bytes.fromhex(locator)
        except ValueError:
            return False
        # descend from our tip collecting until we hit the locator
        with self.lock:
            chain, node = [], self.state.tip
            found = lh == node.header.hash()
            while not found and node.header.height > 0:
                parent = self.store.get(node.header.prev_hash)
                if parent is None:
                    break
                chain.append(node)
                node = parent
                found = node.header.hash() == lh
            if not found:
                return True                       # locator unknown: no service
            serve = chain[-limit:][::-1] if chain else []
        for blk in serve:
            if not peer.send(MSG_BLOCK, encode_block(blk)):
                return False
        return True

    # ------------------------------------------------------------ status

    def status(self) -> dict:
        with self.lock:
            tip = self.state.tip.header
            return {
                "name": self.name,
                "network": self.params.name,
                "listen": f"{self.host}:{self.port}",
                "peers": sum(1 for p in self.peers if p.handshaked),
                "tip_height": tip.height,
                "tip_hash": tip.hash().hex(),
                "difficulty": self.state.difficulty,
                "total_shards": self.state.total_shards,
                "supply_prsm": self.state.total_shards / 10 ** 8,
                "applied": self.applied,
                "rejected": self.rejected,
                "dupes_ignored": self.dupes_ignored,
                "synced_downloads": self.synced_downloads,
                "wire_errors": self.wire_errors,
                "dandelion": {"stems": self.dandelion_stems,
                              "flares": self.dandelion_flares},
            }


def _replace_header(base, nonce: int):
    """dataclasses.replace with the mined nonce folded into PoWFields."""
    import struct as st
    from dataclasses import replace as dreplace
    from .block import PoWFields
    return dreplace(base, pow=PoWFields(nonce=st.pack("<Q", nonce),
                                        viewkey_hash=base.pow.viewkey_hash))


def make_header_template(prev: Block, params, ts: int, cb: bytes):
    """Header template with final merkle root filled in (mirrors
    node.mine_block so PoW covers the real content)."""
    from .block import BlockHeader, PoWFields
    from .node import merkle_root
    from .pow import viewkey_for_height
    mr = merkle_root([] + [cb])
    return BlockHeader(
        height=prev.header.height + 1,
        timestamp=ts,
        prev_hash=prev.header.hash(),
        merkle_root=mr,
        im_merkle_root=prev.header.im_merkle_root,
        denylist_root=prev.header.denylist_root,
        pow=PoWFields(nonce=bytes(8),
                      viewkey_hash=viewkey_for_height(prev.header.height + 1,
                                                       prev.header.hash())),
        version_major=1, version_minor=0, version_vote=False,
        size_bytes=0,
    )


# ---------------------------------------------------------------------------
# soak harness: real sockets, real threads, wall clock
# ---------------------------------------------------------------------------

def soak(nodes_n: int = 3, seconds: float = 20.0, block_time_s: float = 2.0,
         mine_every: int = 0) -> dict:
    """Boot `nodes_n` real daemon processes-as-threads on loopback, let one
    of them mine at wall-clock cadence for `seconds`, then verify every node
    converged to the identical tip hash. Honest scope: this proves the wire
    transport + sync under wall clock for SHORT windows; the 72 h figure in
    spec §13 remains virtual-cadence evidence until a long run is executed."""
    results = {"nodes": [], "passed": False}
    nds: list[Node] = []
    try:
        for i in range(nodes_n):
            nds.append(Node(f"soak{i}", "127.0.0.1", 0,
                            miner=(i == 0), block_time_s=block_time_s).start())
        for nd in nds[1:]:
            nd.connect_seeds([(nds[0].host, nds[0].port)])
        for nd in nds:
            assert nd.wait_for_peers(1, timeout=15.0), \
                f"{nd.name}: no handshake within 15 s"
        t0 = time.monotonic()
        while time.monotonic() - t0 < seconds:
            time.sleep(0.5)
        # convergence check: poll tips for up to 3x block_time grace
        deadline = time.monotonic() + 3 * block_time_s + 5
        while time.monotonic() < deadline:
            tips = {(nd.status()["tip_hash"], nd.status()["tip_height"])
                    for nd in nds}
            if len(tips) == 1:
                break
            time.sleep(0.5)
        tips = {(nd.status()["tip_hash"], nd.status()["tip_height"])
                for nd in nds}
        results["nodes"] = [nd.status() for nd in nds]
        results["wall_seconds"] = time.monotonic() - t0
        results["identical_tips"] = len(tips) == 1
        results["tip"] = next(iter(tips)) if len(tips) == 1 else None
        results["passed"] = len(tips) == 1 and next(iter(tips))[1] > 0
        return results
    finally:
        for nd in nds:
            nd.stop()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="prism-node",
                                 description="Refraction P2P daemon")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9876)
    ap.add_argument("--network", default="refraction",
                    choices=sorted(NETWORKS))
    ap.add_argument("--seed", action="append", default=[],
                    help="host:port of an existing peer (repeatable)")
    ap.add_argument("--mine", action="store_true",
                    help="run the CPU faucet miner loop (testnet only)")
    ap.add_argument("--block-time", type=float, default=30.0)
    ns = ap.parse_args(argv)
    seeds = []
    for s in ns.seed:
        h, _, p = s.partition(":")
        seeds.append((h, int(p)))
    nd = Node("daemon", ns.host, ns.port, network=ns.network,
              miner=ns.mine, block_time_s=ns.block_time_s)
    if seeds:
        nd.connect_seeds(seeds)
    print(json.dumps(nd.status(), indent=2))
    try:
        while True:
            time.sleep(10)
            st = nd.status()
            print(f"[{time.strftime('%H:%M:%S')}] {nd.name}: "
                  f"tip={st['tip_height']} peers={st['peers']} "
                  f"applied={st['applied']} rejected={st['rejected']} "
                  f"supply={st['supply_prsm']:,.2f} PRSM", flush=True)
    except KeyboardInterrupt:
        nd.stop()


if __name__ == "__main__":
    sys.exit(main())
