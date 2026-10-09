# Chain Rules & Monetary Policy (Technical)

*The L1 rulebook as implemented: block structure, PoW interface, difficulty, emission code paths, and validation. Binding spec: [`spec.md` §4](../spec.md); code: `prism/chain/`.*

---

## Consensus parameters (`prism/chain/params.py`)

| Constant | Value | Notes |
|---|---|---|
| `TARGET_BLOCK_TIME` | 120 s | retargeted adaptively |
| `DIFFICULTY_WINDOW` | 60 blocks | DCR-style averaging |
| `SOFT_TARGET_SIZE` / `MAX_SIZE` | 2 MB / 4 MB | median-last-10 scaling; oversized blocks penalized |
| `SPEND_MATURITY` | 10 blocks | coinbase outputs locked for ring-decoy integrity |
| `HALVING_INTERVAL_BLOCKS` | 315,360 | = 2 years at 120 s (RFC-0001; the older 787,750 figure assumed 60 s blocks — wrong cadence, see spec §4.1) |
| `MAX_SUPPLY` | 21,000,000 PRSM | approached asymptotically, then tail |
| `TAIL_ANNUAL_INFLATION` | ≈0.6% | consensus-fixed, non-discretionary (D1) |
| `BASE_FEE_SHARDS` | 10,000 (=0.0001 PRSM) | fixed; priority ≤10× base or tx is non-standard |
| `RING_SIZE_MIN/TARGET` | 16 / 32 | enforced in tx validation |
| `FORK_VOTE_WINDOW` | 181 blocks | version-bit voting, ≥90-day notice, 2-week grace |
| Timestamp tolerance | ±2 blocks | plus monotonic-difficulty check |

Networks: **mainnet** (planned), **refraction** (Phase-1 testnet target). Same code path, different `Params`.

## Block anatomy (`prism/chain/block.py`)

Canonical binary encoding with strict validation — every field has exactly one valid byte representation, because ambiguity in serialization is where chain-split bugs are born:

```text
header = height(u64) ‖ timestamp(u64) ‖ prev_hash(32) ‖ merkle_root(32)
       ‖ im_merkle_root(32) ‖ denylist_root(32)
       ‖ pow(diff_target ‖ nonce ‖ viewkey_hash)
       ‖ version(major u16 ‖ minor u16 ‖ vote ∈ {0,1} canonical!)
       ‖ size_bytes(u32) ‖ miner_tx_ref(32)
```

Notable fields:
- **`im_merkle_root`** — accumulator over all spent key images; lets nodes prove "this image was never seen" cheaply.
- **`denylist_root`** — weekly sanctioned-output accumulator root published *for reference*, never enforced by consensus (D5 design; see [Prism Protocol](The-Prism-Protocol.md)).
- **`version.vote`** — canonical domain `{0,1}` enforced in both constructor and parser; bool/int coercion variants are rejected so parse→serialize round-trips are exact (fuzz-tested).

## Proof of work (`prism/chain/pow.py`)

Target algorithm: **RandomX** — CPU-friendly, ASIC-resistant memory-hard PoW, mined with a per-block `viewkey_hash` seed to prevent precomputation. Phase-1 reference code ships behind a `PoWBackend` interface:

- `assert_miner_backend()` refuses placeholder backends on mainnet parameters — the demo engine literally cannot mine real coins.
- Real RandomX integration lands with the daemon (Phase 1 acceptance: three independent nodes syncing 72 h on Refraction).

Difficulty adjusts via DCR-style windowed retarget (`difficulty.py`): bounded per-step change prevents both stagnation and oscillation after hashrate shocks.

## Emission (`prism/chain/emission.py`)

Pure function of height → `(base_shards, tail_shards, miner_share, dev_fund_share)`:

- Halving curve toward the 21M cap, then constant tail (D1).
- Dev-fund carve-out is a fixed percentage of each reward; everything else goes to the miner.
- Property tests assert monotonic convergence to cap, exact zero at genesis, and continuity across halving boundaries. Rationale prose lives in [Monetary Policy Rationale](Monetary-Policy-Rationale.md).

## Node validation rules (`prism/chain/node.py`)

`ChainState.validate_and_apply(block)` enforces, in order:

1. Header hash meets declared difficulty target; timestamp within tolerance; monotonic-difficulty sanity.
2. `prev_hash` links; height increments exactly.
3. Merkle roots match included transactions (txs + coinbase).
4. Coinbase pays *exactly* `emission_at(height)` + collected fees — no more (inflation rule).
5. Per transaction: inputs exist and unspent (key-image set), rings sized ≥ min, CLSAG verifies, range proofs verify, commitment balance equation holds (`Σin − Σout = fee·H + blinder·G`), maturity respected.
6. Reorg handling: longest-work wins; state transitions reversible per block.

## Transaction format summary ([`spec.md` §6.1](../spec.md))

RingCT v2 txs carry: ring inputs (members, key image, CLSAG proof), Pedersen-commitment outputs with stealth addresses + Bulletproofs+ range proofs + optional encrypted memo, explicit fee (the only visible amount), optional lock time, and typed attachments (`anchored_disclosure`, `channel_open/close`). Full JSON schema in the spec.

## Fork & upgrade policy

Version bits voted per block; 181-window tally; upgrades activate with ≥90 days' announced notice and 2-week grace; frozen-consensus testing rig guards against bricking (E18). Circuit IDs and crypto-algorithm IDs version separately so ZK layer can rotate without hard forks ([Security Model](Security-Model.md) E6).

---

**Next:** [Cryptography Deep Dive](Cryptography-Deep-Dive.md) · [Running a Node](Running-a-Node.md) · [Testing & Quality](Testing-&-Quality.md)
