# Project Prism — Technical Specification (spec.md)

**Project:** Prism
**Ticker:** PRSM
**Tagline:** "Clarity on your terms"
**Document Version:** 0.9 (Draft for review)
**Status:** Pre-implementation. Sections marked ❓ depend on answers to the Clarifying Questions in §14.

---

## Table of Contents

1. Vision & Scope
2. Design Principles & Non-Goals
3. System Architecture Overview
4. Monetary Policy & Consensus (L1)
5. Privacy Model & The Prism Protocol (Selective Disclosure)
6. Data Model
7. User Flows
8. AI Integration Layer (L2)
9. Wallet, Key Management & Recovery (L3)
10. Fees, Economics & L2 Channels
11. Edge Cases, Threat Model & Failure Modes
12. Regulatory & Compliance Posture
13. Development Roadmap & Milestones
14. Open Questions / Clarifications Needed
15. Appendices (Glossary, Reference Parameters)

---

## 1. Vision & Scope

Prism is a privacy-first, AI-native Layer-1 cryptocurrency that combines:

- **Bitcoin-grade monetary rigor** — fixed supply, auditable emission, PoW security.
- **Monero-grade default privacy** — obscured sender, receiver, and amount on every transaction.
- **Novel selective disclosure ("The Prism Protocol")** — user-controlled zero-knowledge proofs that reveal only chosen facts about chosen funds, to chosen parties, for chosen durations.
- **AI-native accessibility** — on-device intelligence that makes self-custody forgiving, understandable, and low-friction for non-technical users.

**Primary persona (v1 focus):** Independent creative professionals (e.g., music producers) who receive irregular global income, pay collaborators frequently, value privacy, and are underserved by hostile crypto UX.

**In scope for v1:** Base chain, RingCT + ZKP layer, MPC wallet with social recovery, AI-assisted native wallet app, micro-fee economy, scoped auditor view keys.

**Out of scope for v1:** General-purpose smart-contract VM, permissioned enterprise ledger mode, exchange/custodial services, mobile-only limitation (desktop daemon supported).

---

## 2. Design Principles & Non-Goals

### Principles
1. **Forgiving by Default** — Mistakes should be recoverable; self-custody must not require perfection. Every destructive action has a delay window or undo path where cryptographically possible.
2. **Private by Default, Transparent by Choice** — All transactions are private unless the user explicitly generates a disclosure. No "privacy-off" nudges.
3. **Intelligent, Not Intrusive** — AI proposes, explains, and automates reversible actions. AI never signs or broadcasts an irreversible transaction without explicit, informed human consent (except pre-authorized scoped agent actions, which carry veto windows).
4. **Neuro-Inclusive UX** — Interfaces accommodate ADHD, anxiety, and cognitive diversity: progressive disclosure, calm defaults, no dark patterns, adjustable notification intensity, plain-language everything.
5. **Predictable Micro-Fees** — Fee uncertainty is a UX bug. Base fee is fixed; priority fee is bounded.

### Non-Goals
- Not an Ethereum competitor; no general smart-contract platform in v1.
- Not fully anonymous against a global passive adversary with quantum computers (see §11 threat model limits).
- Not a compliance coin: no mandatory backdoor, no universal view key held by any foundation or government.

---

## 3. System Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│  L3 — Accessibility Layer (Wallet Apps / SDK)           │
│   • MPC key shares (GG20-style threshold ECDSA/EdDSA)   │
│   • Social recovery coordinator, dead-man switch        │
│   • Biometric unlock (device-bound, never on-chain)     │
│   • Neuro-inclusive UI system                           │
├─────────────────────────────────────────────────────────┤
│  L2 — Intelligence Layer (On-Device AI)                 │
│   • NL Transaction Compiler (LLM → intent JSON → tx)    │
│   • Fraud Detection Model (on-device, small CNN/GBDT)   │
│   • Transaction Simulator (explain-before-sign)         │
│   • Autonomous Agent Runtime (scoped, veto-windowed)    │
│   • Federated Learning Client (DP + secure aggregation) │
├─────────────────────────────────────────────────────────┤
│  L2-lite — Payment Channels                             │
│   • Bidirectional PRSM channels (Lightning-like)        │
├─────────────────────────────────────────────────────────┤
│  L1 — Base Chain (Daemon, P2P)                          │
│   • RandomX PoW, adaptive difficulty, 2-min blocks      │
│   • RingCT (stealth addresses, ring signatures/CLSAG,   │
│     Pedersen commitments, range proofs)                 │
│   • ZKP sublayer (PLONK/Sonic circuit set) for          │
│     selective disclosure proofs                         │
│   • Dynamic block size 2–4 MB, fixed base fee policy    │
└─────────────────────────────────────────────────────────┘
```

**Trust boundaries:** The chain trusts only consensus. The wallet trusts the device OS (biometrics), MPC signers (threshold), and the user's disclosure decisions. The AI layer is advisory except within explicitly granted agent scopes.

---

## 4. Monetary Policy & Consensus (L1)

### 4.1 Token Parameters
| Parameter | Value | Notes |
|---|---|---|
| Ticker | PRSM | Display unit: PRSM; base unit: "shard" = 1e-8 PRSM (8 decimals) |
| Max supply | 21,000,000 PRSM | Hard cap, Bitcoin-style scarcity |
| Emission | Tail-emission model ❓ | Option A: halving every 2 years until ~block 2M, then constant tail (≈0.6%/yr) to incentivize post-cap security. Option B: pure capped with fee-only security after cap. **Needs decision (§14 Q1).** |
| Block time | 120 seconds | Adaptive difficulty retargets over a 60-block window |
| Block size | Dynamic, soft target 2–4 MB | Median-last-10 scaling; oversized blocks pay penalty fee |
| Locking / spend maturity | 10 blocks (≈20 min) | Incoming funds require 10 confirmations before spending (RingCT decoy integrity) |
| Pre-mine | None ❓ | Assumed none; team allocation via public vesting from future emission? **Needs decision.** |

### 4.2 Consensus
- **RandomX** proof-of-work (ASIC resistance, CPU-friendliness) with Prism-specific tweaks:
  - Adaptive difficulty per Monero's DCR-style algorithm tuned for 120 s blocks.
  - Timestamp tolerance ±2 blocks; monotonic difficulty check.
- **Transaction relay:** Dandelion++ (stem/fluff) for sender-linkability protection at the network layer. Epoch length 64 blocks; embargo 5–8 hops randomized.
- **Fork policy:** Upgrades signaled via version-bit voting over 181-block windows (Monero-style), with 2-week grace periods announced ≥90 days ahead.

### 4.3 Node Types
1. **Full archival node** — validates all rules, stores complete encrypted output history (commitments visible; values hidden).
2. **Pruned node** — stores recent blocks + Merkle proofs; cannot serve arbitrary historical decoys (degrades ring quality; discouraged for wallets).
3. **Indexer node (optional)** — maintains view-key-scoped indexes for owners who run their own infrastructure. Never stores plaintext balances of others.

---

## 5. Privacy Model & The Prism Protocol (Selective Disclosure)

### 5.1 Baseline Privacy (always on)
- **Amounts:** Pedersen commitments `C = v·H + r·G` with 64-bit range proofs (bulletproofs+) — *note: exact proving system is an open research item (§14 Q2).*
- **Senders:** Ring signatures (CLSAG) over decoy sets of size ≥ 16 (target 32). Decoys sampled from a recency-weighted, popularity-aware distribution to blunt mass-decoy attacks.
- **Receivers:** One-time stealth addresses derived via Diffie-Hellman from the recipient's public view key. Standard and sub-addresses supported.
- **Network:** Dandelion++ hides originating IP from peers.

### 5.2 Key Taxonomy
| Key | Purpose | Revealed to user? | On-chain artifact? |
|---|---|---|---|
| Spend key `a` | Authorize spends | Yes (MPC shares) | Public spend key `A` in payment address |
| View key `b` | Scan chain for incoming outputs | Yes | Public view key `B` in payment address |
| Payment address | Receiving | Yes | n/a (user-shared) |
| Scoped view key `V(s,t)` | Auditor access to subset | Only when disclosed | n/a (off-chain, shared out-of-band) |
| Disclosure proof `π` | Prove a fact about funds | Generated on demand | Verified on-chain or off-chain (choice below) |

### 5.3 Disclosure Primitives (ZKP circuits)
Each is a PLONK/Sonic circuit operating against the public commitment tree, using a **one-time nullifier-tagged disclosure**: generating a proof does not link to the original spend graph beyond the proven statement.

1. **Source Provenance Proof** — "Output O was not received from any address on sanctioned-list L." Verifies membership/non-membership against a hash-chained, community-maintained denylist accumulator (denylist root published weekly on-chain as a 32-byte header field).
2. **Balance Solvency Proof** — "My unspent commitments sum ≥ X over period [t1,t2]" without revealing which outputs or total balance.
3. **Income Attribution Proof** — "Incoming payments totaling exactly Σ during [t1,t2] were received by me" (for tax reporting), optionally enumerating counterparties who consented to being named.
4. **Reserve/Attestation Proof** — "I hold collateral C" for DeFi or merchant escrow.
5. **Clean-Exit Proof** — "This output will be spent only to addresses I control" (useful for exchange off-ramps).

### 5.4 Revocability Semantics
- Disclosures are **one-time artifacts**: a proof `π` is bound to `(statement, verifier-nonce, expiry-timestamp)`. Reuse requires regeneration.
- A **Disclosure Registry** (per-user, device-synced, optional on-chain anchor Merkle root) logs every generated proof so the user can see what they've revealed and when.
- **Revocation caveat (must state plainly in UX):** cryptographic proofs already verified by a third party cannot be un-sent. "Revocable" means (a) future verifiers reject expired proofs via timestamp binding, and (b) scoped view keys can be rotated such that new data is inaccessible. The spec commits to honest UX language: *"You can limit and expire disclosures; you cannot recall one already accepted."* ❓ *(§14 Q3)*

### 5.5 On-chain vs Off-chain Verification
- Default: **off-chain verification** (verifier checks proof against public chain state locally). Keeps volume off L1.
- Optional: proofs may be anchored on-chain (as memo-tagged transactions) when both parties want a consensus-recorded attestation (e.g., collateral).

---

## 6. Data Model

### 6.1 Chain-Level Structures

```jsonc
// Block Header
{
  "height": 123456,
  "timestamp": 1760000000,
  "prev_hash": "hex(32)",
  "merkle_root": "hex(32)",           // txs + miner reward
  "pow": { "algo": "randomx", "diff_target": "...", "nonce": "...", "viewkey_hash": "..." },
  "im_merkle_root": "hex(32)",        // key image accumulator
  "denylist_root": "hex(32)",         // weekly sanctioned-output accumulator root
  "version": { "major": 1, "minor": 0, "vote": true },
  "size_bytes": 2411520,
  "miner_tx_ref": "txid"
}

// Transaction (RingCT type)
{
  "txid": "hex(32)",
  "version": 2,
  "input_amount": null,               // hidden
  "inputs": [
    {
      "type": "ring_signature",
      "ring_members": ["out_ref_1", "...", "out_ref_16"],  // real index hidden
      "key_image": "hex(32)",         // unique per spend; double-spend detector
      "amount_mask": "pedersen_commitment",
      "proof_clsag": "bytes"
    }
  ],
  "outputs": [
    { "stealth_address": "hex(32)", "commitment": "pedersen", "range_proof": "bytes", "memo_encrypted": "bytes|null" }
  ],
  "fee_shard": 10000,                 // fixed base fee (0.0001 PRSM)
  "priority_shard": 0,                // optional, capped at 10×base
  "lock_time": 0,                     // relative height or absolute UTC
  "attachment": { "type": "none|anchored_disclosure|channel_open|channel_close", "data": "bytes" }
}

// Anchored Disclosure (optional attachment)
{
  "statement_type": "balance_solvency",
  "circuit_id": "prsm.solvency.v1",
  "public_inputs": { "min_amount_commitment": "...", "period": [t1, t2], "verifier_nonce": "..." },
  "expiry_height": 130000,
  "proof": "bytes(plonk)"
}

// Output (Unspent Transaction Output, opaque)
{
  "global_index": 987654321,
  "commitment": "pedersen",
  "stealth_address": "hex(32)",
  "height_received": 123456,
  "spent_key_image": null | "hex(32)"
}
```

### 6.2 Wallet-Side Structures (local DB, encrypted at rest)

```jsonc
// Wallet Account
{
  "account_id": "uuid",
  "name": "Royalties",
  "scheme": "mpc_threshold",          // | "seed_legacy"
  "threshold": 3, "total_shares": 5,
  "signer_registry": [ { "share_id": "...", "device|contact": "...", "transport": "ee2ee" } ],
  "recovery_policy": { "quorum": 3, "timelock_hours": 72, "cooldown_days": 30 },
  "inheritance": { "mode": "dead_man_switch", "beneficiary_pubkey": "...", "inactivity_days": 365 },
  "created_at": "...", "last_sync_height": 123456
}

// Owned Output (post-scan)
{
  "wallet_output_id": "uuid",
  "txid": "...", "output_index": 2,
  "global_index": 987654321,
  "decoded_amount_shard": 5000000000,   // decrypted via view key
  "received_at_height": 123456,
  "maturity": "unlocked|locked|spent",
  "risk_label": { "score": 0.02, "source": "on_device_model_v3", "flags": [] },
  "privacy_tags": ["standard_addr", "subaddr:royalties"]
}

// Contact / Address Book
{
  "contact_id": "uuid",
  "display_name": "Alex (mastering)",
  "payment_address": "prsm1...",
  "verified": true, "verification_method": "qr_in_person|dns_proof|trust_chain",
  "nickname_aliases": ["alex", "alex@studio"],   // used by NL compiler
  "consent_to_be_named_in_disclosures": false
}

// Transaction Intent (the contract between NL layer and signer)
{
  "intent_id": "uuid",
  "raw_utterance": "Send $50 to Alex for the mastering session, keep it private",
  "compiled": {
    "action": "transfer",
    "recipient_ref": "contact:alex",
    "amount": { "fiat_quote": { "currency": "USD", "value": 50.00, "oracle": "medianizer", "max_slippage_pct": 2.0 }, "or_prsm": null },
    "privacy_mode": "default_max",
    "memo": "Mastering session",
    "fee": "base_only"
  },
  "simulation": { "predicted_effects": "...", "risk_score": 0.01, "warnings": [] },
  "confirmation": { "required": true, "method": "biometric", "signed_at": null },
  "status": "draft|awaiting_confirm|broadcast|confirmed|failed|cancelled"
}

// Agent Permission Grant
{
  "grant_id": "uuid",
  "agent": "auto_dutcher.v1",
  "capability": "buy_prsm_fiat_drip",
  "constraints": { "max_per_month_shard": 10000000000, "price_ceiling_usd": null, "destinations_whitelist": ["self_subaddr:dca"] },
  "veto_window_hours": 24,
  "valid_until": "...", "revoked_at": null
}

// Agent Action Log
{
  "action_id": "uuid", "grant_id": "...", "proposed_intent": { ...Intent... },
  "state": "proposed|vetoed|executed_after_veto_window|blocked_by_risk_model",
  "explanation": "Bought 12.4 PRSM @ avg $4.03 under your $100/month plan.",
  "hashchain_prev": "hex(32)"   // tamper-evident local audit log
}

// Disclosure Record
{
  "disclosure_id": "uuid",
  "statement_type": "income_attribution",
  "scope": { "period": ["2026-01-01","2026-12-31"], "output_set_digest": "..." },
  "verifier": { "name": "Accountant LLP", "nonce": "...", "channel": "pdf+app_link" },
  "expires_at": "...", "anchored_on_chain": false,
  "registry_root_included_in": "device_backup_snapshot_id"
}

// Payment Channel
{
  "channel_id": "uuid",
  "open_txid": "...", "capacity_shard": 5000000000,
  "counterparty": "merchant:bandcamp-ish",
  "local_balance_shard": 3100000000,
  "commitment_updates": [ { "seq": 41, "sig_local": "...", "sig_remote": "..." } ],
  "state": "open|closing|closed|penalized",
  "watchtower_registered": true
}

// Federated Learning Contribution
{
  "round_id": 20261008, "model": "fraud_det_v3",
  "client_dp_epsilon": 1.0, "clip_norm": 1.0,
  "update_digest": "sha256", "submitted_via_mixnet": true, "reward_eligible": true
}
```

### 6.3 Identifier Conventions
- Addresses: bech32m with prefix `prsm1` (standard), `prsmsub` (sub-address), `prsmr` (request w/ embedded amount+memo).
- All timestamps on-chain: UTC unix seconds; wallet-side ISO-8601.
- Amounts stored as integers (`shard`, 1e-8 PRSM). Floats appear only in fiat-quote layers.

---

## 7. User Flows

### 7.1 First Run & Wallet Creation (Target: <5 min, zero seed required)
1. Download wallet → biometric enrollment (device-local).
2. Choose custody mode:
   - **A. Solo MPC (default):** 2-of-3 shares across phone + desktop + Prism recovery server (server share is *blind* — knows nothing without user co-signature; acts only as one leg of quorum). ❓ *(§14 Q4: is a vendor-operated recovery leg acceptable?)*
   - **B. Social MPC:** user invites 4 trusted contacts; 3-of-5 threshold. Contacts only ever hold opaque key shares — no visibility into funds.
   - **C. Legacy seed:** 25-word mnemonic (advanced; explicitly warned as non-recoverable).
3. Share generation ceremony: each share transfer over EE2EE (QR or invite link); liveness check per signer; abort-safe resumable protocol.
4. Tutorial funded by test drip faucet; first inbound transfer demo.

### 7.2 Receiving
1. User taps "Receive" → picks account/sub-address → shows QR + `prsmr:` URI (optionally with requested amount).
2. Sender pays; wallet scans with view key; shows pending → confirmed at 10 blocks with gentle notification (intensity per user setting).
3. Risk model labels inbound outputs (e.g., "from newly seen address") — informational only.

### 7.3 Natural-Language Send (Happy Path)
1. User types/says: *"Send $50 to Alex for the mastering session, keep it private."*
2. **NL Compiler** produces an Intent (§6.2). Ambiguity handling:
   - Unknown contact → single clarifying chip ("Did you mean Alex Rivera?"), never a wall of options.
   - Fiat amount → live oracle quote shown with locked 60-second rate window and max-slippage guard.
3. **Simulator** renders plain-English preview: "This sends ≈12.4 PRSM ($50) to Alex. Fee: $0.001. It will appear private to everyone except Alex and you. Confirm?"
4. **Fraud model** scores destination/history; if score > threshold, adds a calm interruption card with reasons and a 10-second pause option ("sleep on it").
5. Biometric confirm → MPC signing round (2–3 signers online) → broadcast via Dandelion++.
6. Receipt with copyable disclosure ID (so user can later prove this payment existed, if desired).

### 7.4 Social Recovery
1. User loses primary device → starts recovery from new device with identity attestation (biometric re-enrollment + old-share challenge token if available).
2. Needs quorum (3-of-5): contacts approve via simple app prompt ("Confirm this is Maya recovering her wallet? Code: 4821").
3. **72-hour timelock** before recovered shares can move funds; user's remaining devices + email/push get loud warnings throughout; anyone holding a stale share can cancel the recovery during the window (prevents stolen-phone + coerced-contact attack).
4. New share set re-generated (proactive refresh); old shares revoked.

### 7.5 Coercion / Duress Flow
- Duress PIN unlocks a **decoy wallet** (plausible low balance) instead of signaling failure.
- Recovery timelocks double as anti-coercion: forced recovery still takes 72 h with visible alarm state. ❓ *(§14 Q5: how aggressive should alarms be given privacy-under-coercion concerns?)*

### 7.6 Selective Disclosure (Tax Season)
1. Wallet → "Privacy Tools → Prepare tax report."
2. User selects period + statement type (Income Attribution).
3. App builds proof locally, shows exactly what will and won't be revealed (side-by-side diagram).
4. User shares proof package to accountant's verifier app (or web verifier). Proof expires 90 days post-issue by default.
5. Disclosure Registry entry created; user sees "Active disclosures" dashboard.

### 7.7 Autonomous Agent (DCA Example)
1. User enables "Dutch Round-Up": convert up to $100/mo spare change to PRSM.
2. Agent creates proposals; each appears in a quiet digest; **24 h veto window**; auto-executes only if unvetoed AND risk model clean.
3. Monthly explainable report; one-tap revoke; all actions in hash-chained audit log.

### 7.8 Inheritance (Dead Man's Switch)
- Beneficiary holds a share that activates only after N days (default 365) of zero user chain activity AND M-of-K attestator confirmation (attestators are contacts or lawyer/oracle service). Grace ping to user every 30 days before activation. ❓ *(§14 Q6: on-chain activity as liveness signal leaks usage patterns — acceptable? Alternative: signed heartbeat messages.)*

---

## 8. AI Integration Layer (L2)

### 8.1 Deployment Constraint
All inference is **on-device**. No transaction content, balances, or raw utterances leave the device except standard P2P protocol traffic. Cloud fallback is opt-in, redacted, and never used for signing-related reasoning.

### 8.2 Components
| Component | Architecture (initial) | Size budget | Update channel |
|---|---|---|---|
| NL Intent Compiler | Distilled instruction-tuned LLM (≤3B params, quantized INT4) + deterministic grammar validator; LLM output MUST parse into strict Intent schema or fall back to form UI | ≤2 GB RAM peak | Signed model bundle, monthly |
| Fraud Detector | Gradient-boosted trees + small sequence model over address-behavior features; rule overlay for known scam patterns | ≤50 MB | Weekly feature/rule packs |
| Tx Simulator | Deterministic executor against local chain snapshot + template explainer (not generative for numeric claims) | ≤200 MB | With daemon releases |
| UX Optimizer | On-device contextual bandit (no cross-user learning) | ≤5 MB | Local only |

**Safety invariant:** The LLM compiles *intents*; it never derives keys, never sees full private keys/shares, and its output passes through a deterministic validation layer before any signature request. Numeric facts in previews come from the simulator, not the LLM (anti-hallucination boundary).

### 8.3 Federated Learning
- Goal: improve fraud detection globally without user data leaving devices.
- Mechanism: FedAvg + client-side DP (ε ≤ 1.0 per round, Gaussian mechanism), secure aggregation among ≥256 contributors per round; contributions routed through a mix network; per-round opt-in with visible reward (small FL mining-credit share? ❓ §14 Q7).
- Governance: model cards published per release; poisoning defenses (norm clipping, median-based aggregation, contribution reputation decay).

### 8.4 Agent Runtime
- Capability model: allowlisted verbs (`transfer.self`, `buy_drip`, `channel_topup`, `report_generate`) — no arbitrary contract calls in v1.
- Constraints compiled to a policy engine enforced *outside* the LLM (belt-and-braces: even a compromised agent process cannot exceed grant bounds because the signer enforces policy at signing time).
- Veto windows: default 24 h; instant-execution allowed only for whitelisted recipients + amounts under a user-set "comfort threshold."

---

## 9. Wallet, Key Management & Recovery (L3)

- **Threshold signatures:** GG20-type ECDSA over Ed25519 variant (frost/threshold-eddsa candidate) for spend authority; key shares distributed across devices/contacts/server-leg.
- **Proactive share refresh:** every 90 days or after any recovery event (protects against long-term share theft).
- **Biometrics:** gate access to the local share only (secure enclave-wrapped share); biometric data never leaves device, never on-chain.
- **Backups:** encrypted wallet snapshot (shares-independent metadata: address book, disclosure registry) to user-chosen cloud with zero-knowledge envelope; restoring snapshot ≠ restoring spend rights (still needs quorum).
- **Legacy seed path:** BIP39-compatible 25-word mnemonic; clearly labeled "irreversible-loss risk" tier.

---

## 10. Fees, Economics & L2 Channels

### 10.1 Fee Policy
- Base fee: **fixed 0.0001 PRSM** (target ≈ $0.001 at launch economics).
- Priority fee: optional, integer multiples of base, **hard cap 10×**, enforced by block-space policy (transactions above cap are non-standard).
- Fee market: within the priority band, blocks pack by priority density; dynamic block size absorbs bursts so base-fee transactions clear within ≤2 blocks at normal load.
- Storage rent consideration for UTXO set growth: **rejected for v1** (adds UX hostility), revisit at scale.

### 10.2 Channels
- Bidirectional, commitment-based (LN-like) with watchtower outsourcing (encrypted penalty blobs).
- Channel open/close cost = 2 base-fee transactions. In-channel payments: effectively zero fee, instant.
- Target use: recurring collaborator payouts, tips, microroyalties.

### 10.3 Fiat Pricing Oracle (wallet-level, not consensus)
- Median-of-N off-chain exchange quotes with signed price feeds; used only for display and slippage guards in intents. Chain itself has no oracle dependency.

---

## 11. Edge Cases, Threat Model & Failure Modes

### 11.1 Crypto-Protocol Edge Cases
| # | Scenario | Handling |
|---|---|---|
| E1 | Double-spend attempt (two spends, same key image) | Key-image uniqueness at consensus level; second tx invalid. |
| E2 | 51% reorg | 10-block maturity + depth-based UI confidence; exchanges advised ≥30. Merchant channel closes protected by penalty tx. |
| E3 | Stuck/lost channel counterparty | Watchtower submits latest commitment; unilateral close with delay; penalty path for cheating close. |
| E4 | Ring decoy exhaustion (old outputs pruned) | Pruned nodes cannot serve decoys → wallets default to archival/full nodes; decoy sampling includes age-bucket floors. |
| E5 | Denylist fork/dispute (who curates sanctions list?) | Community-signed weekly roots; multiple parallel lists supported (verifier chooses list ID); proofs reference specific root — no single censor point. ❓ §14 Q8 |
| E6 | Broken/failing ZKP circuit discovered post-release | Circuit IDs versioned; verifiers reject deprecated IDs; anchored proofs carry circuit ID so audits can flag affected ones. |
| E7 | Expired disclosure presented anyway | Verifier app rejects past-expiry proofs (nonce+expiry binding); accepted-but-expired proofs are verifier's liability — documented in verifier SDK. |
| E8 | MPC share theft (single share) | Below-threshold shares useless; proactive refresh; quorum-change alerting. |
| E9 | Recovery collusion (3 malicious contacts) | Timelock + cancellation by honest share-holder + user-configurable "trusted quorum subsets" (e.g., require ≥1 device share in recovery). |
| E10 | Duress forced send | Duress-PIN decoy wallet; optional "panic lock" freezes outgoing above comfort threshold pending 24 h veto (even for manual sends) — user-toggleable. |
| E11 | Seed-phrase legacy loss | No recovery path — must be surfaced repeatedly at creation and first large send. |
| E12 | Orphaned inheritance (user dies mid-veto grace) | Beneficiary claim proceeds after full inactivity window; attestator quorum prevents false death claims. |
| E13 | NL miscompilation ("send 50" vs "$50" ambiguity) | Strict Intent schema; ambiguous parses always produce a confirm-card, never silent execution; simulator preview is ground truth. |
| E14 | LLM hallucinated explanation | Numeric/asset claims rendered from simulator data templates; LLM text flagged as "explanation, not terms." |
| E15 | Poisoned federated update | Clipping + robust aggregation + reputation decay; emergency global FL pause flag in governance config. |
| E16 | Model bundle supply-chain attack | Signed bundles, transparency log of hashes, pinned versions in wallet; unsigned models refuse to load. |
| E17 | Sanctioned-counterparty receipt | Funds arrive but flagged; spending them doesn't break privacy of unrelated outputs (flagging is local label, not chain blacklist of the output) — user chooses whether to disclose/cooperate. |
| E18 | Chain halt (dev fund / emission dispute) | Not applicable (PoW); however upgrade bricking mitigated by 90-day notice + frozen-consensus testing rig. |

### 11.2 Adversary Capabilities (assumed)
- Global passive network observer: partially mitigated (Dandelion++, not perfect).
- Chain-analysis firms with big-data heuristics: mitigated via decoy distributions; residual risk acknowledged.
- Compromised device (read memory): out of threat model beyond OS-provided enclave protections — stated honestly.
- Quantum (Classically-hard assumptions): roadmap note — hash-based STARK/back-up plans tracked in Research Area R1.

### 11.3 Usability Failure Modes (neuro-inclusive lens)
- Notification fatigue → intensity dials (calm/normal/loud) per event class.
- Panic loops on "pending" states → progress explained in plain words + expected time.
- Overwhelm from advanced screens → progressive disclosure; advanced panel off by default.

---

## 12. Regulatory & Compliance Posture

- Prism Foundation position: privacy software, non-custodial; no company-held keys, no universal access.
- Selective disclosure is the compliance bridge: users can satisfy tax/audit obligations *themselves*, without trusting intermediaries.
- Denylist accumulator (§5.3) is community-curated and provably neutral in structure (multiple lists, no forced consumption).
- Jurisdiction strategy: launch node/wallet code under OSS license (MIT/BSD-2 ❓); entity formation deferred pending counsel (research area R4).
- Age-of-majority and ToS gates on hosted services (recovery-server leg, watchtowers); core protocol permissionless.

---

## 13. Development Roadmap & Milestones

### Phase 1 — Foundation (Months 1–6) ✅ acceptance criteria added
- [ ] L1 daemon skeleton: RandomX PoW, 2-min adaptive blocks, Dandelion++ (Testnet "Refraction") — *Criterion: 3 independent nodes sync & sustain 72 h.*
- [ ] RingCT core: CLSAG rings, Pedersen commitments, stealth addresses, bulletproof-range integration — *Criterion: spend-to-self loop passes property tests ≥10⁶ iterations.*
- [ ] Disclosure Registry + scoped view-key mechanics (off-chain verifier v0).
- [ ] MPC wallet prototype: 2-of-3 solo scheme + share ceremony.
- [ ] NL Intent Compiler v0 (grammar-validated, 20-intent benchmark suite ≥95% correct compile or safe fallback).

### Phase 2 — Intelligence (Months 7–12)
- [ ] On-device fraud model v1 + rule packs; benchmark vs curated phishing corpus (target: FPR <0.5%).
- [ ] Transaction simulator + explanation templates.
- [ ] Federated learning pipeline (mixnet, DP accounting, aggregator).
- [ ] Agent runtime with policy-enforcing signer; veto-window UX.
- [ ] ZKP circuits v1: Source Provenance + Balance Solvency (audited by ≥2 external firms).

### Phase 3 — Accessibility (Months 13–18)
- [ ] Social recovery GA (3-of-5), timelock + cancellation flows.
- [ ] Biometric share-gating (iOS Secure Enclave / Android StrongBox).
- [ ] Neuro-inclusive design system shipped; usability study with neurodiverse participants (n≥20).
- [ ] Payment channels + watchtower beta.
- [ ] Fee predictability hardening (priority cap enforcement, block-packing policy).

### Phase 4 — Ecosystem (Months 19–24)
- [ ] Mainnet launch (emission parameters finalized per §14 decisions).
- [ ] Developer SDK (Rust/C/TS bindings), verifier SDK.
- [ ] Merchant toolset (channel receiving, invoicing via `prsmr:` URIs).
- [ ] Privacy-preserving DeFi primitives (collateral solvency proofs in escrow contracts-lite).
- [ ] Compliance toolkit (tax-season wizard, auditor portal built on disclosure circuits).

### Security gates
- Formal audit of consensus + RingCT before mainnet; separate ZKP circuit audits per release; bug bounty program from Phase 2; fuzzing harness (chain parsing, MPC protocol) continuous.

---

## 14. Open Questions / Clarifications Needed (blocking finalization)

These questions must be answered to promote this draft to v1.0:

1. **Emission & premine:** Fixed 21M cap with tail emission, or pure cap? Any team/community allocation, and from where (premine conflicts with "Bitcoin rigor" — recommend zero premine + transparent dev-fund from emission)?
2. **Proving-system tradeoff:** zk-SNARK (PLONK, smaller proofs, trusted setup per circuit) vs zk-STARK (transparent setup, larger proofs) for mobile clients — which constraint dominates: proof size or trustlessness optics?
3. **"Revocable disclosure" language:** Accept the honest semantics in §5.4 (expire + rotate, not recall), or require a stronger mechanism (e.g., time-lock encryption of auxiliary data shared with auditors, which *is* revocable going forward)?
4. **Vendor recovery leg:** Is the optional Prism-operated blind MPC share (Solo mode) acceptable, or must default recovery be purely user-owned devices/contacts? This decides business model and trust story.
5. **Duress severity:** How loud should recovery/coercion alarms be (push to all contacts? timed public announcement?) balanced against outing a coerced user?
6. **Inheritance liveness signal:** On-chain inactivity (privacy leak) vs signed heartbeats (extra UX burden) as the dead-man-switch trigger?
7. **FL incentives:** Reward federated-learning participants with emission share (creates Sybil/account farming surface) or keep it purely altruistic + reputation?
8. **Denylist governance:** Who signs the weekly sanction accumulator roots — foundation multisig, elected council, or purely opt-in third-party lists with no official root?
9. **Data-model specifics:** Are the schemas in §6 aligned with intended product surfaces (accounts vs sub-addresses, multi-currency intents, memo policy)? Which fields are must-have for MVP vs later?
10. **User-flow scope:** Confirm §7 personas/flows match priorities — especially: is fiat-on-ramp integration (for "$50 to Alex" flows) in v1, or display-only pricing?

---

## 15. Appendices

### A. Glossary
- **Shard:** smallest PRSM unit (1e-8). **RingCT:** Ring Confidential Transactions. **CLSAG:** aggregated ring signature scheme. **Key image:** per-output spend marker preventing double spends. **Dandelion++:** transaction relay obfuscation. **MPC:** multi-party computation (here: threshold signing). **DP:** differential privacy. **Intent:** validated JSON transaction request produced by the NL layer. **Watchtower:** outsourced channel-state guardian.

### B. Reference Parameters (defaults, tunable)
| Param | Default |
|---|---|
| Ring size | 16 (min), 32 (target) |
| Confirmations for spend | 10 blocks |
| Base fee | 0.0001 PRSM |
| Priority cap | 10× base |
| Recovery quorum | 3-of-5 |
| Recovery timelock | 72 h |
| Share refresh interval | 90 d |
| Agent veto window | 24 h |
| Disclosure default expiry | 90 d |
| Dead-man inactivity | 365 d, 30 d pings |
| FL epsilon | 1.0/round |
| Denylist root cadence | weekly |

### C. Research Areas (tracked)
R1 Post-quantum migration path · R2 Mobile ZKP prover optimization (GPU/NPU delegation) · R3 Optimal recovery collusion resistance · R4 Multi-jurisdiction disclosure interoperability · R5 Cognitive-load telemetry methodology (consent-based, on-device metrics only).

---

*This specification is a living document. Version 1.0 will be cut once §14 questions are resolved.*
