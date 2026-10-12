# Roadmap

*Where Prism is, what's being built next, and the acceptance gates between phases. Binding source: [`spec.md` §13](../spec.md); this page adds status color as of **October 2026**.*

---

## Status snapshot (October 2026)

**Phase 1 — Foundation: in progress, majority of core logic implemented.** The repository holds a pure-Python reference implementation with property tests and fuzz harnesses: crypto primitives (field/curve/hashing/Pedersen/stealth/CLSAG/Bulletproofs+), chain rules (params/emission/difficulty/block codec/node validation/CLI), MPC key management + recovery state machine, ZK disclosure circuits + PLONK-style verifier, wallet intent compiler + agent grants, and AI prototypes (fraud scoring, deterministic simulator, FL client). Phase-1 core work has landed — including the shipped lean-auth wallet (Amendment A2), GUI v2 installers for Windows/macOS/Linux, and production-parity Refraction testnet with faucet, mining and transaction soak coverage in CI. Remaining: full RingCT output-mixing depth inside `ChainState`, public multi-node networking, and the neuro-inclusive usability study.

```text
Phase 1 ████████░░░░░░░░░░░░░░░░░░░░░░░░  Foundation (core logic done; tx layer + netcode next)
Phase 2 ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  Intelligence (prototypes exist; GA pending)
Phase 3 ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  Accessibility
Phase 4 ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  Ecosystem / mainnet
```

## Phase 1 — Foundation (Months 1–6) 🚧

| Deliverable | Acceptance criterion | State |
|---|---|---|
| L1 daemon skeleton: RandomX PoW, 2-min adaptive blocks, Dandelion++ | 3 independent nodes sync & sustain 72 h on **Refraction** testnet | Rules engine ✅ · netcode 🚧 |
| RingCT core: CLSAG rings, Pedersen commitments, stealth addresses, bulletproof-range integration | Spend-to-self loop passes property tests ≥10⁶ iterations | Primitives ✅ · tx-layer wiring 🚧 |
| Disclosure Registry + scoped view-key mechanics | Off-chain verifier v0 works end-to-end | Circuits/encoding ✅ |
| MPC wallet prototype: 2-of-3 solo + share ceremony | Ceremony completes abort-safe/resumable | 📦 logic shipped, retired by A2 (kept unwired) |
| Lean-auth wallet: sealed key + CAPTCHA gates + GUI v2 | 60-second setup; every payment human-gated | ✅ shipped v0.1.4–0.1.5 (`auth/`, `gui/`) |
| Testnet parity (faucet, mining, tx soak) | Cross-validated blocks CLI↔GUI↔daemon | ✅ 10 pinned tests |
| NL Intent Compiler v0 | 20-intent benchmark ≥95% correct compile-or-safe-fallback | Schema/compiler ✅ |

## Phase 2 — Intelligence (Months 7–12) 🗓️

- On-device fraud model v1 + rule packs — target FPR <0.5% vs curated phishing corpus.
- Transaction simulator GA + explanation templates (prototype exists).
- Federated learning pipeline: mixnet, DP accounting, aggregator.
- Agent runtime with policy-enforcing signer; veto-window UX.
- ZKP circuits v1 (Source Provenance + Balance Solvency) audited by ≥2 external firms.
- Bug bounty program opens.

## Phase 3 — Accessibility (Months 13–18) 🚧 in progress

- ✅ Social recovery GA (3-of-5): timelock + cancellation flows hardened — implemented & formally tested (`tests/test_rpc_gui.py::TestRecovery`, spec §13).
- 🧪 Biometric share-gating: iOS Secure Enclave / Android StrongBox — beta: `prism/mpc/biometric.py` (SE interface, device-bound sealing, lockout + attestation) with desktop simulated SE; native attestation lands with device builds.
- ⏳ Neuro-inclusive design system shipped; usability study n≥20 neurodiverse participants — open external human-subjects task (cannot close in code).
- 🧪 Payment channels + watchtower beta (`prism/l2/`) — beta: `channels.py` implemented & tested (`test_l2_fees_channels.py`, `phase3_smoke.py`); pending node-mempool CLTV wiring.
- ✅ Fee-predictability hardening: priority-cap enforcement, block-packing policy — `prism/l2/fees.py` (lattice N·BASE, 1≤N≤10; deterministic quotes; fee-density packing), tested.

## Phase 4 — Ecosystem (Months 19–24) 🗓️

- **Mainnet launch** under D1 emission parameters (halving → 21M cap, ≈0.6%/yr tail, zero premine, dev-fund carve-out audited pre-launch).
- Developer SDK (Rust/C/TS bindings) + verifier SDK for accountants/institutions.
- Merchant toolset: channel receiving, `prsmr:` invoicing.
- Privacy-preserving DeFi primitives (collateral solvency proofs in escrow-lite).
- Compliance toolkit: tax-season wizard, auditor portal on disclosure circuits.

## Security gates (cross-phase, non-negotiable)

1. Formal audit of consensus + RingCT **before mainnet**.
2. Separate ZKP circuit audits per release.
3. Continuous fuzzing harnesses (chain parsing, wire formats) — already running in CI.
4. Property-test thresholds per phase (e.g., RingCT spend-loop ≥10⁶ iterations).
5. Release pipeline blocks on failing test suites ([Testing & Quality](Testing-&-Quality.md), [Release Process](Release-Process.md)).

## Known open research areas (do not block delivery)

R1 post-quantum migration path · R2 mobile ZKP prover optimization · R3 recovery collusion resistance · R4 multi-jurisdiction disclosure interop · R5 consent-based cognitive-load telemetry · R6 forward-revocable disclosures (descoped from v1) · R7 Compliance Council election/slashing mechanics. Details: [`spec.md` §15C](../spec.md).

## How to follow progress

- Releases are tagged `vX.Y.Z` with per-OS binaries + checksums (see [Release Process](Release-Process.md)).
- Phase milestones land as PRs referencing spec sections; the decision log ([§14](../spec.md)) is append-only — if a binding choice changes, it appears there first.
- Testnet access announcements will appear in release notes; no ETA promises beyond the month ranges above.

---

**Next:** [Architecture Overview](Architecture.md) · [Governance](Governance.md) · [Contributing](Contributing.md)
