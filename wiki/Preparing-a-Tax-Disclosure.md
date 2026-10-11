# Preparing a Tax Disclosure

*How to satisfy real-world reporting obligations from Prism privacy tools — for users and for the accountants/verifiers receiving their proofs. Built on [The Prism Protocol](The-Prism-Protocol.md).*

---

## The mental model (read this first)

You are **not** exporting your transaction history. You are generating a *mathematical proof of a specific statement* about your funds:

```text
statement : "Incoming payments to me during 2026 total exactly Σ PRSM,
             attributable to these consenting counterparties."
revealed  : that sentence, plus verification it's true against public chain data.
hidden    : your balance · every outgoing payment · individual dates/amounts
            unless you opt in · any counterparty who didn't consent to naming
```

The verifier checks the proof against public chain state with their own node or light client — they never need your view key, your device, or your trust beyond the statement itself.

## For users: the walkthrough

1. **Privacy Tools → Prepare tax report.**
2. **Pick the period** (fiscal year / quarter) and **statement type**. For most tax filings: *Income Attribution*. If your jurisdiction also asks about holdings at year-end, add a *Balance Solvency* disclosure at that date.
3. **Review the side-by-side panel.** The wallet renders, item by item, what will be revealed and what stays hidden — including which counterparties appear named (only contacts who set `consent_to_be_named_in_disclosures`) versus as opaque digests.
4. **Set expiry & recipient nonce.** Default expiry is 90 days post-issue; the proof binds to your accountant's verifier nonce so a copy handed to anyone else fails verification. Confirm both before generating.
5. **Generate locally.** Proving happens on your device; nothing uploads anywhere.
6. **Share the package** (PDF summary + machine-readable proof + app link). Your accountant opens it in the verifier app, which re-checks against the chain independently.
7. **Registry entry created automatically:** visible under *Active disclosures* with its expiry date. Keep your own records as usual — the proof supports your figures; you still file numbers through normal channels.

### Practical tips

- **Use sub-addresses per income source** (platform A vs. agency B vs. direct clients) from day one — attribution proofs become dramatically cleaner and can be scoped per-source later ([Using the Wallet](Using-the-Wallet.md)).
- **Generate early.** Proofs bind to chain state; if your 2026 activity somehow continues changing (late payments), regenerate rather than stretching an old proof.
- **Anchor only if asked.** Optional on-chain anchoring creates a consensus-recorded attestation — useful for disputes, unnecessary for ordinary filings, and itself a small disclosure event (the fact-of-anchoring becomes public). Default: off.
- **One-time artifacts.** Reuse requires regeneration by design (D3 semantics); if two parties need the same figure, each gets their own nonce-bound copy.

## For accountants & verifiers

What you receive and how to check it honestly:

| Field | Meaning | Your check |
|---|---|---|
| `statement_type` / `circuit_id` | Which claim, which versioned circuit | Reject deprecated circuit IDs (E6 policy) |
| `public_inputs` | Period bounds, verifier nonce, amount commitments | Nonce must equal **your** session nonce — otherwise the proof wasn't made for you |
| `expiry_height` / timestamp | Validity window | Reject expired proofs (E7: accepting anyway is *your* liability — verifier SDK enforces by default) |
| `proof` | PLONK bytes | Verify against local chain state via verifier SDK |
| Optional anchor txid | On-chain attestation reference | Cross-check commitment root inclusion |

Expectations to set with clients: disclosures prove *statements*, not *completeness of intent* — pair them with standard professional diligence. And note the honest limitation again: once you've verified and retained a proof, your client cannot recall it; they can only ensure it expires and doesn't generalize.

Jurisdiction interoperability (making statements map onto local forms) is active research area R4; v1 ships conservative primitives, not jurisdiction packs.

## Frequently asked here

**"Can I prove taxes were paid but hide the amounts?"** You prove *what you choose*: e.g., solvency ≥ threshold without totals. Exact figures require choosing the exact-figure statement — the dial is yours per disclosure.
**"What if the auditor demands my full view key?"** Then selective disclosure isn't solving your case; the scoped tools exist precisely so full-key requests should look disproportionate. Rotating scoped keys bound any leak ([Prism Protocol](The-Prism-Protocol.md) §rotation).
**"Does the IRS/government get automatic access?"** No. Nothing is automatic. Disclosures are user-generated artifacts — that's the entire design point.

---

**Next:** [The Prism Protocol](The-Prism-Protocol.md) · [FAQ](FAQ.md) · [Glossary](Glossary.md)
