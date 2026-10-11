# l2/ — micropayment channels & fee policy (Phase 3 · BETA)

LN-style commitment-channel design adapted to PRSM base fees (spec §10).

## Status: beta (implemented & tested, not yet wired to live net)

| Module | What it does | Evidence |
|---|---|---|
| `fees.py` | Fee-predictability hardening (§10.1): fixed 10,000-shard base fee, priority-fee lattice enforcement (whole multiples N·BASE, 1 ≤ N ≤ 10), deterministic pre-sign quotes for the three UI tiers, and greedy fee-density block packing with deterministic tie-breaks. Non-standard fees are rejected at pack time so a hostile miner can't smuggle them. | `tests/test_l2_fees_channels.py` |
| `channels.py` | Bidirectional payment channels + watchtower (§10.2, E2/E3): Schnorr-signed monotonic state versions, instant zero-fee in-channel payments, cooperative close (lifetime chain cost = exactly 2 base fees, asserted against `chain.params.BASE_FEE_SHARDS`), stale-close penalty sweep to the innocent party, and watchtower **encrypted penalty blobs** (keccak-CTR sealed + MAC'd; tower sees only a truncated tag + version). | same test file + `property/phase3_smoke.py` |

Full-suite evidence: 464 passed / 7 skipped (`pytest -q`, see spec.md §13 notes).

## Beta caveats (honest posture)

- Channel logic is a protocol-level model: real CLTV/adapter transactions land with the
  node mempool work (Phase 3→4 boundary). Watchtower is a beta service contract, not
  yet a deployed relay.
- GUI "in-app channels" surface ships after Refraction testnet wiring.
