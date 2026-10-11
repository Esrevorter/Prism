# Refraction — public testnet runbook (Phase 1 exit criteria)

Genesis: `python3 -m chain.cli genesis --network refraction`
PoW: placeholder sha3-diff until RandomX FFI ships (difficulty 1 faucet).
Denylist root: embed GENESIS_ACC until Council v0 publishes weekly roots.

## Networking daemon (real sockets)

Start a node (listens on loopback by default):

    python -m prism.chain.p2p --port 9876 --mine --block-time 30

Join existing peers (repeatable `--seed`):

    python -m prism.chain.p2p --port 9877 --seed 127.0.0.1:9876

Protocol: strict length-prefixed JSON frames (`prism/chain/wire.py`);
version/verack handshake pins network magic + genesis hash — mismatched
peers are dropped before any state exchange; blocks gossip inv→block with
getblocks sync-heal; tx routing uses the Dandelion++ stem/flare skeleton
(spec §4.2). Every wire violation fails closed: connection dropped, zero
chain-state mutation.

### Short wall-clock soak (run in-process)

    python -c "from prism.chain.p2p import soak; \
               import json; r = soak(nodes_n=3, seconds=8); \
               print(json.dumps({k: r[k] for k in ('passed','identical_tips','tip','wall_seconds')}, indent=2))"

Result (2026-10-11, this sandbox): **SHORT WALL-CLOCK PASS** — passed: True,
identical tips across all 3 nodes at height 5
(`b8c1a4a756d734524d6173d6ced1d35bb9e6eff7826f9197b21ebdadbcfd0eca`),
8.0 s of real wall time over real TCP sockets/threads, 0 rejected blocks,
0 wire errors. Mining cadence is clamped to prev.timestamp + cadence growth
(like the RPC devnet faucet) so wall-clock drift can never produce
future-tolerance violations.

Honest scope: this 8 s pass exercises the real transport under wall clock;
it is **not** the spec §13 "sustain 72 h" criterion. The 72 h figure remains
virtual-cadence evidence (`prism/property/node_sync_soak.py`) until a ≥72 h
wall-clock run of this daemon executes. Do not conflate the two in reports.

Regression coverage: `prism/tests/test_p2p_wire.py` (29 tests: frame
round-trip, truncated/garbage-frame rejection, wrong-genesis handshake drop,
loopback gossip convergence, dandelion stem/flare counts, timestamp clamp).

Exit criteria (spec §13 Phase 1):
- [x] block header w/ denylist_root field
- [x] emission + tail schedule exact-integer
- [x] adaptive difficulty retarget
- [ ] RingCT tx format + validation   <- next
- [ ] MPC keygen ceremony prototype
- [ ] NL→Intent compiler (dry-run only)
