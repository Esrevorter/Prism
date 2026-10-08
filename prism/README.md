# Prism (PRSM) — Reference Implementation

Monorepo for the Prism L1 daemon and its companion components.
Authoritative design document: [`../spec.md`](../spec.md) (v1.0).

## Layout

| Path | Component | Phase | Status |
|---|---|---|---|
| `chain/` | L1 node core: block header, emission curve, adaptive difficulty, PoW ABI, denylist accumulator | 1 | 🟩 skeleton running |
| `crypto/` | Cryptographic primitives (Ed25519-compatible curve, Pedersen, CLSAG stubs) | 1 | 🟨 scaffolded |
| `zk/` | PLONK / ultra-honk disclosure circuits (D2) + SRS ceremony tooling | 1-2 | 🟨 scaffolded |
| `mpc/` | GG20-style threshold key shares, social recovery ceremony | 1 | 🟨 scaffolded |
| `wallet/` | Intent compiler, NL interface contract, local store | 1-2 | 🟨 scaffolded |
| `ai/` | On-device models: fraud detector, simulator, UX optimizer; FL client | 2 | 🟨 scaffolded |
| `l2/` | LN-style micropayment channels | 3 | ⬜ planned |
| `proto/` | Wire-format schemas (canonical serialization) | 1 | 🟨 scaffolded |
| `testnet/` | **Refraction** — public testnet runbook & genesis | 1 | 🟩 config present |

## Quick start (Phase 1 skeleton)

```bash
python3 -m chain.cli genesis --network refraction   # build genesis block
python3 -m chain.cli emit --heights 0,100,315360    # inspect emission schedule
python3 -m pytest                                   # unit tests
```

The current `chain/` package implements, per spec v1.0:

- Block header with the 32-byte `denylist_root` field (§6.1, Decision D5).
- Emission curve: halving every 315,360 blocks (2 years @ 120 s — RFC-0001
  resolved) to a 21M cap, then a
  consensus-fixed tail of ≈0.6%/yr (Decision D1), zero premine, dev-fund
  carve-out as a fixed % of block reward.
- Adaptive difficulty retarget (DCR-style, 60-block window, 120 s target).
- RandomX integration boundary (`chain/pow.py`) — deterministic placeholder
  hash-check now, real librandomx via FFI at hardhat time.

## Ground rules (from spec)

1. `spec.md` is authoritative. Code that contradicts it is a bug in the code
   until argued otherwise in an RFC.
2. No feature ships without the acceptance criteria in §13 of the spec.
3. Privacy defaults are load-bearing: any PR touching decoy sampling,
   Dandelion++, or view-key handling needs a security note.
