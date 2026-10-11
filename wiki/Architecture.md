# Architecture Overview

*The technical map of Prism: what the layers are, what each one trusts, and where this repository's code lives. The binding source of truth is [`spec.md` §3](../spec.md); this page explains it.*

---

## The four layers at a glance

```text
┌─────────────────────────────────────────────────────────┐
│  L3 — Accessibility Layer (Wallet Apps / SDK)           │
│   • MPC key shares (threshold EdDSA/Frost-style)        │
│   • Social recovery coordinator, dead-man switch        │
│   • Biometric unlock (device-bound, never on-chain)     │
│   • Neuro-inclusive UI system                           │
├─────────────────────────────────────────────────────────┤
│  L2 — Intelligence Layer (On-Device AI)                 │
│   • NL Transaction Compiler (utterance → intent JSON)   │
│   • Fraud Detection Model (on-device)                   │
│   • Transaction Simulator (explain-before-sign)         │
│   • Autonomous Agent Runtime (scoped, veto-windowed)    │
│   • Federated Learning Client (DP + secure aggregation) │
├─────────────────────────────────────────────────────────┤
│  L2-lite — Payment Channels                             │
│   • Bidirectional PRSM channels (Lightning-like)        │
├─────────────────────────────────────────────────────────┤
│  L1 — Base Chain (Daemon, P2P)                          │
│   • RandomX PoW, adaptive difficulty, 120 s blocks      │
│   • RingCT: stealth addresses, CLSAG rings, Pedersen    │
│     commitments, Bulletproofs+ range proofs             │
│   • ZKP sublayer (PLONK) for selective disclosure       │
│   • Dynamic block size 2–4 MB, fixed base fee policy    │
└─────────────────────────────────────────────────────────┘
```

**Read bottom-up:** L1 is the ledger and its rules; L2-lite moves L1 value off-ledger for instant micro-payments; L2 is intelligence that runs *on your device*; L3 is everything the human actually touches.

---

## Trust boundaries (the most important diagram in the project)

| Boundary | What it means in practice |
|---|---|
| **Chain ↔ everyone** | The chain trusts *only consensus*. It validates math (signatures, range proofs, key-image uniqueness) — never identities, never "allowed" participants. No allowlists, no admin keys. |
| **Wallet ↔ device OS** | The wallet trusts the device for biometric gating (secure enclave wraps the local share). Biometric data never leaves the device and never touches the network. |
| **Wallet ↔ MPC signers** | Spend authority requires a threshold of key shares (2-of-3 solo, 3-of-5 social). Below-threshold shares are mathematically useless. Per Decision D4, **Prism operates no recovery leg of any kind** — every share belongs to the user or their chosen contacts. Zero vendor custody surface. |
| **AI ↔ signing** | The AI layer proposes, compiles, simulates, and explains — but its output must pass a *deterministic* validator before any signature request, and the signer enforces agent policy independently of the model. A hallucinating or compromised LLM cannot exceed a grant or forge a spend. |
| **User ↔ disclosures** | Disclosure proofs are bound to (statement, verifier nonce, expiry). Verifiers reject expired proofs. The honest UX contract: *"You can limit and expire disclosures; you cannot recall one already accepted."* |

---

## Repository map

```text
prism/
├── crypto/          # L1 primitives — pure Python, stdlib only
│   ├── field.py         # curve order & scalar arithmetic mod ℓ
│   ├── edwards.py       # Ed25519 curve points (extended coords)
│   ├── pedersen.py      # value/blinder commitments C = v·H + r·G
│   ├── hashing.py       # domain-separated Keccak-style hashes
│   ├── stealth.py       # one-time recipient addresses (DH)
│   ├── clsag.py         # aggregated ring signatures (sender hiding)
│   └── bulletproof.py   # 64-bit range proofs (amount hiding, no setup)
├── chain/           # L1 rules & node skeleton
│   ├── params.py        # consensus constants, networks (refraction testnet)
│   ├── emission.py      # halving curve → 21M cap + tail (D1), dev-fund carve-out
│   ├── difficulty.py    # DCR-style adaptive retarget (60-block window)
│   ├── pow.py           # PoW interface (RandomX target; placeholder for Phase-1 demo)
│   ├── block.py         # canonical header/block encoding, strict validation
│   ├── denylist.py      # sanctioned-output accumulator roots (weekly, D5)
│   ├── node.py          # ChainState: accept/reject rules, key images, maturity
│   └── cli.py           # `prism` command: genesis / emit / mine / params
├── mpc/             # L3 key management
│   ├── secp256k1.py     # curve ops for threshold ECDSA experiments
│   ├── sharing.py       # Shamir split / verify / refresh
│   ├── gg20.py          # GG20-style threshold signing rounds
│   ├── frost.py         # FROST-style EdDSA threshold candidates
│   └── recovery.py      # quorum ceremony, 72 h timelock, cancellation
├── zk/              # Prism Protocol circuits (selective disclosure)
│   ├── field.py         # BN-family scalar field for PLONK
│   ├── plonk.py         # PLONK-ish prover/verifier (reference grade)
│   └── circuits.py      # provenance / solvency / income / reserve / clean-exit
├── wallet/
│   ├── intent.py        # strict Intent schema + grammar-validated compiler
│   └── agent_auth.py    # capability grants, veto windows, hash-chained audit log
├── ai/
│   ├── fraud.py         # rule overlay + scoring model (prototype)
│   ├── simulator.py     # deterministic executor + plain-English explainer
│   └── fl_client.py     # DP-clipped federated update client
├── l2/              # payment channels (Phase 3)
└── tests/           # property tests, fuzz harnesses, acceptance suites
```

### How modules connect (happy-path send)

```text
 utterance ──▶ wallet/intent.py ──▶ ai/simulator.py ──▶ user confirm
                     │  (strict schema)      (preview + risk score)     │
                     ▼                                                  ▼
              crypto/stealth.py ── crypto/pedersen.py ── crypto/bulletproof.py
              (one-time address)    (hidden amounts)      (range proof)
                     └────────── crypto/clsag.py (ring signing via MPC shares)
                                          │
                                          ▼
                              chain/node.py validate_and_apply()
                              (key images, balance-of-commitments, PoW, maturity)
```

---

## Design decisions that shaped the architecture

Summarized from the decision log ([`spec.md` §14](../spec.md)); read there for full rationale.

| ID | Decision | Architectural consequence |
|---|---|---|
| **D1** | Halving curve to 21M + ≈0.6%/yr tail; zero pre-mine; dev fund carved from future emission | `chain/emission.py` is consensus-critical and fully tested; no genesis allocations exist to audit away |
| **D2** | zk-SNARKs (PLONK) for disclosure circuits; amounts stay Bulletproofs+ | Two separate ZKP stacks: trusted-setup circuits (`zk/`) vs. setup-free range proofs (`crypto/bulletproof.py`) |
| **D3** | Revocability = expire-and-rotate, nothing more | Proofs bind to nonce+expiry; Disclosure Registry is a local-first artifact; no recall promises anywhere in the stack |
| **D4** | Recovery purely user-owned | `mpc/recovery.py` has no server leg; contact quorums + timelocks only |
| **D5** | Elected Compliance Council signs weekly denylist roots; parallel lists first-class | `chain/denylist.py` treats roots as *inputs*, not enforcement; circuits reference a specific root hash |

---

## Scaling & upgrade posture

- **Blocks:** dynamic size (median-last-10 scaling, soft target 2–4 MB) with oversized-block penalties keeps fee pressure off users during bursts.
- **Fork policy:** version-bit voting over 181-block windows, ≥90-day notice, 2-week grace. `block.py`'s canonical `version_vote` encoding exists precisely so forks can't be signaled ambiguously.
- **Privacy at scale:** decoy sampling is recency-weighted with age-bucket floors; pruned nodes deliberately *cannot* serve good decoys, which is why wallets default to full nodes ([Running a Node](Running-a-Node.md)).
- **Post-quantum:** tracked as research area R1; the layered design means the signature/ring layer can be re-instantiated without touching monetary rules.

---

**Next:** [Chain Rules & Monetary Policy](Chain-Rules-and-Monetary-Policy.md) · [Cryptography Deep Dive](Cryptography-Deep-Dive.md) · [Testing & Quality](Testing-&-Quality.md)
