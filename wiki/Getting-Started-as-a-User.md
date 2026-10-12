# Getting Started as a User

*From download to first private payment — written for someone who has never touched a blockchain and should not need to. As of v0.1.5 the flow below is real: install the wallet from [Releases](https://github.com/Esrevorter/Prism/releases/latest) and it works end-to-end on the Refraction testnet.*

---

## ⏳ Reality check first

What exists today: a **working wallet app** (Windows `.msi`, macOS `.dmg`, Linux `.deb`/`.AppImage`) and a **portable CLI**, both published per release, driving the production-parity *Refraction* testnet — faucet, mining, transactions, recovery, persistence all exercised by CI. What does **not** exist: a mainnet. Test-PRSM has no value, and any site, app, or "PRSM sale" claiming otherwise is a scam. Never store real value in pre-release software.

---

## 📥 Step 0 — Get the software (today: the CLI demo)

**Windows**
1. Download `prism-x.y.z-windows-x86_64.zip` from Releases.
2. Right-click → **Extract All…** → remember the folder.
3. Open the extracted folder, click the address bar, type `cmd`, press Enter.
4. Type `prism params` — you just read Prism's money rules directly from the code.

**macOS / Linux:** unzip, then `./prism params` in Terminal (macOS may need *right-click → Open* once, then System Settings approval for unsigned dev builds — see [Release Process](https://github.com/Esrevorter/Prism/wiki/Release-Process)).

Try these five commands like a guided tour:

| Command | What you learn |
|---|---|
| `prism params` | The fixed rules: block time, cap, fees |
| `prism genesis --network refraction` | The exact first block of the future testnet |
| `prism emit --heights 0,100,100000` | How many PRSM exist at any height |
| `prism mine --blocks 3` | Watch blocks get mined & validated (demo PoW) |

## 🚀 Step 1 — Wallet setup (works today, ~60 seconds)

1. **Install** the wallet for your OS and open it.
2. **Pick a password.** It seals your key on your machine — it is never sent anywhere, and the strength meter tells you honestly how good it is.
3. **Write down your 12 words.** One screen, big readable chips, a read-back check, and plain warnings: these words *are* your money on a new device; nobody at Prism can reset them. (They restore the exact same address — verified by tests.)
4. **First drip:** the built-in faucet sends test-PRSM so you can practice sending/receiving with fake money.

Optional tiers (Settings → Security): a trusted-contact recovery mode with quorum approval + 72 h timelock, or the classic legacy-seed route for crypto veterans — each explained in-app with its trade-offs stated plainly.

## 💰 Step 2 — Receiving money

1. Tap **Receive** → pick account (e.g., "Client income") or sub-address → show QR.
2. Payer scans; within ~2 minutes the payment appears **pending**, then confirms after 10 blocks (~20 min) with a notification at whatever intensity you set (*calm / normal / loud*).
3. Every payment lands at a brand-new one-time address behind the scenes — the address you shared is safe to reuse because it never actually appears on-chain ([How Privacy Works](https://github.com/Esrevorter/Prism/wiki/How-Privacy-Works)).

## 💸 Step 3 — Sending, the natural way

Type or say:

> **"Send $50 to Alex for the session, keep it private."**

What happens, visibly:

```text
① Intent compiled     "Pay Alex Rivera  ≈12.4 PRSM ($50 @ $4.03, locked 60 s)"
② Simulator preview   Fee: 0.0001 PRSM (~$0.001) · Private to everyone but Alex
③ Fraud check         ✓ destination verified in person 3 times · risk low
④ Human gate          3×3 picture puzzle bound to this exact transaction's
                      fingerprint — single-use, expires in 2 minutes
⑤ Key gate            your password unlocks the sealed key; signing happens
                      locally, nothing leaves the machine
⑥ Broadcast           Dandelion++ relay → mempool → block ≤ 2 min
⑦ Receipt             copyable disclosure ID — proof this payment existed,
                      usable later if you ever need to show it
```

Ambiguity is asked about, never guessed ("Which Alex?" arrives as one tap-to-answer chip, not a form). Fiat amounts always show the rate, its source, and a 60-second lock with slippage guard.

## 🔐 Step 4 — Set up your safety net (do this day one)

- **Test your backup once** — the app offers a dry-run restore so you *know* the 12 words work before you ever need them; optionally add trusted contacts for the tier-B recovery mode.
- **Turn on the dead-man switch** (optional): choose beneficiary + attestators; if you're unreachable 365 days, signed-heartbeat absence + attestator quorum starts their claim, with 30-day grace pings to you throughout. Full guide: [Recovery & Inheritance](https://github.com/Esrevorter/Prism/wiki/Recovery-and-Inheritance-Guide).
- **Set your comfort threshold** — amounts above it always wait out a veto window, even manual sends, if panic-lock is on.

## 🧾 Step 5 — When reality asks questions (taxes, landlords…)

*Privacy Tools → Prepare tax report* → pick period → review the side-by-side "reveals / doesn't reveal" panel → send the expiring proof package to your accountant's verifier. Walkthrough: [Preparing a Tax Disclosure](https://github.com/Esrevorter/Prism/wiki/Preparing-a-Tax-Disclosure).

## ⚙️ Neuro-inclusive settings worth knowing

- **Notification intensity dials** per event class (payments in, confirmations, agent digests).
- **Progressive disclosure:** advanced panels (rings, decoys, raw intents) are OFF by default.
- **Pending states explain themselves:** "Waiting for block 4/10 — about 12 minutes left," not a spinner.
- **"Sleep on it" button** on flagged sends: 10-second pause card, reasons listed, no shame language.

---

## Where to next

[Fees & Payment Channels](https://github.com/Esrevorter/Prism/wiki/Fees-and-Payment-Channels) · [Using the Wallet](https://github.com/Esrevorter/Prism/wiki/Using-the-Wallet) · [FAQ](https://github.com/Esrevorter/Prism/wiki/FAQ) · [Glossary](https://github.com/Esrevorter/Prism/wiki/Glossary)
