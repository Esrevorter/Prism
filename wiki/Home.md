<div align="center">

# 🔹 Prism Wiki

### Clarity on your terms.

*Everything about Prism — from "what is this?" to "how do I audit the code?"*

[![Release](https://img.shields.io/badge/release-v0.1.5-7c5cff?style=flat-square&logo=github&logoColor=white)](https://github.com/Esrevorter/Prism/releases/latest)
[![Tests](https://img.shields.io/badge/tests-511%20passing-22c55e?style=flat-square&logo=pytest&logoColor=white)](https://github.com/Esrevorter/Prism/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-f5c242?style=flat-square)](../LICENSE)

</div>

---

> [!TIP]
> **Fastest route:** grab the wallet from [Releases](https://github.com/Esrevorter/Prism/releases/latest) → ~60-second setup (password + 12 words) → faucet → first private test payment. Details: [Getting Started as a User](https://github.com/Esrevorter/Prism/wiki/Getting-Started-as-a-User).

## 👋 Which door should you walk through?

| You are… | Start here |
|---|---|
| **Curious** — heard about Prism, want the gist in 10 minutes | → [Prism in Plain English](https://github.com/Esrevorter/Prism/wiki/Prism-in-Plain-English) |
| **A future user** — want to set up a wallet and get paid | → [Getting Started as a User](https://github.com/Esrevorter/Prism/wiki/Getting-Started-as-a-User) |
| **A creative professional/freelancer** — wondering if this fits your workflow | → [Using the Wallet](https://github.com/Esrevorter/Prism/wiki/Using-the-Wallet) · [Preparing a Tax Disclosure](https://github.com/Esrevorter/Prism/wiki/Preparing-a-Tax-Disclosure) |
| **Security-minded** — want the honest threat model, not marketing | → [Security Model & Honest Limits](https://github.com/Esrevorter/Prism/wiki/Security-Model) |
| **A developer** — want to build or contribute | → [Getting Started as a Developer](https://github.com/Esrevorter/Prism/wiki/Getting-Started-as-a-Developer) |
| **An auditor / researcher** | → [Architecture](https://github.com/Esrevorter/Prism/wiki/Architecture) → [Cryptography Deep Dive](https://github.com/Esrevorter/Prism/wiki/Cryptography-Deep-Dive) → [Testing & Quality](https://github.com/Esrevorter/Prism/wiki/Testing-&-Quality) |
| **Allergic to jargon** | → [Glossary](https://github.com/Esrevorter/Prism/wiki/Glossary) (and keep it bookmarked) |

---

## 📚 The complete library

### 📘 Foundations
- [Prism in Plain English](https://github.com/Esrevorter/Prism/wiki/Prism-in-Plain-English) — the whole idea, zero jargon
- [Roadmap](https://github.com/Esrevorter/Prism/wiki/Roadmap) — where we are, what's next, when
- [FAQ](https://github.com/Esrevorter/Prism/wiki/FAQ) — short answers to real questions, including awkward ones
- [Glossary](https://github.com/Esrevorter/Prism/wiki/Glossary) — every term, defined like you're smart but new
- [Governance](https://github.com/Esrevorter/Prism/wiki/Governance) — who decides what, and how you can check them

### 🧰 Using Prism
- [Getting Started as a User](https://github.com/Esrevorter/Prism/wiki/Getting-Started-as-a-User) — download to first payment
- [Using the Wallet](https://github.com/Esrevorter/Prism/wiki/Using-the-Wallet) — send, receive, contacts, notifications
- [Recovery & Inheritance Guide](https://github.com/Esrevorter/Prism/wiki/Recovery-and-Inheritance-Guide) — recovery phrase, optional trusted-contact path, dead-man switch
- [Fees & Payment Channels](https://github.com/Esrevorter/Prism/wiki/Fees-and-Payment-Channels) — why it costs a tenth of a cent
- [Preparing a Tax Disclosure](https://github.com/Esrevorter/Prism/wiki/Preparing-a-Tax-Disclosure) — accountant-friendly proofs

### 🧵 How it works
- [How Privacy Works](https://github.com/Esrevorter/Prism/wiki/How-Privacy-Works) — hiding amounts, senders, receivers, IPs
- [The Prism Protocol: Selective Disclosure](https://github.com/Esrevorter/Prism/wiki/The-Prism-Protocol) — proving facts without outing yourself
- [AI Layer Explained](https://github.com/Esrevorter/Prism/wiki/AI-Layer-Explained) — what the assistant does, refuses, and never touches
- [Monetary Policy Rationale](https://github.com/Esrevorter/Prism/wiki/Monetary-Policy-Rationale) — 21M, halvings, tail emission, zero pre-mine

### 🏗️ Building it
- [Architecture Overview](https://github.com/Esrevorter/Prism/wiki/Architecture) — the four layers and trust boundaries
- [Chain Rules & Monetary Policy](https://github.com/Esrevorter/Prism/wiki/Chain-Rules-and-Monetary-Policy) — consensus parameters, blocks, PoW
- [Cryptography Deep Dive](https://github.com/Esrevorter/Prism/wiki/Cryptography-Deep-Dive) — field math → CLSAG → Bulletproofs+ → PLONK
- [Running a Node](https://github.com/Esrevorter/Prism/wiki/Running-a-Node) — full, pruned, and indexer nodes
- [Testing & Quality](https://github.com/Esrevorter/Prism/wiki/Testing-&-Quality) — property tests, fuzzing, release gates
- [Contributing](https://github.com/Esrevorter/Prism/wiki/Contributing) — git flow, style, review expectations
- [Release Process](https://github.com/Esrevorter/Prism/wiki/Release-Process) — how Windows/macOS/Linux binaries are built and published

---

## ⚠️ Current status, stated plainly

**Wallet v0.1.5 is released** — installers for Windows, macOS and Linux are in [Releases](https://github.com/Esrevorter/Prism/releases/latest), running the lean-auth wallet (password-sealed key + CAPTCHA-gated signing, GUI v2) against the production-parity *Refraction* testnet. The chain itself remains a *reference implementation*: real, tested (511 passing), unaudited, and **not yet a live public network**. There is no way to acquire, mine, or spend real PRSM today; any exchange, token sale, or "PRSM contract address" claiming otherwise is fraudulent. Custody note: since spec Amendment A2 the wallet uses a sealed local key + 12-word phrase — the original multi-device MPC ceremony design is retired (kept in-tree as historical evidence).

Track actual progress on the [Roadmap](https://github.com/Esrevorter/Prism/wiki/Roadmap). The binding technical source of truth is [`spec.md`](https://github.com/Esrevorter/Prism/wiki/spec) at the repository root; the wiki explains it, the spec governs it.

---

*Found an error or an unclear page? Edit suggestions welcome — see [Contributing](https://github.com/Esrevorter/Prism/wiki/Contributing).*
