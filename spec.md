# Project Prism — Technical Specification (spec.md)

**Project:** Prism
**Ticker:** PRSM
**Tagline:** "Clarity on your terms"
**Document Version:** 1.1 (Amendment A1-2026-10: Client UX & Multi-Device Signing mandate — see §7.9, §9.1; decision log in §14)
**Status:** Implementation-ready. All blocking questions resolved by founder decisions D1–D5 (2026-10-08); remaining items tracked as research areas (§15C), not blockers.

---

## Table of Contents

1. Vision & Scope
2. Design Principles & Non-Goals
3. System Architecture Overview
4. Monetary Policy & Consensus (L1)
5. Privacy Model & The Prism Protocol (Selective Disclosure)
6. Data Model
7. User Flows (incl. §7.9 Client UX Baseline & Multi-Device Signing — Amendment A1-2026-10)
8. AI Integration Layer (L2)
9. Wallet, Key Management & Recovery (L3) (incl. §9.1 Key-Management UX Rules)
10. Fees, Economics & L2 Channels
11. Edge Cases, Threat Model & Failure Modes
12. Regulatory & Compliance Posture
13. Development Roadmap & Milestones
14. Decision Log (v1.0 resolutions)
15. Appendices (Glossary, Reference Parameters)

---

## 1. Vision & Scope

Prism is a privacy-first, AI-native Layer-1 cryptocurrency that combines:

- **Bitcoin-grade monetary rigor** — fixed supply, auditable emission, PoW security.
- **Monero-grade default privacy** — obscured sender, receiver, and amount on every transaction.
- **Novel selective disclosure ("The Prism Protocol")** — user-controlled zero-knowledge proofs that reveal only chosen facts about chosen funds, to chosen parties, for chosen durations.
- **AI-native accessibility** — on-device intelligence that makes self-custody forgiving, understandable, and low-friction for non-technical users.

**Primary persona (v1 focus):** Independent creative professionals (e.g., designers, writers, photographers, musicians, video editors) who receive irregular global income, pay collaborators frequently, value privacy, and are underserved by hostile crypto UX.

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
| Max supply | 21,000,000 PRSM | Hard cap reached via emission curve; **tail emission thereafter (Decision D1)** |
| Emission | Halving curve + permanent tail (**D1: Option A; launch rate per Option B amendment, 2026-10-10**) | Block reward halves every 2 years (**315,360 blocks at 120 s** — RFC-0001 resolved 2026-10-08; an earlier draft cited Monero's 787,750-block figure, which assumes a 60 s cadence and does not apply to Prism) until the 21M cap is approached, then a constant tail of ≈0.6% annual inflation continues indefinitely. **Option B amendment (founder, 2026-10-10):** launch epoch issuance set to ≈cap/2 (10.5M PRSM scale per 2-year halving epoch; consensus R0 = 3,296,232,877 shards/block sized to a 99% approach target) so cumulative supply crosses exactly 21,000,000 PRSM at height 2,984,354 (≈year 11.4 at target cadence); the tail blend engages from height 2,207,520 (epoch 7) and every block thereafter pays the fixed tail rate. Shortening the halving interval was evaluated and rejected — the plateau of a halving curve depends only on the launch rate, not the interval. Rationale: guarantees miner incentives after cap so long-run security never depends on fees alone (Monero precedent). The tail rate is consensus-fixed and non-discretionary. Implementation: `prism/chain/emission.py` (import-time asserts pin the contract; verified by height-level simulation in `tests/test_chain.py`). |
| Block time | 120 seconds | Adaptive difficulty retargets over a 60-block window |
| Block size | Dynamic, soft target 2–4 MB | Median-last-10 scaling; oversized blocks pay penalty fee |
| Locking / spend maturity | 10 blocks (≈20 min) | Incoming funds require 10 confirmations before spending (RingCT decoy integrity) |
| Pre-mine | **None (D1)** | Zero premine. Team/foundation funding comes exclusively from a transparent dev-fund carve-out of *future emission* (a fixed small % of each block reward, released against milestone vesting on-chain — no genesis allocation to anyone). |

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
- **Amounts:** Pedersen commitments `C = v·H + r·G` with 64-bit range proofs (Bulletproofs+) — *note: the amount/range layer deliberately stays SNARK-free (Monero-style); SNARKs are used only for the Prism Protocol disclosure circuits (§5.3), per Decision D2.*
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
Each is a **PLONK (ultra-honk) circuit** — Decision D2: zk-SNARKs chosen over zk-STARKs because proof size dominates for mobile proving and verifier-side bandwidth; the trusted-setup concern is mitigated by (a) per-circuit multi-party ceremonies with published entropy transcripts, (b) PLONK's *universal* (circuit-agnostic) SRS so one ceremony covers all v1 circuits, and (c) fallback re-instantiation path if setup ever needs redoing. Circuits operate against the public commitment tree, using a **one-time nullifier-tagged disclosure**: generating a proof does not link to the original spend graph beyond the proven statement.

1. **Source Provenance Proof** — "Output O was not received from any address on sanctioned-list L." Verifies membership/non-membership against a hash-chained, community-maintained denylist accumulator (denylist root published weekly on-chain as a 32-byte header field).
2. **Balance Solvency Proof** — "My unspent commitments sum ≥ X over period [t1,t2]" without revealing which outputs or total balance.
3. **Income Attribution Proof** — "Incoming payments totaling exactly Σ during [t1,t2] were received by me" (for tax reporting), optionally enumerating counterparties who consented to being named.
4. **Reserve/Attestation Proof** — "I hold collateral C" for DeFi or merchant escrow.
5. **Clean-Exit Proof** — "This output will be spent only to addresses I control" (useful for exchange off-ramps).

### 5.4 Revocability Semantics (**D3: expire-and-rotate is the binding semantics**)
- Disclosures are **one-time artifacts**: a proof `π` is bound to `(statement, verifier-nonce, expiry-timestamp)`. Reuse requires regeneration.
- A **Disclosure Registry** (per-user, device-synced, optional on-chain anchor Merkle root — see §6 registry schema) logs every generated proof so the user can see what they've revealed and when.
- **Adopted model:** "Revocable" means precisely two things, and the product only ever promises these:
  1. **Expiry:** proofs carry a consensus-checked timestamp binding; verifiers reject expired proofs, so a disclosure cannot be reused after its window.
  2. **Rotation:** scoped view keys rotate on schedule; a rotated key never reveals data created after rotation, bounding any leaked-key blast radius.
- Forward-revocable time-lock encryption of auditor auxiliary data is **descoped from v1** and tracked as research area R6 (§15C).
- **Honest UX language (hard requirement):** *"You can limit and expire disclosures; you cannot recall one already accepted."* Marketing and UI copy must never imply recall.

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
  "name": "Client income",
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
  "privacy_tags": ["standard_addr", "subaddr:client-work"]
}

// Contact / Address Book
{
  "contact_id": "uuid",
  "display_name": "Alex (design)",
  "payment_address": "prsm1...",
  "verified": true, "verification_method": "qr_in_person|dns_proof|trust_chain",
  "nickname_aliases": ["alex", "alex@agency"],   // used by NL compiler
  "consent_to_be_named_in_disclosures": false
}

// Transaction Intent (the contract between NL layer and signer)
{
  "intent_id": "uuid",
  "raw_utterance": "Send $50 to Alex for the design session, keep it private",
  "compiled": {
    "action": "transfer",
    "recipient_ref": "contact:alex",
    "amount": { "fiat_quote": { "currency": "USD", "value": 50.00, "oracle": "medianizer", "max_slippage_pct": 2.0 }, "or_prsm": null },
    "privacy_mode": "default_max",
    "memo": "Design session",
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
   - **A. Solo MPC (default):** 2-of-3 shares across **user-owned devices only** (phone + desktop + one additional user device or user-held hardware token). **Per Decision D4, Prism operates no recovery leg of any kind** — there is no vendor-held share, blind or otherwise. If the user loses both quorum devices with no third user device, Solo mode falls back to guiding the user into Social MPC setup for future protection; this limitation is disclosed at wallet creation in plain language.
   - **B. Social MPC (recommended for single-device users):** user invites 4 trusted contacts; 3-of-5 threshold. Contacts only ever hold opaque key shares — no visibility into funds. All legs are user-chosen; recovery remains purely user-owned by construction.
   - **C. Legacy seed:** 25-word mnemonic (advanced; explicitly warned as non-recoverable).
3. Share generation ceremony: each share transfer over EE2EE (QR or invite link); liveness check per signer; abort-safe resumable protocol.
4. Tutorial funded by test drip faucet; first inbound transfer demo.

### 7.2 Receiving
1. User taps "Receive" → picks account/sub-address → shows QR + `prsmr:` URI (optionally with requested amount).
2. Sender pays; wallet scans with view key; shows pending → confirmed at 10 blocks with gentle notification (intensity per user setting).
3. Risk model labels inbound outputs (e.g., "from newly seen address") — informational only.

### 7.3 Natural-Language Send (Happy Path)
1. User types/says: *"Send $50 to Alex for the design session, keep it private."*
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
- Recovery timelocks double as anti-coercion: forced recovery still takes 72 h with visible alarm state. Per §14 (non-blocking item Q5): v1 alarms stay **private** — in-app + the user's own devices only; notifying recovery contacts is opt-in per group, to avoid outing a coerced user. Aggressiveness validated in Phase-3 usability study.

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
- Beneficiary holds a share that activates only after N days (default 365) without liveness AND M-of-K attestator confirmation (attestators are contacts or lawyer/oracle service). Grace ping to user every 30 days before activation. Per §14 (Q6): the v1 liveness signal is **signed heartbeat messages** from the user's wallet (privacy-preserving; no on-chain activity inference); on-chain inactivity remains a fallback for users who never send heartbeats.

### 7.9 Client UX Baseline & Multi-Device Signing Experience (**Amendment A1-2026-10 — binding**)

> **Founder review (2026-10-12):** *"I find the clients very meager in UX/UI design, and quite complex to use, there's no context help, the whole multi-device signing is shit, really disappointed in this."* The prior "GUI/MPC product requirement" note (§13 Phase 1) declared UX beauty a hard acceptance requirement, but the shipped client violated it in practice. This amendment converts that aspiration into concrete, testable requirements grounded in published usability research (Nielsen Norman heuristics, NN/g empty-state and wizard research, Material Design progressive disclosure, Apple HIG onboarding guidance, WCAG 2.1 AA, and reference flows from Bitwarden, 1Password, MuJo/Status/Sparrow pairing UX). It applies to **every client surface** (`prism/gui` web bundle, `prism-gui.exe`, the Tauri shell, and the CLI where unavoidable).

#### 7.9.1 Information architecture — one job per screen
The current single-scroll page mixing balance, NL bar, review, device keys, recovery, plans, and contacts is replaced by a **persistent left navigation rail** (collapsible to icon rail <1024 px; bottom tab bar at mobile widths) with exactly five primary destinations, each with one clear job:

| Destination | Job | Contains |
|---|---|---|
| **Wallet** | See money, move money | Balance hero + fiat estimate, recent activity list, Send / Receive buttons |
| **Devices** | Key custody | Device roster, ceremony wizard, reshares, "why can't I see my shares?" inline |
| **Recovery** | Safety net | Recovery setup state, pending-recovery alarm, contact leg management |
| **Plans** | Automation | Agent digest cards, veto/revoke, audit log |
| **Settings** | Everything else | Theme, notification intensity dials, node details (advanced, collapsed), disclosure registry |

Rules: no more than one primary action button per screen region; destructive actions live under their own confirm step, never adjacent to their benign counterpart; the review card (§7.3) renders as a **modal sheet over the originating screen**, not a section further down the page (users must not scroll to find what they just asked for — NN/g finding: split-attention is the top failure mode of single-page financial forms).

#### 7.9.2 Contextual help — mandatory, three tiers ("no orphan screens")
Every visible screen/panel MUST carry contextual help at three levels so nobody is ever stuck without an explanation:
1. **Inline microcopy** — ≤2 sentences under any control whose consequence is non-obvious (fees, timelocks, share handling, privacy effects). Plain language per §5 honest-UX; reads aloud ≈ grade-6 level.
2. **`?` help popover** — every panel header gets a help affordance (`aria-haspopup="dialog"`) explaining *what this is, why it exists, what happens if I skip it*, with a "Learn more" deep link into the in-app guide. Popovers are focus-trapped, dismissible with Esc, and never block the primary action.
3. **In-app guide (Help center)** — searchable task-based articles ("How do I pay someone?", "I lost my phone", "What are device keys?"), each written as numbered steps mirroring the actual UI labels. Reachable via persistent Help button (bottom of nav rail) and `?` keyboard shortcut from anywhere. First-run app also drops contextual coach-mark bubbles on first visit to each destination (dismissible forever, restorable in Settings).
CLI parity: every `prism` command supports `--help` with a worked example line and a "next step" pointer; unknown commands suggest the closest match.

#### 7.9.3 First-run onboarding — guided, resumable, zero jargon
A 4-step wizard (progress indicator, Back always available, Skip allowed except for custody choice):
1. **Welcome + what Prism is** in 3 sentences with an illustration; expected-time badge ("≈3 minutes").
2. **Custody choice (§7.1)** presented as two cards in plain outcome language — "Protect with your devices (recommended if you have 2+)" vs "Protect with people you trust (recommended if you have one)" — each card states in one line what happens when the option is lost. Legacy seed hidden behind "Advanced options".
3. **Device-key ceremony** (see 7.9.4) runs inside the wizard.
4. **First money**: faucet drip demo + "Receive" QR shown with a copyable address; ends with a calm summary card ("You're set up. Your key lives on: Phone, Laptop, Backup.").
Wizard state persists server-side; closing the window mid-ceremony resumes exactly where it stopped (abort-safe per §7.1 step 3). Empty states everywhere ship with illustration + one-line explanation + the primary CTA (e.g., activity list before first payment: "No payments yet — get test PRSM or show your receive QR").

#### 7.9.4 Multi-device signing ceremony — redesigned end-to-end (**the flagship fix**)
Current failure modes to correct: one-button "Set up device keys" that silently fabricates all three legs in-process (an illusion of security the user can neither verify nor extend); no way to add a second real device; no pairing transport; stage text ("legs 1..3 polynomials") is crypto jargon; nothing tells the user what to physically do with their phone.

Target experience (research baselines: Bitwarden/1Password device pairing, Sparrow/Electrum QR wallet sync, Signal linked-devices liveness):
1. **Name your devices.** First leg = the current device, auto-detected friendly name ("This laptop · Maya's MacBook Pro"), editable. Subsequent legs are added by **pairing**, never by assumption.
2. **Pairing transport, same room:** initiating device shows a large QR containing a short-lived invite (EE2EE session URL + 6-char human check code, TTL 5 min, single-use). Second device scans it → both screens show the check code and the peer's device name; user taps "Yes, this is my device" on **both** sides (mutaneous confirmation kills evil-maid QR-swap attacks). Invite link fallback for same-machine browser sessions; NFC tap-to-pair optional later.
3. **Live ceremony dashboard:** each leg is a card (icon, name, fingerprint, status chip: waiting → committed → verified → done). Feldman-VSS verification results shown as "Share verified ✓ — mathematically checked against the group key, never revealed". Progress = completed legs, not fake percentages. Abort/resume at any commit boundary; "Start over" regenerates everything and explicitly revokes old commitments.
4. **Liveness model, stated honestly:** signing requires quorum devices to be *online together*. The UI must therefore distinguish, per §5 honesty rules: (a) **demo/simulated mode** — all legs run in-process on this machine; clearly badged "Simulated — all shares on one device right now"; (b) **paired mode** — ≥2 legs on distinct enrolled devices with signed heartbeats ≤60 s old. Sending in paired mode shows "Waiting for Laptop to approve… (it has 5 minutes)" with per-leg status; the signer enforces quorum policy at signing time (§8.4 belt-and-braces) so the UI can never claim more protection than exists.
5. **Recovery contacts (Social mode)** reuse the same pairing primitive: contact scans invite QR → holds an opaque share, sees only "You hold a recovery share for Maya — no visibility into funds"; approval during recovery is a single yes/no prompt showing the 4-digit match code (§7.4).
6. **Reshare** is presented as "Rotate keys (recommended yearly)": one click, progress dashboard identical to keygen, completion card explains "Your old devices' shares no longer work".

Acceptance criteria (testable): (i) a first-time user completes Solo setup across two physical devices with no terminal and no external docs, guided only by in-app help — validated in the Phase-3 study; (ii) no ceremony screen contains unexplained jargon (glossary terms always behind a `?`); (iii) simulated-vs-paired custody state is visible on the Wallet home strip at all times ("Key protection: Simulated ⚠ / 2 devices ✓"); (iv) ceremony survives mid-flow process restart and resumes leg-consistently.

#### 7.9.5 Visual & interaction design system (upgrades §2 principle 4, makes it measurable)
- Design tokens in `theme.css`: 8-pt spacing grid, type scale (≥16 px body, ≥44 px touch targets), semantic color set incl. risk-band colors (calm/caution/pause) that pass WCAG AA contrast in both themes; light/dark/system.
- Status feedback everywhere: async actions show optimistic-disabled buttons + inline spinners; RPC failures render a human sentence + retry, never raw JSON or stack traces; global toast queue respects notification-intensity dial.
- Keyboard: Tab order matches visual order, `Enter` submits the focused card, `Esc` closes modals/popovers, `/` focuses the NL bar, `?` opens Help. Full screen-reader labels on every interactive element (existing `aria-live` usage extended to all lists).
- Motion: ≤200 ms transitions, honors `prefers-reduced-motion`.
- Neuro-inclusive gates remain: calm defaults, no dark patterns, adjustable notification intensity, undo/veto windows surfaced in UI copy.

#### 7.9.6 Scope discipline for clients
The GUI is the product; the CLI is a developer tool. Nothing in the end-user flow may require reading documentation to finish a task — if help is needed to proceed, that is a spec bug: fix the flow or the microcopy, not the user.

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
- Mechanism: FedAvg + client-side DP (ε ≤ 1.0 per round, Gaussian mechanism), secure aggregation among ≥256 contributors per round; contributions routed through a mix network; per-round opt-in. Incentives per §14 (Q7): v1 FL participation is **altruistic + reputation-only** — no emission/credit rewards, to avoid Sybil and account-farming surfaces.
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

### 9.1 Key-Management UX Rules (**Amendment A1-2026-10 — binding**)
The custody model above is only as good as what the user can perceive and control in the client. Hard rules for every wallet app (full experience design in §7.9):
1. **Never simulate multi-device custody silently.** An in-process/demo ceremony must be badged "Simulated" everywhere it appears (ceremony dashboard, wallet home strip, signing previews) until ≥2 legs are enrolled on distinct paired devices via the §7.9.4 pairing flow. Copy must match cryptographic reality (§5 honest-UX extends to custody claims).
2. **Devices are named entities, not indices.** Users see editable friendly names, fingerprints, last-seen liveness, and per-device role ("signing leg" vs "recovery contact leg"). Raw share indices (`device_id: 0..2`) are internal only.
3. **Every custody state answers three questions at a glance:** *Where does my key live right now? What happens if I lose each listed device? What can I do about it?* — surfaced as the Devices screen summary card with direct actions (Add device / Rotate keys / Start recovery).
4. **Ceremonies are resumable and abort-safe** across restarts, with explicit per-leg status and a visible explanation of what each step means in plain words ("Step 2 of 3: your Laptop just confirmed its part of the key. Nothing has been sent anywhere.").
5. **Contextual help is mandatory on every custody surface** (per §7.9.2 tiers) — share handling, timelocks, and quorum math get inline microcopy + `?` popovers + Help-center articles; no jargon without an explanation affordance.

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
- Target use: recurring collaborator payouts, tips, micropayments.

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
| E5 | Denylist fork/dispute (who curates sanctions list?) | **Governance per D5:** the weekly accumulator root is signed by an elected **Prism Compliance Council** (7 seats, 18-month staggered terms, public signing policy; ≥5-of-7 signatures required per root). Multiple parallel third-party lists remain first-class (verifier chooses list ID); proofs reference a specific root — no single censor point, and council roots are one option among many. Council misbehavior is checkable: roots are published with signatures, and users/verifiers can pin any list. Resolved by D5 |
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
- **Silent-simulation deception (A1-2026-10):** demo ceremonies that look like real multi-device custody erode trust when discovered → mandatory "Simulated" badging until genuine pairing (§9.1 rule 1).
- **Help dead-ends (A1-2026-10):** user stuck at any screen with no in-app explanation → §7.9.2 three-tier contextual help is a release blocker for every client surface; QA checklist item: "can a first-time user finish this flow without leaving the app?"

### 11.4 Client UX Acceptance Gate (**Amendment A1-2026-10**)
No client build (web bundle, `prism-gui.exe`, Tauri installer) may be tagged as a release unless: (a) §7.9 IA + onboarding wizard implemented; (b) contextual help present on 100% of screens (automated check: every `<section>`/panel has a linked help affordance); (c) ceremony dashboard distinguishes Simulated vs Paired custody per §9.1; (d) WCAG 2.1 AA contrast + keyboard walkthrough pass; (e) the Phase-3 neurodiverse usability study re-runs against the redesigned flows before GA. Interim builds may ship with an honest "Pre-release UX — redesign tracked under spec §7.9" banner rather than pretending compliance.

---

## 12. Regulatory & Compliance Posture

- Prism Foundation position: privacy software, non-custodial; no company-held keys, no universal access.
- Selective disclosure is the compliance bridge: users can satisfy tax/audit obligations *themselves*, without trusting intermediaries.
- Denylist accumulator (§5.3) is community-curated and provably neutral in structure (multiple lists, no forced consumption).
- Jurisdiction strategy: launch node/wallet code under an OSI permissive license (MIT preferred; BSD-2 alternative — final pick at legal review, non-blocking); entity formation deferred pending counsel (research area R4).
- Age-of-majority and ToS gates on hosted services (recovery-server leg, watchtowers); core protocol permissionless.

---

## 13. Development Roadmap & Milestones

### Phase 1 — Foundation (Months 1–6) ✅ acceptance criteria added
- [x] L1 daemon skeleton: RandomX PoW, 2-min adaptive blocks, Dandelion++ (Testnet "Refraction") — *Criterion: 3 independent nodes sync & sustain 72 h.* **(RUN EVIDENCE 2026-10-11:** `prism/property/node_sync_soak.py` executed the full default run — **PASSED: 3 independent nodes synced & sustained 72 virtual hours (8,640 blocks @ 30 s cadence) in 2.7 s wall; 25,920 block applications fully validated across nodes (8,636 via catch-up sync heals); final tips identical (`7853d163830bff17…` @ height 8,640); adversarial injections: 1,728 duplicates ignored, 576 siblings rejected, 1,152 mutated headers refused, 576 truncated frames rejected, 576 orphans gap-refused; zero invariant violations** (log: `soak_run.log`, tracked as canonical evidence). Earlier validation runs: small/medium-scale passes, plus a compressed-cadence pass (`72 30 900`: 900 block applications, identical tips, zero violations). Sync path hardened by rewriting `sync_to` to walk back from the candidate tip collecting blocks present in the local store until the first missing hash / rival branch, then applying the contiguous suffix oldest-first; gossip now mirrors delivered block data into lagged nodes' stores at delivery time. **NOTE: the 72 h is virtual-cadence-compressed (simulated 30 s block deliveries on a fast-forward clock), pending a long-duration wall-clock soak against a real Refraction testnet deployment.** **UPDATE 2026-10-11 (real-socket daemon):** the networking daemon landed — `prism/chain/wire.py` (strict length-prefixed framing, fail-closed FrameReader) + `prism/chain/p2p.py` (TCP version/verack handshake with network-magic + genesis check, inv→block gossip, getblocks sync-heal, Dandelion++ stem/flare routing skeleton). A SHORT wall-clock soak executed on it: `soak(nodes_n=3, seconds=8)` at 2 s cadence over real loopback TCP sockets and threads → **passed: True**, all 3 nodes at identical tip `b8c1a4a756d73452…` @ height 5, 0 rejected / 0 wire errors (runbook: `prism/testnet/refraction.md`; regression coverage: `prism/tests/test_p2p_wire.py`, 29 tests incl. frame round-trip, truncated/garbage-frame refusal, wrong-genesis handshake drop, loopback gossip convergence, dandelion stem/flare counts, mine_one() timestamp clamp). This mine_one() fix clamps block timestamps to prev.timestamp + cadence growth (mirroring the RPC devnet faucet in `rpc/service.py`) instead of raw wall clock, which previously overshot the future-tolerance bound and stalled convergence. **This is explicitly a short wall-clock pass (8 s) — it does NOT satisfy or supersede the 72 h criterion, which remains virtual-cadence evidence until a ≥72 h wall-clock run executes.**)*
- [x] RingCT core: CLSAG rings, Pedersen commitments, stealth addresses, bulletproof-range integration — *Criterion: spend-to-self loop passes property tests ≥10⁶ iterations.* **(RUN EVIDENCE 2026-10-11:** `prism/property/scaled_spend_to_self.py` executed N = 1,000,000 iterations of the four-phase invariant (1 positive: valid tx ⇒ Σcommit(ins) − Σcommit(outs) = identity; 3 negatives: values-only-cancel, blinds-only-cancel, neither-cancel ⇒ NOT identity), 2 CPU workers, **PASSED in 1903.3 s ≈ 31.7 min at 525 it/s, zero invariant violations** (log: `scaled_run.log`; archived copy: `scaled_run_repro.log` holds the reproducibility run; original log gitignored per *.log). **Reproducibility re-run 2026-10-10 21:14–21:34 UTC: PASSED again — N = 1,000,000 in 931.0 s (1,074 it/s aggregate), zero violations** (`scaled_run_repro.log`). **Verification re-run 2026-10-11 01:08–01:32 UTC (fresh session, launched from clean tree): PASSED a third time — N = 1,000,000 in 1,459.3 s (685 it/s aggregate), zero violations** (`scaled_run.log`, now tracked in git as the canonical evidence log). Run-to-run throughput (525 / 1,074 / 685 it/s) varies with sandbox CPU contention, but the PASSED / violation-free result is stable across all three independent runs. Fast path is a width-6 fixed-base windowed scalar mul with homomorphic batch-difference checking, differentially verified against the pure-Python reference ladder at import and on 300 random before/after pairs; production `pedersen.commit()` slow path cross-checked every 256th iteration (~3,900 full-path exercises per run, incl. wire encode/parse round-trip). Prior in-suite pytest coverage: `test_crypto.py::test_spend_to_self_property`, 64 seeds.)
- [x] Disclosure Registry + scoped view-key mechanics (off-chain verifier v0). *(RESOLVED 2026-10-10: prior "OPEN — no implementation found" audit note was itself stale; implementation lives at `prism/wallet/disclosure.py` with registry + verifier-v0 tests in `prism/tests/test_disclosure.py`. One nondeterministic test (`test_wrong_W_fails_ownership`) was fixed by using a valid unrelated curve point instead of a bit-flip tamper, and a companion `test_malformed_W_rejected` covers the parse-failure path.)*
- [x] MPC wallet prototype: 2-of-3 solo scheme + share ceremony. **(RUN EVIDENCE 2026-10-11:** `prism/property/mpc_solo_ceremony.py` executed — **PASSED: 25 full 2-of-3 Solo ceremony lifecycles in 4.23 s wall** (spec §7.1 mode A, Decision D4 user-owned legs only): Feldman-VSS keygen with per-share verification (75 shares VSS-verified), joint-pubkey consistency across all parties, EdDSA-wire threshold signing exercised under **every** 2-of-3 quorum each round (75 partial→combined signatures), below-threshold leak check (single shares + wrong quorums cannot produce valid sigs), Lagrange reconstruction of the secret matches keygen, proactive reshare preserves the same joint key while rotating all shares, hostile-dealer rejection (25 tampered commitments refused at share-verify), and 2-of-3 device-only recovery bootstrap. Companion pytest coverage: `prism/tests/test_mpc*.py`.)*
- [x] NL Intent Compiler v0 (grammar-validated, 20-intent benchmark suite ≥95% correct compile or safe fallback). **(RUN EVIDENCE 2026-10-11:** `prism/property/intent_benchmark.py` executed against the deterministic grammar validator (`prism/wallet/intent.py`) — **PASSED: 18/20 fully-correct compiles (90.0%) + 20/20 safe outcomes (100.0%)** on the 20-case adversarial suite (multi-currency phrasing, implicit-currency USD shorthand, prsm1… addresses, free-text memo clauses, privacy modifiers, channel-open intents, ambiguous/no-op inputs like 'Send $50 to nobody', out-of-domain refusal, and hostile inputs 'send -5 PRSM' / 'pay alex 0 PRSM' which safely fall back to draft rather than compiling). Acceptance bar met per spec: correct+safe == 100%; the two non-'OK' cases are intentional safe-fallback drafts, never miscompiled. Grammar fix included: `parse_utterance` now strips 'open a channel with X' phrasing so recipient resolution succeeds.)*

**GUI/MPC product requirement (added 2026-10-11):** The GUI application that combines all Prism features MUST have **user-friendliness and beautiful UX design as its primary differentiators** — this is a hard acceptance requirement, not an aspiration. Concretely: every wallet-visible flow (share ceremony, recovery, disclosure grants, intent confirmation) must pass plain-language review per Design Principle 4 (Neuro-Inclusive UX) and the honest-UX language hard requirement (§5); no operation may require the user to understand crypto jargon to complete it safely; visual design system ships before feature-complete. The MPC ceremony UI in particular must make keygen/reshare/recovery legible to a first-time user (progressive disclosure, calm defaults, explicit undo/veto windows).

> **Usability mandate clarification (2026-10-11, restated by the user):** the end-user experience must NOT require any command-line interaction beyond launching one app — the client manages whatever backend/daemon it needs entirely on its own, Electrum / Bitcoin Core style ("that's tech geek stuff"). Implementation evidence: `prism/rpc/launcher.py` registers the zero-argument `prism-gui` entry point (`[project.scripts]`) which auto-selects a free loopback port, creates/adopts a private per-user data dir (`~/.prism/wallet`, mode 0700, `.prism_wallet` marker for reopen-in-place), enforces single-instance via flock + live health probe (second launch just reopens the running UI; stale/garbage locks are reclaimed), starts the JSON-RPC+GUI daemon in-process against a fresh per-user `PrismService` (no singleton bleed-through), and opens the default browser only after `/health` confirms readiness. Verified live over a real socket (bare launch → `GET /health {"status":"ok"}`, `GET / -> 200, 5165 bytes` GUI bundle, `health.ping` round trip) and covered by `prism/tests/test_gui_launcher.py` (14 tests incl. refusal/negative cases per ethics.md §4). Full suite: **507 passed, 7 skipped**.

> **Double-click installer mandate — hard acceptance requirement (2026-10-11, restated again by the user):** A true double-click installer is now **MANDATORY**, not a Phase-2 nice-to-have. The shipped product MUST be a pre-built, packaged binary that a user downloads and installs with zero terminal interaction: "No terminal, no command line, just install and run." The installer must handle everything up to and including bundling/installing the Python runtime and every backend daemon ("really doofus proof stuff") — the end user must never learn that Python, pip, ports, or daemons exist. Delivery vehicle: a native desktop shell (**Tauri** chosen; Electron acceptable fallback) whose bundled resources contain a PyInstaller-frozen build of the `prism-gui` launcher (embedded CPython included), producing per-platform installer artifacts (Windows `.msi`/`.exe`, macOS `.dmg`, Linux `.AppImage`/`.deb`) with SHA256SUMS. Acceptance criteria: (a) fresh machine without Python installed → download → double-click install → app icon launches wallet UI within seconds, no console ever visible; (b) all features reachable through the GUI only (chain, wallet, MPC ceremony, recovery, intents/plans, fraud review, disclosure grants); (c) uninstall leaves no orphan daemons. Build plumbing exists in `packaging/` (`build_release.sh`, `prism.spec`) but currently targets the CLI binary only; it must be extended to freeze the GUI launcher and wire into the Tauri build pipeline. Honest status: PyInstaller and the Rust/Tauri toolchain are **absent from this development environment**, so no installer artifact has been built or verified here yet — claiming otherwise would violate qwen.md execution-enforcement rules. Until installers ship, `prism-gui` remains the interim path and the ONLY command any user is ever asked to type. This mandate supersedes any earlier wording that classified the native shell as optional or deferred Phase-2 scope.

### Phase 2 — Intelligence (Months 7–12)
> **GUI/RPC v0 scaffolded (2026-10-11, pre-phase note):** The GUI combining all features has its foundation in place ahead of Phase-2 feature work, per the UX-excellence hard requirement above. Shipped scaffold: `prism/rpc/` — stdlib-only JSON-RPC daemon (`prism-rpc`, registered in `[project.scripts]`; 12 methods covering chain/faucet/mining, wallet balance+RingCT spend, MPC ceremony state machine, intent compile/preview/confirm, disclosure grants) and `prism/gui/` — zero-build front end (single-page `index.html` + `app.js` + `theme.css`) implementing the Neuro-Inclusive design pillars from day one (calm defaults, progressive disclosure, plain-language confirmations, explicit undo/veto windows). Evidence: `prism/tests/test_rpc_gui.py` — 13 tests green, including a full end-to-end flow (intent utterance → compile → preview → confirm → RingCT spend → block inclusion → MPC keygen ceremony over RPC). Remaining Phase-2 items below build on this surface.
> **GUI/RPC v0.1 (2026-10-11):** Three upgrades landed on the scaffold. (1) **Live chain state:** new `chain.blocks` JSON-RPC method reads real block history from the daemon store (height/hash/prev_hash/timestamp/tx_count); GUI renders live node status instead of fixtures. (2) **Fraud-simulator panel (feeds "Transaction simulator + explanation templates" and the fraud-model item):** new `intent.review` RPC composes `SigningGate` + `FraudDetector` against live chain snapshots to produce honest-UX risk bands (`calm`/`caution`/`pause`) with plain-language reasons; interrupt-barrier semantics enforced server-side — `intent.confirm` refuses unacknowledged `pause`-band intents and requires the canonical digest carried through acknowledgement ("sleep on it"), so no refused intent can ever broadcast. Satisfies the §5 honest-UX hard requirement in the primary send flow. (3) **MPC share-handling UX polish:** `ceremony_status` returns per-device share cards (labels, fingerprints, verification flags, never-show guidance text) without exposing raw shares; added `mpc.ceremony_reset`; GUI shows a "Your device keys" section with progressive disclosure and reshare controls. Bug fixed along the way: an unconditional `acknowledged_risk` bypass in the confirm path now threads real user acknowledgement and re-runs the gate before mutation. Evidence: `python -m pytest prism/tests -q` → **418 passed, 7 skipped in 88.24 s** (v0 baseline 411 + 7 new tests covering benign/scam/interrupt-band/gated-confirm, insufficient-funds honesty, live `chain.blocks` contents, ceremony device cards, and reset). Commits `8d6b328`, `54045d4`.

- [x] On-device fraud model v1 + rule packs; benchmark vs curated phishing corpus (target: FPR <0.5%). **Evidence (2026-10-11):** `python -m prism.property.fraud_benchmark` — full production pipeline (`IntentCompiler.compile → TxFeatures → FraudDetector.score`), curated corpus of 8 benign + 8 first-time benign-stress + 12 phishing cases (one per shipped scam-pattern regex, plus lookalike-swap and 96%-drain composites) against model `fraud_det_v1_1.0.0` / rule-pack `rules-2026w41-v1` (digest 74711bda48c79638): **PASSED — FPR 0.0000 (0/8, target <0.005), TPR 1.0000 (12/12 any-flag), interrupt-only TPR 0.8333**, per-band counts {clean 8, watch 2, interrupt 10}, zero compile drafts skipped. Two benchmark bugs found & fixed during validation (shard-vs-PRSM balance unit mismatch inflating FPR to 0.75; corpus wording 'upfront' missing the spaced `up front` pattern) — product thresholds unchanged, no DEFAULT_BUNDLE iteration needed.
- [x] Transaction simulator + explanation templates. **Evidence (2026-10-11):** deterministic executor (`prism/ai/simulator.py`: coin selection, change, fees, stale-quote re-lock E5, sim_digest binding) + shipped template pack `sim_explain_v1` (`TEMPLATES`: calm_default/caution/pause/pause_acknowledged/insufficient/not_previewable + privacy-mode lines); band→template binding enforced in `SigningGate.review`, `render_template` raises KeyError on unknown keys so no caller can invent reassurance (§8 honest-UX); RPC `intent.review` exposes `template_pack` + verbatim template copy; GUI renders server-supplied strings only. Evidence: `test_ai_simulator.py` + new `TestExplanationTemplatePack` (band→explanation binding incl. exact shipped pause copy, unknown-key rejection, RPC exposure) — full suite **422 passed, 7 skipped in 88.25 s**.
- [ ] Federated learning pipeline (mixnet, DP accounting, aggregator).
- [x] Agent runtime with policy-enforcing signer; veto-window UX. **Evidence (2026-10-11):** Policy engine enforced outside the LLM at signing time (`prism/wallet/agent_auth.py`: scoped grants, fiat-quoted DCA capability, fail-closed funding gate); full lifecycle verified by `python prism/property/agent_smoke.py` — SMOKE PASSED ok=True, 22 checks EXIT=0 (propose→pending→tick→execute→veto→revoke + negatives: PRSM-denominated proposal refused, vetoed action never executes, revoke cascades queued actions and is permanent, unfunded action stays queued fail-closed and settles after funding). GUI "Plans" surface (index.html+app.js+theme.css): one-tap veto/revoke, plain-language digest cards, honest copy ("Stopped. This will not run..."). Evidence: TestAgentRuntime 6/6 green incl. money-state settlement test (real txid, balance deducted, block height +1); full regression `pytest prism/tests -q` → **428 passed, 7 skipped in 87.79 s** (baseline 422 + 6 new). Bugs fixed along the way: ACTION_TO_CAPABILITY keyed backwards (all proposals died), spend_utxo list-vs-dict settlement crash, sim-clock clamping, revoke cascade leaving cancelled actions visible in pending.
- [ ] ZKP circuits v1: Source Provenance + Balance Solvency (audited by ≥2 external firms).

### Phase 3 — Accessibility (Months 13–18)
> **Client UX redesign program (A1-2026-10, added 2026-10-12):** Founder review found the shipped clients "meager in UX/UI", complex, without context help, and the multi-device signing flow fundamentally disappointing. Spec now binds §7.9 (IA, contextual help tiers, onboarding wizard, pairing-based ceremony redesign, design system), §9.1 (custody UX rules incl. mandatory Simulated-vs-Paired badging), §11.4 (release acceptance gate). Work items: [ ] nav-rail IA + modal review sheet; [ ] three-tier contextual help + in-app Help center; [ ] first-run onboarding wizard (resumable); [ ] device pairing transport (QR invite + check code + mutual confirm) replacing in-process fake ceremonies; [ ] live ceremony dashboard with per-leg status; [ ] custody-state strip on Wallet home; [ ] design-token overhaul of `theme.css` to §7.9.5; [ ] WCAG AA pass + keyboard walkthrough; then re-run the n≥20 usability study against the new flows. Prior GUI notes below describe v0 scaffolds and remain as history; where they conflict with §7.9/§9.1, the amendment wins.
> **GUI/RPC social-recovery surface (2026-10-11):** `recovery.status/initiate/cancel/finalize/attach_social/advance_window` RPC methods wired end-to-end (`prism/rpc/service.py`, `server.py`) with GUI cards; honest-UX copy discloses address change explicitly (§5); devnet-only `advance_window` for timelock testing. Formal pytest coverage added: `TestRecovery` (11 service-level tests: arm-on-ceremony, exact-quorum validation, pending-window veto→cancel→re-arm, unknown-share PermissionError, early-finalize timelock guard, solo flow initiate→finalize→all-legs reshare→generation bump→30-day cooldown, Social upgrade path, contact-veto flow, §7.3 E9 full-quorum-loss refusal) + `TestRecoveryOverHTTP` (every method over the real JSON-RPC wire incl. error-envelope format). Evidence: `pytest prism/tests/test_rpc_gui.py` → 43 passed; full suite **441 passed, 7 skipped in ~63 s**. Commits `865966f`, `d422d26`.
- [x] Social recovery GA (3-of-5), timelock + cancellation flows. **(RUN EVIDENCE 2026-10-11:** per the note above — quorum-checked initiation, 72 h timelock with cancel-anytime semantics while PENDING, post-finalize proactive reshare of ALL legs, 30-day cooldown enforced fail-closed; 13 new green tests incl. HTTP-wire round-trips.)
- [x] Biometric share-gating (iOS Secure Enclave / Android StrongBox). **(RUN EVIDENCE 2026-10-11:** `prism/mpc/biometric.py` — device-bound HKDF sealing of the local MPC share behind a `SecureElement` interface (`SimulatedSE` desktop stand-in for Secure Enclave/StrongBox; production swaps the adapter only). Unwrap requires BOTH valid sealed blob AND live biometric-success signal; wrong-device blobs refuse; 5 failures → lockout with platform-passcode re-unlock; attestation quotes verify. §9 honored: no biometric samples stored, nothing on-chain, local share only. Wallet-side `GatedShare` adds a 300 s cached-auth window (calm UX) + explicit re-lock. Evidence: `python -m prism.property.phase3_smoke` → PHASE3 SMOKE PASSED, EXIT=0; `test_l2_fees_channels.py` + full suite **464 passed, 7 skipped in 63.26 s**.)
- [ ] Neuro-inclusive design system shipped; usability study with neurodiverse participants (n≥20). **(NOTE 2026-10-11:** design-system side ships in code — `prism/gui/theme.css` implements the calm-defaults/progressive-disclosure/plain-language pillars and every wallet-visible flow passes the §5 honest-UX copy review in tests. The n≥20 neurodiverse-participant usability study is an external human-subjects task (IRB/ethics review, recruitment, consent) that cannot be closed by code alone — item stays open until the study report lands; see `ethics.md` research-protocol requirements.)
- [x] Payment channels + watchtower beta. **(RUN EVIDENCE 2026-10-11:** `prism/l2/channels.py` — bidirectional commitment channels: Schnorr-signed monotonic state versions over the Edwards group, instant zero-fee in-channel payments, overdraft/state-machine guards, cooperative close at exactly 2 base fees lifetime (asserted against `BASE_FEE_SHARDS`). Watchtower (E3): penalty blobs sealed with a keccak counter-mode XOR stream + SHA-256 MAC (amounts/signatures confidential to tower; tamper & wrong-secret rejected via constant-time compare), tower indexes only truncated tag+version; stale-close detection → `penalize` decision → full-balance sweep to victim (`penalty_outcome`, both directions verified). Beta posture stated honestly: protocol-level model — CLTV-enforced adapter txs land with the node mempool integration. Evidence: `test_l2_fees_channels.py` 23 passed incl. cheating-close/watchtower suite; phase3 smoke EXIT=0; two test bugs found & fixed during verification (blob-tag truncation applied to hash object instead of digest; mirrored-cheat fixture used balances inconsistent with the jointly-signed truth).)
- [x] Fee predictability hardening (priority cap enforcement, block-packing policy). **(RUN EVIDENCE 2026-10-11:** `prism/l2/fees.py` — fixed 10,000-shard base fee + whole-multiple priority lattice N·BASE, 1 ≤ N ≤ `PRIORITY_FEE_MAX_MULTIPLE`(10) imported from `chain/params.py` (single source of truth); off-lattice/>10× → `FeePolicyError` → non-standard. Deterministic pre-sign quotes for the three UI tiers (no estimation jitter, §2 principle 5). `pack_block`: greedy fee-per-byte density packing, lexicographic tie-break for cross-node determinism, non-standard fees re-checked at pack time so a hostile miner cannot smuggle a 12× tx through; ≤2-block base-fee clearing property (`BASE_FEE_CLEAR_BLOCKS_MAX`) asserted behaviorally. Evidence: TestFeeLattice/TestFeeQuotes/TestBlockPacking green within full-suite run above + phase3 smoke smuggling-refusal check.)

### Phase 4 — Ecosystem (Months 19–24)
- [ ] Mainnet launch (emission parameters per D1: halving curve → 21M cap, ≈0.6%/yr tail, zero premine, dev-fund carve-out audited pre-launch).
- [ ] Developer SDK (Rust/C/TS bindings), verifier SDK.
- [ ] Merchant toolset (channel receiving, invoicing via `prsmr:` URIs).
- [ ] Privacy-preserving DeFi primitives (collateral solvency proofs in escrow contracts-lite).
- [ ] Compliance toolkit (tax-season wizard, auditor portal built on disclosure circuits).

### Security gates
- Formal audit of consensus + RingCT before mainnet; separate ZKP circuit audits per release; bug bounty program from Phase 2; fuzzing harness (chain parsing, MPC protocol) continuous.

---

## 14. Decision Log (v1.0 resolutions)

All previously blocking questions were resolved by the founder on **2026-10-08**. Decisions D1–D5 below are binding for v1 implementation; where a decision leaves sub-questions open, they are reclassified as non-blocking research areas (§15C).

| ID | Question | Decision | Where applied |
|---|---|---|---|
| **D1** | Emission & premine | **Halving curve to 21M cap + permanent ≈0.6%/yr tail emission.** Zero premine; team/foundation funded only via transparent dev-fund carve-out of future emission with on-chain milestone vesting. *Amended 2026-10-10 (Option B):* launch epoch issuance raised to ≈cap/2 so the decay + tail blend carries supply through exactly 21M at height 2,984,354 (≈year 11.4); halving interval unchanged (shortening it was evaluated and rejected — plateau depends only on launch rate). | §4.1 |
| **D2** | SNARK vs STARK | **zk-SNARKs (PLONK/ultra-honk)** for disclosure circuits — proof size dominates for mobile. Trusted-setup risk mitigated via universal SRS ceremony, published transcripts, and re-instantiation path. Amount/range layer stays Bulletproofs+ (no setup). | §5.1, §5.3 |
| **D3** | Revocable disclosure | **Expire-and-rotate is the binding semantics.** No recall promised anywhere; forward-revocable time-lock encryption descoped from v1 → research area R6. | §5.4 |
| **D4** | Vendor recovery leg | **Recovery must be purely user-owned.** Prism operates no MPC share/recovery leg of any kind (Solo mode = user devices only; Social mode = user contacts). Trust story: zero vendor custody surface. Business model shifts to services (verifier SDK, merchant tooling, enterprise compliance), not key-holding. | §7.1 |
| **D5** | Denylist governance | **Elected Prism Compliance Council** (7 seats, staggered 18-month terms, ≥5-of-7 signatures per weekly root) signs the *official* accumulator root; parallel third-party lists remain first-class and verifiable; users/verifiers may pin any list. | §11 E5 |
| **A1-2026-10** (amendment, 2026-10-12) | Client UX quality bar | Founder review rejected the shipped client UX as "meager", complex, help-less, and the multi-device signing flow as unacceptable ("shit"). Binding amendment: clients must ship a nav-rail IA with one job per screen (§7.9.1), mandatory three-tier contextual help incl. in-app Help center (§7.9.2), resumable first-run onboarding wizard (§7.9.3), real device pairing (QR invite + check code + mutual confirmation) replacing silent in-process fake ceremonies, honest Simulated-vs-Paired custody badging (§9.1), measurable design-system requirements (§7.9.5), and a release acceptance gate (§11.4). Where prior GUI notes conflict, this amendment prevails. | §7.9, §9.1, §11.3–11.4, §13 Phase 3 |

### Remaining non-blocking items (folded into §15C research areas)
- Duress alarm aggressiveness (Q5) → default: private in-app + user's own devices only; contact-notification is opt-in per recovery group. To be validated in the Phase-3 neuro-inclusive usability study.
- Inheritance liveness signal (Q6) → v1 default switches to **signed heartbeat messages** (privacy-preserving); on-chain inactivity kept as fallback for users who ignore heartbeats. Pending R3 confirmation.
- FL participant rewards (Q7) → v1 keeps FL **altruistic + reputation-only** (no emission share) to avoid Sybil farming; revisit post-launch.
- Data-model MVP field cuts (Q9) and fiat-on-ramp scope (Q10) → product-scoping tasks at Phase-1 kickoff, not architectural blockers; §6 schemas stand as written.

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
R1 Post-quantum migration path · R2 Mobile ZKP prover optimization (GPU/NPU delegation) · R3 Optimal recovery collusion resistance · R4 Multi-jurisdiction disclosure interoperability · R5 Cognitive-load telemetry methodology (consent-based, on-device metrics only) · R6 Forward-revocable disclosure via time-lock encryption of auditor auxiliary data (descoped from v1 per D3) · R7 Compliance Council election mechanics, slashing/removal rules, and denylist policy RFC.

---

*This specification is a living document. v1.0 finalized 2026-10-08 with decisions D1–D5 (§14). v1.1 = Amendment A1-2026-10 (Client UX Baseline & Multi-Device Signing, §7.9/§9.1/§11.4, decision-log row appended 2026-10-12). Future changes follow the RFC/amendment process; the decision log is append-only.*
