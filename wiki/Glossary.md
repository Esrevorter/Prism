# Glossary

*Every Prism term, defined for smart people new to this. Links point at the deep-dive pages.*

---

## Money & units

| Term | Definition |
|---|---|
| **PRSM** | The currency's display ticker. 1 PRSM = 100,000,000 shards. |
| **shard** | Smallest unit (1e-8 PRSM, 8 decimals). All amounts in code are integer shards — floats appear only in fiat-quote displays. |
| **emission curve** | The formula producing each block's new coins: halving every 315,360 blocks (~2 years) approaching the cap, then a constant tail. [Monetary Policy Rationale](Monetary-Policy-Rationale.md) |
| **tail emission** | Permanent ≈0.6%/yr inflation after the 21M cap, consensus-fixed, guaranteeing miner revenue forever (Decision D1). |
| **dev-fund carve-out** | A fixed small percentage of each block reward allocated to development funding, released via on-chain milestone vesting. Not a pre-mine. |
| **base fee / priority fee** | Fixed 0.0001 PRSM cost per transaction; optional multiplier up to 10× for faster inclusion. [Fees & Channels](Fees-and-Payment-Channels.md) |

## Privacy machinery

| Term | Definition |
|---|---|
| **RingCT** | *Ring Confidential Transactions* — the umbrella scheme hiding senders (rings), receivers (stealth), and amounts (commitments + range proofs). |
| **Pedersen commitment** | Sealed-envelope number `C = v·H + r·G`: hides value `v` and blinder `r`, while allowing math *on* the sealed value (add/subtract commitments without opening them). [Crypto Deep Dive](Cryptography-Deep-Dive.md) |
| **range proof** | Proof that a committed value lies in `[0, 2⁶⁴)` without revealing it — stops negative-value counterfeiting. Prism uses **Bulletproofs+** (no trusted setup, ~352 bytes). |
| **ring signature** | Signature by "one of these N keys" — proves an input was authorized without saying which of the ring members is real. **CLSAG** is Prism's aggregated, linkable variant. |
| **key image** | Unique public marker generated when a coin is spent; two spends of one coin show one identical image → double-spend rejected. Reveals nothing else. |
| **decoy** | A stranger's output included in your ring to disguise yours. Decoy sampling quality directly determines anonymity. |
| **stealth address** | One-time destination derived by Diffie-Hellman from the recipient's view key — every payment gets a fresh address nobody can link to the published one. |
| **Dandelion++** | Relay protocol: transaction travels a secret stem path before flooding, so observing peers can't reliably learn your IP. |
| **sub-address** | Your wallet's internally-derived receiving addresses (per-platform labels) that all land in one account. [Using the Wallet](Using-the-Wallet.md) |

## Selective disclosure (the Prism Protocol)

| Term | Definition |
|---|---|
| **zero-knowledge proof (ZKP)** | Math convincing a verifier a statement is true while leaking nothing beyond the statement. |
| **zk-SNARK / PLONK** | Small, fast ZKP family needing a one-time *trusted setup*; PLONK's setup is universal across circuits (Decision D2). |
| **zk-STARK** | Setup-free alternative with bigger proofs — evaluated and set aside for mobile proof-size reasons; kept as fallback path. |
| **disclosure circuit** | One of five versioned statements you can prove: Source Provenance, Balance Solvency, Income Attribution, Reserve Attestation, Clean Exit. [Prism Protocol](The-Prism-Protocol.md) |
| **verifier nonce** | Random value binding a proof to *this* verifier session — copies presented elsewhere fail. |
| **expiry** | Consensus-checked timestamp inside a proof; verifiers reject expired ones. Default 90 days. |
| **rotation** | Scheduled scoped-view-key refresh bounding what a leaked key can reveal (nothing post-rotation). |
| **Disclosure Registry** | Your local dashboard of every proof ever generated: scope, recipient, expiry. You always know what you revealed. |
| **anchoring** | Optional recording of a disclosure's Merkle root on-chain for consensus-grade attestations. |
| **denylist accumulator root** | Weekly hash-chained digest of a sanctioned-output list, published in block headers *for reference*; never enforced by consensus. Multiple parallel lists supported (Decision D5). |

## Keys & custody

| Term | Definition |
|---|---|
| **spend key / view key** | Private spend key authorizes outgoing funds; private view key scans incoming. Public halves live in your payment address. |
| **MPC / threshold signing** | Key split into shares (Shamir); any quorum co-signs, fewer-than-quorum learns nothing. GG20/FROST-style protocols. [Recovery Guide](Recovery-and-Inheritance-Guide.md) |
| **key share** | One fragment of your spend authority, held by a device or contact. Opaque alone. |
| **Solo MPC / Social MPC** | 2-of-3 across your own devices / 3-of-5 including up to four contacts. Prism itself holds nothing (Decision D4). |
| **proactive refresh** | Periodic re-splitting of the key into new shares (every 90 days) — stolen old shares decay to worthless. |
| **social recovery** | Quorum-approved regeneration of shares on a new device, gated by the 72-hour cancellable timelock. |
| **timelock (recovery)** | 72 h during which recovered shares can't move funds while warnings broadcast and stale shares retain cancel rights. |
| **dead-man switch** | Inheritance mechanism: beneficiary share activates after 365 days of missed signed heartbeats + attestator quorum, with grace pings. |
| **duress PIN / decoy wallet** | Alternate unlock code opening a plausible low-balance wallet under coercion instead of failing visibly. |
| **panic lock** | Optional freeze on sends above your comfort threshold pending veto window — applies even to manual sends. |
| **heartbeat** | Signed periodic liveness message from your wallet (privacy-preserving absence detection for inheritance). |

## AI layer

| Term | Definition |
|---|---|
| **Intent** | Strict JSON transaction request produced by the NL compiler; must pass deterministic validation before any signing. |
| **NL Intent Compiler** | On-device language model turning plain requests into Intents, falling back to forms on ambiguity. |
| **Transaction Simulator** | Deterministic executor producing the exact preview terms shown before signing — the source of all numbers in UI. |
| **Fraud Detector** | On-device scoring model + rule overlay flagging risky destinations pre-confirm. Local labels only. |
| **agent grant** | Explicit permission record for autopilots: capability, constraints, whitelist, 24 h veto window, expiry. |
| **veto window** | Delay before agent-executed actions run — your standing chance to say no. |
| **hash-chained audit log** | Tamper-evident local journal of every agent action with plain-English explanations. |
| **federated learning (FL)** | Wallets collaboratively improve the fraud model without data leaving devices: DP-clipped updates, secure aggregation, mixnet routing. |
| **differential privacy (DP)** | Mathematical noise guaranteeing any single participant's data barely affects aggregate outputs (ε ≤ 1.0/round here). |

## Network & chain

| Term | Definition |
|---|---|
| **L1 / L2 / L2-lite / L3** | Base chain / on-device intelligence layer / payment channels / wallet-app accessibility layer. [Architecture](Architecture.md) |
| **block / height** | 2-minute bundle of transactions; height counts from genesis. |
| **PoW / RandomX** | Proof-of-Work securing the chain; RandomX is CPU-friendly and ASIC-resistant by design. |
| **adaptive difficulty** | Retarget algorithm keeping ~120 s blocks as hashrate changes (DCR-style, 60-block window). |
| **maturity** | 10-block waiting period before received funds may be spent (protects decoy integrity). |
| **full / pruned / indexer node** | Complete validator / space-saver (can't serve good decoys) / owner-run view-key-scoped index. [Running a Node](Running-a-Node.md) |
| **Refraction** | Phase-1 testnet name (`--network refraction`). |
| **version-bit voting** | Miner signaling for upgrades over 181-block windows; ≥90-day notice policy. |
| **watchtower** | Outsourced channel-state guardian submitting encrypted penalty blobs if a counterparty misbehaves. |
| **channel** | Off-chain payment tunnel: fund once, transact instantly/free, close anytime. |

## Governance & process

| Term | Definition |
|---|---|
| **Decision D1–D5** | Binding founder resolutions (emission, SNARK choice, revocability semantics, user-owned recovery, denylist governance) — [`spec.md` §14](../spec.md). |
| **Compliance Council** | Elected 7-seat body signing the official weekly denylist roots (≥5-of-7); one option among many parallel lists. [Governance](Governance.md) |
| **RFC / amendment process** | Spec changes follow numbered RFCs; decision log is append-only. |
| **research areas R1–R7** | Tracked-but-non-blocking open questions (post-quantum, prover optimization, collusion resistance, jurisdiction interop, telemetry ethics, forward-revocable crypto, council mechanics). |
| **E1–E18** | Numbered edge-case/failure-mode entries in the threat model ([Security Model](Security-Model.md)). |

---

*Missing a term? Request it via a `wiki` issue — this page answers to readers, not to tradition.*
