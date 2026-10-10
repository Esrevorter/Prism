# FAQ

*Short, honest answers. If an answer sounds too good, check the linked deep-dive — every claim here has a source.*

---

## The basics

**What is Prism in one sentence?** Digital money that's private by default, recoverable when you lose a device, and able to prove specific facts about your finances (for taxes, audits) without revealing everything else.

**Why "Prism"?** A prism splits light so you choose which wavelength to show. Same idea: your full financial life stays hidden; you reveal one clean fact at a time. ([Plain English](Prism-in-Plain-English.md))

**Is there a token I can buy right now?** **No.** There is no live network, no sale, no contract address. Zero pre-mine means literally nobody — including the founders — holds early PRSM. Anything selling "PRSM" today is fraudulent. ([Roadmap](Roadmap.md))

**Who's it for?** Designed around independent creative professionals living on irregular global income, but the features generalize to anyone with a paycheck-plus-side-hustle life. ([spec §1](../spec.md))

## Privacy & disclosure

**Is Prism fully anonymous?** No, and neither is anything else. On-chain, sender/receiver/amount are hidden by cryptography; we defend against chain-analysis firms and casual surveillance strongly, and against a global passive adversary *partially* (Dandelion++ etc.). We publish the limits instead of pretending. ([Security Model](Security-Model.md))

**Can the company see my balance?** There is no company key, no universal view key, no admin access — structurally impossible by design (Decision D4). Your data lives on your devices; the chain sees sealed envelopes only. ([How Privacy Works](How-Privacy-Works.md))

**But then how do I do taxes?** Selective disclosure: generate a proof of exactly the statement you need ("2026 incoming = Σ") for exactly the recipient, expiring by default in 90 days. ([Preparing a Tax Disclosure](Preparing-a-Tax-Disclosure.md))

**You advertise "revocable" disclosures — can I really take one back?** Only in the precise sense: disclosures **expire** and scoped keys **rotate**. Once someone verifies and keeps a proof, knowledge can't be un-emitted. We promise limit-and-expire, never recall — and our UI copy is bound to say the same. ([Prism Protocol](The-Prism-Protocol.md) §D3)

**Can governments force compliance?** They can compel *you*, like with any asset. What Prism changes is bargaining power: you can satisfy legitimate reporting with minimal, targeted proofs rather than total ledger surrender. There's no built-in blacklist enforcement; denylist roots inform optional provenance proofs only. ([Governance](Governance.md))

## Money & mining

**Total supply?** 21,000,000 PRSM cap via halving curve, then a permanent ≈0.6%/year tail emission so miner security never collapses. Zero pre-mine; team funded from future emission with on-chain milestone vesting. ([Monetary Policy Rationale](Monetary-Policy-Rationale.md))

**Why tail emission instead of pure Bitcoin-style scarcity?** Because "what pays miners forever?" must have an answer that doesn't depend on fee revenue surviving usage droughts. We chose provable long-run security over absolute-scarcity aesthetics and disclose the ~0.6% dilution tradeoff openly. (Same page.)

**Can I mine it?** When mainnet launches: yes — RandomX is CPU-friendly by design, ASIC farms get no structural advantage. Today `prism mine` is a local demo behind a guardrail that refuses placeholder PoW on mainnet parameters. ([Chain Rules](Chain-Rules-and-Monetary-Policy.md))

**Fees?** Fixed base 0.0001 PRSM (~$0.001); optional priority capped at 10×; channels make micro-payments effectively free. ([Fees & Channels](Fees-and-Payment-Channels.md))

## Wallet & safety

**I lost my phone — am I doomed?** No. Losing one share ≠ losing funds (threshold schemes); full recovery uses your other devices/contacts plus a 72-hour cancellable timelock. Only the deliberately-chosen Legacy Seed mode is unrecoverable if you lose the phrase. ([Recovery Guide](Recovery-and-Inheritance-Guide.md))

**Do my recovery contacts see my money?** Never. Contacts hold opaque numbers that only ever contribute to co-signing ceremonies you initiate. Being a contact costs them one rare approval tap.

**What stops the AI assistant from stealing my funds?** Cryptography, not policy: the model never derives or sees keys/shares, its output must pass a deterministic validator before signing, agent actions execute only within grants enforced *by the signer* outside the model, and irreversible sends require your biometric confirmation. ([AI Layer](AI-Layer-Explained.md))

**What happens if I'm forced to open my wallet?** Duress PIN opens a plausible decoy; forced recovery still burns 72 h in alarm state; optional panic-lock delays above-threshold sends even manually. Honest caveat: these raise attacker cost and buy intervention time — they're not magic. ([Recovery Guide](Recovery-and-Inheritance-Guide.md) · [Security Model](Security-Model.md))

**If something happens to me, can family get the funds?** Optional dead-man switch: beneficiary share activates after 365 days of missed signed heartbeats + attestator quorum, with 30-day grace pings throughout. No custodian needed while you're alive.

## Tech & trust

**Why Python if this is production crypto?** It isn't production — it's the *reference implementation*: the readable, testable definition of every rule, fuzzed and property-tested, from which audited Rust/C node ports will be derived (test vectors shared). Clarity beats cleverness for consensus rules. ([Getting Started as a Developer](Getting-Started-as-a-Developer.md))

**Trusted setup for the SNARK circuits — isn't that dangerous?** Deliberate choice (D2): constant-size proofs matter most for mobile users. Setup risk is mitigated by PLONK's universal SRS (one ceremony covers all v1 circuits), published entropy transcripts, and a re-instantiation path. Amount/range proofs use setup-free Bulletproofs+ precisely to keep the biggest surface ceremony-less. ([Prism Protocol](The-Prism-Protocol.md) · [Crypto Deep Dive](Cryptography-Deep-Dive.md))

**Is the code audited?** Not yet — Phase-1 reference code is pre-audit; formal audits gate mainnet, and the test suite + fuzzers run on every release build meanwhile. ([Roadmap](Roadmap.md) · [Testing & Quality](Testing-&-Quality.md))

**License?** MIT — free to use, fork, and ship. ([LICENSE](../LICENSE))

**Who runs the network?** Anyone who runs a node; miners secure it; upgrades pass version-bit voting with ≥90-day notice. No foundation kill-switch exists. ([Governance](Governance.md))

## Getting involved

**How do I try it today?** Download a release binary and run the CLI tour ([Getting Started as a User](Getting-Started-as-a-User.md)), or clone and run the tests ([Developer](Getting-Started-as-a-Developer.md)).

**How do I help?** See [Contributing](Contributing.md) — docs, tests, review, and port work all welcome; `good-first-issue` label marks entry points.

**Where's the technical truth?** [`spec.md`](../spec.md), decisions D1–D5 in §14, append-only decision log. This wiki explains it; where prose and spec disagree, spec wins.

---

*Anything missing? Open an issue labeled `wiki` — unanswered questions become documentation.*
