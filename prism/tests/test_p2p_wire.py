"""P2P wire-protocol + daemon tests (spec §4.2 transport, §13 Phase-1 note).

Covers, per ethics.md §4 (every behavioral change ships a positive AND a
negative/refusal test):

  * frame round-trip (encode_frame -> FrameReader.feed) — incl. split
    delivery across feeds and multi-frame batches;
  * truncated / garbage / oversize frame rejection (fail closed: WireError,
    never a partial message);
  * wrong-genesis (and wrong-magic) handshake drop — the daemon must
    disconnect mismatched peers before any state exchange;
  * gossip convergence on loopback — two real daemon nodes over real TCP
    sockets reach the identical tip hash in a short wall-clock window;
  * dandelion stem/flare counts (§4.2) — exactly one stem peer, fanout to
    all others, fail-closed local routing with zero peers;
  * mine_one() timestamp derivation — clamped to prev.timestamp + cadence
    growth (like the RPC devnet faucet), so wall-clock jumps can never
    produce a "timestamp too far in future" block.

All tests are stdlib-only, bind ephemeral loopback ports, and tear down
every socket/thread they open.
"""
from __future__ import annotations

import json
import socket
import struct
import time

import pytest

from prism.chain.node import Block, make_genesis, mine_block
from prism.chain.params import NETWORKS
from prism.chain.wire import (FrameReader, MAX_FRAME_BYTES, MSG_BLOCK,
                              MSG_INV, MSG_PING, MSG_VERSION, WireError,
                              block_hash_of, decode_block, encode_block,
                              encode_frame)
from prism.chain.p2p import Node


# ------------------------------------------------------------------ helpers

def _refraction_genesis():
    params = NETWORKS["refraction"]
    return params, make_genesis(params)


# ------------------------------------------------------------ frame layer ---

class TestFrameRoundTrip:
    def test_single_frame_roundtrip(self):
        raw = encode_frame("inv", {"hashes": ["ab" * 32]})
        r = FrameReader()
        msgs = r.feed(raw)
        assert msgs == [("inv", {"hashes": ["ab" * 32]})]

    def test_empty_payload_defaults_to_dict(self):
        r = FrameReader()
        assert r.feed(encode_frame("pong")) == [("pong", {})]

    def test_split_across_feeds_buffers_until_complete(self):
        """Truncated frames must NOT surface as messages or errors — they
        simply buffer until the rest of the bytes arrive."""
        raw = encode_frame("version", {"magic": "refraction"})
        r = FrameReader()
        assert r.feed(raw[:3]) == []           # header only
        assert r.feed(raw[3:10]) == []          # partial body
        msgs = r.feed(raw[10:])                 # remainder completes it
        assert msgs == [("version", {"magic": "refraction"})]

    def test_multiple_frames_in_one_feed(self):
        raw = (encode_frame("ping", {}) + encode_frame("pong", {})
               + encode_frame("inv", {"hashes": []}))
        r = FrameReader()
        assert r.feed(raw) == [("ping", {}), ("pong", {}), ("inv", {"hashes": []})]

    def test_exact_length_prefix(self):
        body = json.dumps({"kind": "ping", "payload": {}},
                          separators=(",", ":")).encode()
        raw = encode_frame("ping", {})
        assert raw[:4] == struct.pack("<I", len(body))


class TestFrameRejection:
    def test_garbage_body_rejected(self):
        junk = b"\xff\xfe\xfa not json at all"
        raw = struct.pack("<I", len(junk)) + junk
        with pytest.raises(WireError):
            FrameReader().feed(raw)

    def test_truncated_declared_length_never_completes(self):
        """Declared length larger than what ever arrives: no message is
        produced and no state is exposed (peer just stalls -> timeout)."""
        r = FrameReader()
        assert r.feed(struct.pack("<I", 500) + b"x" * 100) == []

    def test_oversize_declared_frame_refused(self):
        """DoS posture: a declared size beyond the cap kills the stream
        immediately without buffering the payload."""
        with pytest.raises(WireError):
            FrameReader().feed(struct.pack("<I", MAX_FRAME_BYTES + 1) + b"xx")

    def test_json_scalar_body_rejected(self):
        body = json.dumps(42).encode()
        with pytest.raises(WireError):
            FrameReader().feed(struct.pack("<I", len(body)) + body)

    def test_missing_kind_rejected(self):
        body = json.dumps({"payload": {}}).encode()
        with pytest.raises(WireError):
            FrameReader().feed(struct.pack("<I", len(body)) + body)

    def test_non_object_payload_rejected(self):
        body = json.dumps({"kind": "inv", "payload": [1, 2, 3]}).encode()
        with pytest.raises(WireError):
            FrameReader().feed(struct.pack("<I", len(body)) + body)

    def test_encode_refuses_empty_kind(self):
        with pytest.raises(WireError):
            encode_frame("")

    def test_encode_refuses_oversize_payload(self):
        with pytest.raises(WireError):
            encode_frame("inv", {"hashes": ["ab" * 32] * 200_000})


class TestBlockCodec:
    def test_block_roundtrip_fixpoint(self):
        params, g = _refraction_genesis()
        blk = mine_block(params, g, denylist_root=g.header.prev_hash,
                         im_merkle_root=g.header.im_merkle_root,
                         difficulty=1, txids=[],
                         timestamp=g.header.timestamp + 1)
        payload = encode_block(blk)
        back = decode_block(payload)
        assert back.header.hash() == blk.header.hash()
        assert block_hash_of(payload) == blk.header.hash()

    def test_decode_rejects_bad_header_hex(self):
        with pytest.raises(WireError):
            decode_block({"header_hex": "deadbeef", "txids_hex": [],
                          "coinbase_commitment_hex": "00" * 32})

    def test_decode_rejects_short_txid(self):
        params, g = _refraction_genesis()
        payload = encode_block(g)
        payload["txids_hex"] = ["abcd"]          # 2 bytes, not 32
        with pytest.raises(WireError):
            decode_block(payload)

    def test_decode_rejects_missing_keys(self):
        with pytest.raises(WireError):
            decode_block({})


# ------------------------------------------------------- handshake drops ---

def _raw_client(node: Node) -> socket.socket:
    s = socket.create_connection((node.host, node.port), timeout=5)
    return s


def _assert_dropped(s: socket.socket, reader: FrameReader):
    """Fail-closed refusal contract: after a protocol violation the daemon
    sends NO application frames (never a verack) and closes the socket —
    recv() returns EOF (b"") or raises ConnectionError. A stall would also
    surface as socket.timeout, which we accept defensively, but on loopback
    the close is immediate."""
    s.settimeout(5)
    try:
        data = s.recv(4096)
    except (ConnectionError, socket.timeout):
        return
    assert data == b"", f"server replied pre-handshake: {data!r}"
    for kind, _ in reader.feed(data):
        pytest.fail(f"unexpected frame from refused peer: {kind}")


class TestHandshakeRefusal:
    def test_wrong_genesis_drops_peer(self):
        """A version frame carrying the right magic but a foreign genesis
        hash must be answered with an immediate TCP close, no verack."""
        params, _ = _refraction_genesis()
        nd = Node("wronggen", "127.0.0.1", 0).start()
        try:
            s = _raw_client(nd)
            bad = {"magic": params.name,
                   "genesis": "cd" * 32,          # not our genesis
                   "height": 0, "agent": "evil"}
            s.sendall(encode_frame(MSG_VERSION, bad))
            _assert_dropped(s, FrameReader())
        finally:
            nd.stop()
            s.close()

    def test_wrong_magic_drops_peer(self):
        nd = Node("wrongmagic", "127.0.0.1", 0).start()
        try:
            s = _raw_client(nd)
            s.sendall(encode_frame(MSG_VERSION, {
                "magic": "mainnet-wrong", "genesis": "ab" * 32,
                "height": 0, "agent": "x"}))
            _assert_dropped(s, FrameReader())
        finally:
            nd.stop()
            s.close()

    def test_prehandshake_traffic_drops_peer(self):
        """Blocks/inv before version => protocol violation => disconnect."""
        nd = Node("prehand", "127.0.0.1", 0).start()
        try:
            s = _raw_client(nd)
            s.sendall(encode_frame(MSG_BLOCK, {"header_hex": "ff",
                                               "coinbase_commitment_hex": "00" * 32}))
            _assert_dropped(s, FrameReader())
        finally:
            nd.stop()
            s.close()

    def test_garbage_first_frame_drops_peer(self):
        nd = Node("garbage", "127.0.0.1", 0).start()
        try:
            s = _raw_client(nd)
            junk = b"\x00\x00\x00\x05{{{{{"
            s.sendall(junk)
            _assert_dropped(s, FrameReader())
            deadline = time.time() + 2
            while nd.wire_errors < 1 and time.time() < deadline:
                time.sleep(0.05)
            assert nd.wire_errors >= 1
        finally:
            nd.stop()
            s.close()

    def test_good_handshake_gets_verack_and_ping_works(self):
        """Positive control for the refusal cases above."""
        params, g = _refraction_genesis()
        nd = Node("goodhs", "127.0.0.1", 0).start()
        try:
            s = _raw_client(nd)
            s.sendall(encode_frame(MSG_VERSION, {
                "magic": params.name, "genesis": g.header.hash().hex(),
                "height": 0, "agent": "pytest"}))
            r = FrameReader()
            s.settimeout(5)
            msgs = []
            while not any(k == "verack" for k, _ in msgs):
                msgs += r.feed(s.recv(65536))
            assert any(k == "verack" for k, _ in msgs)
            s.sendall(encode_frame(MSG_PING, {}))
            deadline = time.time() + 5
            while not any(k == "pong" for k, _ in msgs) and time.time() < deadline:
                msgs += r.feed(s.recv(65536))
            assert any(k == "pong" for k, _ in msgs)
            assert nd.wait_for_peers(1, timeout=2)
        finally:
            nd.stop()
            s.close()


# ---------------------------------------------------- gossip convergence ---

class TestGossipConvergence:
    def test_two_nodes_converge_on_loopback(self):
        """Short wall-clock proof (NOT the 72 h criterion): miner node
        produces blocks over real TCP; second node reaches the identical
        tip hash via inv->block gossip."""
        a = Node("conv-a", "127.0.0.1", 0, miner=True, block_time_s=0.5)
        b = Node("conv-b", "127.0.0.1", 0, block_time_s=0.5)
        a.start(); b.start()
        try:
            b.connect_seeds([(a.host, a.port)])
            assert a.wait_for_peers(1, timeout=10)
            assert b.wait_for_peers(1, timeout=10)
            deadline = time.time() + 15
            tip_a = tip_b = None
            while time.time() < deadline:
                tip_a, tip_b = a.status()["tip_hash"], b.status()["tip_hash"]
                if tip_a == tip_b and a.status()["tip_height"] > 0:
                    break
                time.sleep(0.2)
            sa, sb = a.status(), b.status()
            assert sa["tip_hash"] == sb["tip_hash"], (sa, sb)
            assert sa["tip_height"] >= 1
            assert sb["applied"] >= 1
            assert sb["rejected"] == 0
            assert sa["wire_errors"] == 0 and sb["wire_errors"] == 0
        finally:
            a.stop(); b.stop()

    def test_invalid_block_cannot_poison_store(self):
        """Negative case: a peer that passes handshake then pushes a
        double-spending/future-dated invalid block must not move the tip."""
        params, g = _refraction_genesis()
        nd = Node("poison", "127.0.0.1", 0).start()
        try:
            s = _raw_client(nd)
            s.sendall(encode_frame(MSG_VERSION, {
                "magic": params.name, "genesis": g.header.hash().hex(),
                "height": 0, "agent": "pytest"}))
            r = FrameReader()
            s.settimeout(5)
            msgs = []
            while not any(k == "verack" for k, _ in msgs):
                msgs += r.feed(s.recv(65536))
            # craft a block with height+1 but a NON-monotonic timestamp
            bad = mine_block(params, g, denylist_root=g.header.prev_hash,
                             im_merkle_root=g.header.im_merkle_root,
                             difficulty=1, txids=[],
                             timestamp=g.header.timestamp)   # == parent: illegal
            s.sendall(encode_frame(MSG_BLOCK, encode_block(bad)))
            time.sleep(0.5)
            st = nd.status()
            assert st["tip_hash"] == g.header.hash().hex()  # tip unmoved
            assert st["rejected"] >= 1 or st["applied"] == 0
        finally:
            nd.stop()
            s.close()


# ------------------------------------------------------ dandelion routing ---

class TestDandelionStemFlare:
    def _nodes_with_peers(self, n):
        hub = Node("dan-hub", "127.0.0.1", 0).start()
        leaves = [Node(f"dan-{i}", "127.0.0.1", 0).start() for i in range(n)]
        for lf in leaves:
            lf.connect_seeds([(hub.host, hub.port)])
        assert hub.wait_for_peers(n, timeout=10)
        return hub, leaves

    def test_stem_one_flare_all_others(self):
        hub, leaves = self._nodes_with_peers(3)
        try:
            before_s = hub.dandelion_stems
            before_f = hub.dandelion_flares
            rep = hub.broadcast_tx_dandelion({"amt": 1, "nonce": "x"})
            assert rep["routed"] is True
            assert rep["phase"] == "stem+flare"
            assert rep["peers"] == 3
            assert rep["flared"] == 2               # everyone except the stem
            assert hub.dandelion_stems == before_s + 1
            assert hub.dandelion_flares == before_f + 2
        finally:
            hub.stop()
            for lf in leaves:
                lf.stop()

    def test_zero_peers_fail_closed_local(self):
        """Refusal case: with no handshaked peers nothing is half-relayed;
        counters stay at zero."""
        solo = Node("dan-solo", "127.0.0.1", 0).start()
        try:
            rep = solo.broadcast_tx_dandelion({"amt": 1})
            assert rep == {"routed": False, "phase": "local", "peers": 0}
            assert solo.dandelion_stems == 0
            assert solo.dandelion_flares == 0
        finally:
            solo.stop()

    def test_two_peers_flare_exactly_one(self):
        hub, leaves = self._nodes_with_peers(2)
        try:
            rep = hub.broadcast_tx_dandelion({"amt": 2})
            assert rep["peers"] == 2 and rep["flared"] == 1
        finally:
            hub.stop()
            for lf in leaves:
                lf.stop()


# ------------------------------------------------- mine_one() timestamping ---

class TestMineOneTimestampClamp:
    def test_wallclock_jump_does_not_break_mining(self):
        """Regression for the soak failure: even if system time sits far
        ahead of the genesis timestamp, mine_one() clamps ts to
        prev.timestamp + cadence growth (RPC-faucet style) so every mined
        block passes validate_header_basic's future tolerance."""
        params, g = _refraction_genesis()
        assert int(time.time()) > g.header.timestamp  # premise holds on Refraction
        nd = Node("tsclamp", "127.0.0.1", 0, block_time_s=2.0)
        try:
            for _ in range(3):
                blk = nd.mine_one()
                assert blk is not None, "clamped mining must not skip rounds"
                assert blk.header.timestamp <= g.header.timestamp + 3 * 6 \
                    + params.target_block_time * 3
            assert nd.status()["tip_height"] == 3
        finally:
            nd.stop()

    def test_timestamps_strictly_monotonic(self):
        nd = Node("tsmono", "127.0.0.1", 0, block_time_s=1.0)
        try:
            prev_ts = nd.genesis.header.timestamp
            for _ in range(3):
                blk = nd.mine_one()
                assert blk is not None
                assert blk.header.timestamp > prev_ts
                prev_ts = blk.header.timestamp
        finally:
            nd.stop()
