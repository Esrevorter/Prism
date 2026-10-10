# Prism in Plain English

*Ten minutes, zero jargon. If you finish this page and still have questions, the [FAQ](FAQ.md) probably has you.*

---

## The problem, in three sentences

Money on most blockchains is a public diary: every payment anyone ever made is visible forever, which means your income, your clients, and your habits are a downloadable dataset. Meanwhile, the "be your own bank" promise came with a loaded gun — one 12-word phrase standing between you and losing everything, and no undo button anywhere. And the people this hurts most are the ones already doing the hardest financial lives: freelancers and creative professionals with irregular global income, many small payments, and real reasons not to publish their ledger.

## What Prism does about it

**1. Private unless you say otherwise.** When you pay someone on Prism, outsiders see that *a* payment happened, but not who sent it, who received it, or how much. This isn't a setting you might accidentally turn off — it's how the network works, like end-to-end encryption but for money.

**2. You decide what to reveal, piece by piece.** Total privacy becomes inconvenient at tax time or when a landlord wants proof of funds. Prism solves this with *selective disclosure*: cryptography that lets you prove **one specific fact** ("my income in 2026 was exactly Σ", "I hold more than X") to **one specific person**, with an **expiry date** — without handing over your whole history. Think of it as a prism splitting white light: your counterpart sees one clean wavelength, not the whole beam.

**3. Losing a device shouldn't mean losing your money.** Instead of one doomsday password, your spending authority is split into several **key shares** — some on your phone, some on your laptop, optionally some with trusted friends. Lose something? Gather the remaining shares (or your friends' approvals), go through recovery, and there's a built-in **72-hour waiting period** during which every device you own gets loudly warned and any old share can cancel the process. A thief with your phone can't drain you; a coerced you can stall them.

**4. Software that speaks human.** Tell your wallet *"send $50 to Alex for the session, keep it private."* It figures out the exact transaction, shows you a plain-English preview ("this sends ≈12.4 PRSM, fee $0.001, appears private except to Alex"), checks Alex's address against known scams, and only then asks for your fingerprint. The assistant can *prepare* things but can never *sign* anything without you.

**5. Pennies, predictably.** Fees are fixed at 0.0001 PRSM (~$0.001). No bidding wars, no "why is a $12 transfer costing $40 today." Tiny recurring payments (tips, micropayments) can go through instant payment channels that cost effectively nothing.

## A day in the life (the intended user)

Maya is a freelance designer. On Tuesday she receives client payments from three platforms — nobody watching the network can correlate those payments to her identity or total them up. She pays her collaborator instantly, in another country, for about a tenth of a cent in fees. In April, her accountant needs numbers: Maya taps *Privacy Tools → Prepare tax report*, picks the year, and gets a cryptographic package that proves exactly her incoming payments per period — nothing else, expiring after 90 days. Her phone dies on the way to a client meeting; from a loaner device she starts recovery, two friends and her desktop approve, and 72 hours later (with warnings screaming on every device the whole time) she's fully back, old keys revoked. That's the whole product experience, described completely.

## What Prism deliberately is *not*

- **Not anonymous in the absolute sense.** It hides far more than Bitcoin does, by default and permanently. But no technology defeats a global adversary that can watch every wire *and* pressure every device; we describe our honest limits in [Security Model](Security-Model.md) instead of pretending.
- **Not a company coin.** No pre-mine, no token sale, no foundation-held keys, no admin switch. The supply rules are fixed in code: 21 million PRSM maximum, created only by miners over time.
- **Not a compliance tool first.** There's no built-in blacklist enforcement and no universal surveillance key. The disclosure tools exist because *users* need them for taxes and audits — they work for you, not on you.
- **Not finished.** Today this is the reference implementation — the engine on the test bench. See [Roadmap](Roadmap.md) for the path to a live network.

## The name, finally

A prism takes white light and lets you choose which wavelengths to show. That's exactly the design goal: your full financial life stays the invisible beam; you pick the single color each person gets to see. Hence **Prism** — *clarity on your terms.*

---

**Keep going:** [How Privacy Works](How-Privacy-Works.md) · [Glossary](Glossary.md) · [FAQ](FAQ.md)
