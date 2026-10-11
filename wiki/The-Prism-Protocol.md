# The Prism Protocol: Selective Disclosure

*The project's namesake feature: proving chosen facts about chosen funds, to chosen parties, for chosen durations. Binding spec: [`spec.md` §5.2–5.5](../spec.md).*

---

## The problem this solves

Default-on privacy is wonderful until reality asks a question: the tax office wants income figures, a lender wants proof of reserves, an exchange off-ramp wants assurance funds aren't sanctioned. Every existing answer is catastrophic: hand over your entire ledger (view key) and every past *and future* payment becomes readable forever. Privacy systems without a disclosure story don't resist compliance — they just force all-or-nothing compliance.

**Prism's answer:** instead of revealing data, reveal *proofs about data*. A zero-knowledge proof convinces a verifier that a statement is true while leaking nothing beyond the statement itself.

---

## The five disclosure circuits (v1)

Implemented in `prism/zk/circuits.py`; each is a versioned circuit ID (e.g. `prsm.solvency.v1`).

| # | Proof | Statement it proves | Typical user |
|---|---|---|---|
| 1 | **Source Provenance** | "Output O was not received from anything on sanctioned-list L" — checked against a hash-chained denylist accumulator root published weekly on-chain | Anyone cashing out at an exchange |
| 2 | **Balance Solvency** | "My unspent commitments sum ≥ X over period [t₁,t₂]" — no outputs identified, no total revealed | Rent applications, DeFi collateral |
| 3 | **Income Attribution** | "Incoming payments totaling exactly Σ during [t₁,t₂] were received by me" — optionally naming only counterparties who consented | Tax season |
| 4 | **Reserve/Attestation** | "I hold collateral C right now" | Merchants, escrow |
| 5 | **Clean-Exit** | "This output will be spent only to addresses I control" | Exchange off-ramps |

All operate against the **public commitment tree** — the same sealed envelopes everyone can see but nobody can open (§[How Privacy Works](How-Privacy-Works.md)). The proof manipulates the envelopes' math directly.

## Why PLONK zk-SNARKs here (Decision D2)

Spec v0.9 left open whether SNARKs or STARKs fit mobile users. Resolution: **SNARKs (PLONK/ultra-honk family)** because on phones *proof size dominates*: SNARK proofs are constant-size kilobytes regardless of statement complexity, while STARKs carry larger polylog proofs — bad for metered connections and verifier bandwidth. The classic SNARK weakness (trusted setup ceremony) is mitigated three ways:

1. **PLONK's universal (circuit-agnostic) SRS** — one multi-party ceremony covers *all* v1 circuits; per-circuit ceremonies would multiply risk.
2. **Published entropy transcripts** — anyone can audit that the ceremony wasn't a single party's coin flips.
3. **Re-instantiation path** — if setup trust ever collapsed, circuits re-instantiate under a fresh SRS with versioned IDs; old proofs get flagged, chain keeps running.

Meanwhile the *amount/range layer stays Bulletproofs+* (setup-free) — SNARKs are used **only** for these disclosure circuits. Two stacks, each where it wins.

---

## Revocability — the honest semantics (Decision D3)

Marketing says "revocable disclosures." Here is precisely what that means, and it's deliberately narrower:

1. **Expiry.** Every proof `π` binds to `(statement, verifier-nonce, expiry-timestamp)` inside its public inputs. Verifier software rejects expired proofs; a disclosure can't be reused after its window (default: 90 days).
2. **Rotation.** Scoped view keys rotate on schedule; a rotated key never reveals data created *after* rotation. If a copy leaks, blast radius is bounded in time.

What revocability is **not**: recall. Once a verifier accepts a proof, they hold a verified artifact; no cryptography unsends knowledge. Forward-revocable encryption of auditor auxiliary data was descoped from v1 (research area R6). The hard UX rule in product copy everywhere:

> *"You can limit and expire disclosures; you cannot recall one already accepted."*

## The Disclosure Registry

Your wallet logs every proof you generate: statement type, scope, recipient, expiry, anchored-or-not. A dashboard shows "Active disclosures" so *you always know what you've revealed*. Optional on-chain anchoring (Merkle root) exists for when both parties want a consensus-recorded attestation — e.g., collateral posted against a loan. Anchored disclosures ride along as memo-tagged transaction attachments (`attachment.type = anchored_disclosure`), never as separate spam.

## On-chain vs off-chain verification

Default is **off-chain**: the verifier runs a small client that checks your proof against public chain state locally. Nothing hits L1, volume stays low, and there's no broadcast of the fact-of-disclosure itself unless both parties choose anchoring. This keeps the protocol economically scalable even if millions of accountants verify daily.

## Who curates the sanction lists? (Decision D5)

Circuit #1 needs a denylist — which immediately raises the censorship question. Design:

- An elected **Prism Compliance Council** (7 seats, staggered 18-month terms, public signing policy, ≥5-of-7 signatures) signs the *official* weekly accumulator root.
- **Parallel third-party lists remain first-class.** Proofs reference a specific root hash; verifiers choose which list(s) they accept. No single censor point exists — a council gone rogue changes only consumers who pin its roots.
- Everything is checkable: roots publish with signatures; users can pin any list. Governance mechanics live in [Governance](Governance.md); election/slashing rules are research area R7.

Note what this does **not** do: the chain never *enforces* the denylist. Funds from listed sources still arrive and remain spendable; flagging is local labeling plus optional provenance proofs. Consensus validates math, never politics.

---

## Example: tax season, end to end

1. Maya opens *Privacy Tools → Prepare tax report*, picks Jan 1 – Dec 31, 2026, statement **Income Attribution**.
2. Wallet builds the proof locally against her owned commitments; screen shows side-by-side: *"Will reveal: exact incoming totals per counterparty class. Will NOT reveal: your balance, outgoing payments, dates of individual receipts, anyone who didn't consent to being named."*
3. She sends the package to her accountant's verifier app. Proof verifies against public chain state, expires 90 days later, bound to her accountant's nonce so it can't be repurposed to a different verifier.
4. Registry entry appears under Active disclosures with a one-line revoke-equivalent: *expires Apr 2, 2027*.

Full walkthrough: [Preparing a Tax Disclosure](Preparing-a-Tax-Disclosure.md).

---

## In code

| Piece | Location |
|---|---|
| Circuit definitions & statements | `prism/zk/circuits.py` |
| PLONK-style prover/verifier (reference grade) | `prism/zk/plonk.py` |
| Statement encoding (canonical, fuzz-tested) | `prism/zk/` + `prism/tests/test_zk_statement_encoding.py` |
| Denylist accumulator | `prism/chain/denylist.py`, header field `denylist_root` |
| Registry & scoped-key structures | wallet-side schemas, [`spec.md` §6.2](../spec.md) |

**Next:** [AI Layer Explained](AI-Layer-Explained.md) · [Security Model](Security-Model.md) · [Glossary](Glossary.md)
