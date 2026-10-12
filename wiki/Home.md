<div align="center">

# 🔹 Prism Wiki

### Clarity on your terms.

*Everything about Prism — from "what is this?" to "how do I audit the code?"*

[![Release](https://img.shields.io/badge/release-v0.1.5-7c5cff?style=flat-square&logo=github&logoColor=white)](https://github.com/Esrevorter/Prism/releases/latest)
[![Tests](https://img.shields.io/badge/tests-511%20passing-22c55e?style=flat-square&logo=pytest&logoColor=white)](https://github.com/Esrevorter/Prism/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-f5c242?style=flat-square)](https://github.com/Esrevorter/Prism/blob/main/LICENSE)

</div>

---

> [!TIP]
> **Fastest route:** grab the wallet from [Releases](https://github.com/Esrevorter/Prism/releases/latest) → ~60-second setup (password + 12 words) → faucet → first private test payment. Details: [Getting Started as a User](Getting-Started-as-a-User).

## 👋 Which door should you walk through?

| You are… | Start here |
|---|---|
| **Curious** — heard about Prism, want the gist in 10 minutes | → [Prism in Plain English](Prism-in-Plain-English) |
| **A future user** — want to set up a wallet and get paid | → [Getting Started as a User](Getting-Started-as-a-User) |
| **A creative professional/freelancer** — wondering if this fits your workflow | → [Using the Wallet](Using-the-Wallet) · [Preparing a Tax Disclosure](Preparing-a-Tax-Disclosure) |
| **Security-minded** — want the honest threat model, not marketing | → [Security Model & Honest Limits](Security-Model) |
| **A developer** — want to build or contribute | → [Getting Started as a Developer](Getting-Started-as-a-Developer) |
| **An auditor / researcher** | → [Architecture](Architecture) → [Cryptography Deep Dive](Cryptography-Deep-Dive) → [Testing & Quality](Testing-&-Quality) |
| **Allergic to jargon** | → [Glossary](Glossary) (and keep it bookmarked) |

---

## 📚 The complete library

### 📘 Foundations
- [Prism in Plain English](Prism-in-Plain-English) — the whole idea, zero jargon
- [Roadmap](Roadmap) — where we are, what's next, when
- [FAQ](FAQ) — short answers to real questions, including awkward ones
- [Glossary](Glossary) — every term, defined like you're smart but new
- [Governance](Governance) — who decides what, and how you can check them

### 🧰 Using Prism
- [Getting Started as a User](Getting-Started-as-a-User) — download to first payment
- [Using the Wallet](Using-the-Wallet) — send, receive, contacts, notifications
- [Recovery & Inheritance Guide](Recovery-and-Inheritance-Guide) — recovery phrase, optional trusted-contact path, dead-man switch
- [Fees & Payment Channels](Fees-and-Payment-Channels) — why it costs a tenth of a cent
- [Preparing a Tax Disclosure](Preparing-a-Tax-Disclosure) — accountant-friendly proofs

### 🧵 How it works
- [How Privacy Works](How-Privacy-Works) — hiding amounts, senders, receivers, IPs
- [The Prism Protocol: Selective Disclosure](The-Prism-Protocol) — proving facts without outing yourself
- [AI Layer Explained](AI-Layer-Explained) — what the assistant does, refuses, and never touches
- [Monetary Policy Rationale](Monetary-Policy-Rationale) — 21M, halvings, tail emission, zero pre-mine

### 🏗️ Building it
- [Architecture Overview](Architecture) — the four layers and trust boundaries
- [Chain Rules & Monetary Policy](Chain-Rules-and-Monetary-Policy) — consensus parameters, blocks, PoW
- [Cryptography Deep Dive](Cryptography-Deep-Dive) — field math → CLSAG → Bulletproofs+ → PLONK
- [Running a Node](Running-a-Node) — full, pruned, and indexer nodes
- [Testing & Quality](Testing-&-Quality) — property tests, fuzzing, release gates
- [Contributing](Contributing) — git flow, style, review expectations
- [Release Process](Release-Process) — how Windows/macOS/Linux binaries are built and published

---

## ⚠️ Current status, stated plainly

**Wallet v0.1.5 is released** — installers for Windows, macOS and Linux are in [Releases](https://github.com/Esrevorter/Prism/releases/latest), running the lean-auth wallet (password-sealed key + CAPTCHA-gated signing, GUI v2) against the production-parity *Refraction* testnet. The chain itself remains a *reference implementation*: real, tested (511 passing), unaudited, and **not yet a live public network**. There is no way to acquire, mine, or spend real PRSM today; any exchange, token sale, or "PRSM contract address" claiming otherwise is fraudulent. Custody note: since spec Amendment A2 the wallet uses a sealed local key + 12-word phrase — the original multi-device MPC ceremony design is retired (kept in-tree as historical evidence).

Track actual progress on the [Roadmap](Roadmap). The binding technical source of truth is [`spec.md`](https://github.com/Esrevorter/Prism/blob/main/spec.md) at the repository root; the wiki explains it, the spec governs it.

---

*Found an error or an unclear page? Edit suggestions welcome — see [Contributing](Contributing).*
