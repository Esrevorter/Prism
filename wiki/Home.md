<div align="center">

# 🔹 Prism Wiki

### Clarity on your terms.

*Everything about Prism — from "what is this?" to "how do I audit the code?"*

</div>

---

## 👋 Which door should you walk through?

| You are… | Start here |
|---|---|
| **Curious** — heard about Prism, want the gist in 10 minutes | → [Prism in Plain English](Prism-in-Plain-English.md) |
| **A future user** — want to set up a wallet and get paid | → [Getting Started as a User](Getting-Started-as-a-User.md) |
| **A creative professional/freelancer** — wondering if this fits your workflow | → [Using the Wallet](Using-the-Wallet.md) · [Preparing a Tax Disclosure](Preparing-a-Tax-Disclosure.md) |
| **Security-minded** — want the honest threat model, not marketing | → [Security Model & Honest Limits](Security-Model.md) |
| **A developer** — want to build or contribute | → [Getting Started as a Developer](Getting-Started-as-a-Developer.md) |
| **An auditor / researcher** | → [Architecture](Architecture.md) → [Cryptography Deep Dive](Cryptography-Deep-Dive.md) → [Testing & Quality](Testing-&-Quality.md) |
| **Allergic to jargon** | → [Glossary](Glossary.md) (and keep it bookmarked) |

---

## 📚 The complete library

### 📘 Foundations
- [Prism in Plain English](Prism-in-Plain-English.md) — the whole idea, zero jargon
- [Roadmap](Roadmap.md) — where we are, what's next, when
- [FAQ](FAQ.md) — short answers to real questions, including awkward ones
- [Glossary](Glossary.md) — every term, defined like you're smart but new
- [Governance](Governance.md) — who decides what, and how you can check them

### 🧰 Using Prism
- [Getting Started as a User](Getting-Started-as-a-User.md) — download to first payment
- [Using the Wallet](Using-the-Wallet.md) — send, receive, contacts, notifications
- [Recovery & Inheritance Guide](Recovery-and-Inheritance-Guide.md) — key shares, social recovery, dead-man switch
- [Fees & Payment Channels](Fees-and-Payment-Channels.md) — why it costs a tenth of a cent
- [Preparing a Tax Disclosure](Preparing-a-Tax-Disclosure.md) — accountant-friendly proofs

### 🧵 How it works
- [How Privacy Works](How-Privacy-Works.md) — hiding amounts, senders, receivers, IPs
- [The Prism Protocol: Selective Disclosure](The-Prism-Protocol.md) — proving facts without outing yourself
- [AI Layer Explained](AI-Layer-Explained.md) — what the assistant does, refuses, and never touches
- [Monetary Policy Rationale](Monetary-Policy-Rationale.md) — 21M, halvings, tail emission, zero pre-mine

### 🏗️ Building it
- [Architecture Overview](Architecture.md) — the four layers and trust boundaries
- [Chain Rules & Monetary Policy](Chain-Rules-and-Monetary-Policy.md) — consensus parameters, blocks, PoW
- [Cryptography Deep Dive](Cryptography-Deep-Dive.md) — field math → CLSAG → Bulletproofs+ → PLONK
- [Running a Node](Running-a-Node.md) — full, pruned, and indexer nodes
- [Testing & Quality](Testing-&-Quality.md) — property tests, fuzzing, release gates
- [Contributing](Contributing.md) — git flow, style, review expectations
- [Release Process](Release-Process.md) — how Windows/macOS/Linux binaries are built and published

---

## ⚠️ Current status, stated plainly

This project is in **Phase 1: Foundation**. The code in this repository is a *reference implementation* — real, tested, but unaudited and not yet connected to a live public network. **There is no way to acquire, mine, or spend real PRSM today.** Any exchange, token sale, or "PRSM contract address" claiming otherwise is fraudulent.

Track actual progress on the [Roadmap](Roadmap.md). The binding technical source of truth is [`spec.md`](../spec.md) at the repository root; the wiki explains it, the spec governs it.

---

*Found an error or an unclear page? Edit suggestions welcome — see [Contributing](Contributing.md).*
