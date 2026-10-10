# Using the Wallet

*The feature tour for the Prism wallet UI (planned app, per [`spec.md` §7](../spec.md) — plus what's verifiable today in code). Written so you can pre-learn it or evaluate the design.*

---

## The main screen

Calm by default. What you see: your balance (one number, one fiat estimate), recent activity in plain language ("Payment from ClientCo · received Tuesday"), and two big buttons: **Send** and **Receive**. Everything else is behind progressive disclosure — the "advanced" panel (rings, decoys, raw intents, per-output controls) starts OFF and remembers if you turn it on. No badges, no streaks, no gamification; financial software that manufactures urgency is a dark pattern.

## Accounts & sub-addresses

One wallet holds named accounts ("Client income", "Collaborations", "Savings") with unlimited sub-addresses each. Different sub-addresses give different platforms/payers — payments to them all land in your account, but *you* can tell sources apart locally even though the chain can't link them. Handy: "this sub-address is only for Client X" makes later disclosures cleanly scoped ([Preparing a Tax Disclosure](Preparing-a-Tax-Disclosure.md)).

## Sending

Two paths, same safety pipeline:

- **Natural language:** type/say the request → intent card → preview → confirm. Details in [AI Layer Explained](AI-Layer-Explained.md).
- **Form mode:** recipient field (paste address or scan QR), amount toggle PRSM/USD, optional encrypted memo, fee selector (Base / Fast — the only two options; Fast costs at most 10× base ≈ $0.01).

Before signing, every send shows the simulator's exact terms: destination verified identity line, amount in PRSM + locked USD quote, fee, expected confirmation time, privacy statement ("visible to you and the recipient only"). Fraud-flagged destinations add a calm interruption card listing reasons plus a **"sleep on it"** 10-second pause. Nothing auto-executes; nothing hides behind a spinner.

**Scheduled/recurring sends** live under an account's menu and run through the agent permission system — i.e., they get grants, veto windows, and audit-log entries like any autopilot ([AI Layer](AI-Layer-Explained.md)).

## Receiving

Receive screen = QR + `prsm1…` address (+ `prsmsub…` for sub-addresses, `prsmr:` URI when embedding a requested amount/memo invoice). Share freely — the address never appears on-chain itself ([How Privacy Works](How-Privacy-Works.md)). Incoming states: *pending* (seen in mempool) → *confirmed block k/10* → *spendable*. Each stage tells you roughly how long remains instead of leaving you guessing.

## Contacts & verification

Address book entries carry a verification level: **verified in person (QR)**, **DNS proof**, or **trust chain** — surfaced exactly where it matters (the send preview line reads "Alex Rivera ✓ verified in person"). Contacts can consent to being nameable in income disclosures; non-consenting counterparties appear as opaque digests.

## Notifications & comfort settings

Intensity dials per event class (calm/normal/loud), quiet hours, and a personal **comfort threshold**: sends above it always wait out a veto window when panic-lock is enabled — including manual ones (duress mitigation E10). Notification wording is descriptive, never urgent ("2 of 10 confirmations · ~16 min remaining").

## Risk labels

Every incoming output gets a local risk score from the on-device model ("from newly seen address"). Labels are informational, stored only on your device, and never affect consensus — flagged funds remain spendable; the point is informing *you*, not policing the ledger ([Security Model](Security-Model.md) E17).

## Privacy Tools (menu)

| Tool | What it does | Deep dive |
|---|---|---|
| **Prepare tax report** | Income-attribution proofs for chosen periods | [Preparing a Tax Disclosure](Preparing-a-Tax-Disclosure.md) |
| **Proof of funds** | Balance-solvency disclosure with expiry | [Prism Protocol](The-Prism-Protocol.md) |
| **Provenance check** | Source-provenance proof against a pinned denylist root | [Prism Protocol](The-Prism-Protocol.md) |
| **Active disclosures** | Registry dashboard: what you revealed, to whom, expiring when | [Prism Protocol](The-Prism-Protocol.md) |
| **View-key scopes** | Generate/rotate scoped auditor keys | [Prism Protocol](The-Prism-Protocol.md) |

## Custody & recovery screens

- **Devices & contacts:** list every key share, its owner, last refresh date; add/remove legs triggers re-sharing ceremony.
- **Recovery:** guided flow with visible 72-hour countdown, warnings broadcast to all devices, cancel button available to any share-holder ([Recovery & Inheritance Guide](Recovery-and-Inheritance-Guide.md)).
- **Duress PIN:** set a second unlock code → opens plausible decoy wallet.
- **Dead-man switch:** beneficiary + attestator setup, heartbeat status, grace-ping history.

## Backup

Encrypted metadata snapshot (address book, registry, settings) to a user-chosen cloud with zero-knowledge envelope — restoring it recovers *context*, not spend rights (quorum still required). Deliberate design: backups can't become the weak point.

## Payments channels (Phase 3+)

Open a channel with a merchant/collaborator (2 base-fee txs), then tip and micropay instantly at ~zero cost; watchtower option guards against counterparty disappearance ([Fees & Payment Channels](Fees-and-Payment-Channels.md)).

---

**Next:** [Recovery & Inheritance Guide](Recovery-and-Inheritance-Guide.md) · [FAQ](FAQ.md) · [Glossary](Glossary.md)
