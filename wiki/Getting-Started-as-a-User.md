# Getting Started as a User

*From download to first private payment — written for someone who has never touched a blockchain and should not need to. Note: Prism is pre-testnet; this page documents the *planned* flow plus what works today (the command-line demo).*

---

## ⏳ Reality check first

There is **no live network yet** and no official wallet app. What exists today:

1. **The reference implementation** in this repository — you can run the `prism` program from [Releases](https://github.com/prism-prsm/prism/releases) and explore chain rules, emission math, and demo mining locally. Great for curiosity, zero financial use.
2. **The documented user experience below** — how the Phase-1+ wallet will work, per [`spec.md` §7](../spec.md). Read it now so that when Refraction (testnet) opens, you'll know exactly what you're doing.

Beware: any site, app, or "PRSM sale" claiming otherwise right now is a scam.

---

## 📥 Step 0 — Get the software (today: the CLI demo)

**Windows**
1. Download `prism-x.y.z-windows-x86_64.zip` from Releases.
2. Right-click → **Extract All…** → remember the folder.
3. Open the extracted folder, click the address bar, type `cmd`, press Enter.
4. Type `prism params` — you just read Prism's money rules directly from the code.

**macOS / Linux:** unzip, then `./prism params` in Terminal (macOS may need *right-click → Open* once, then System Settings approval for unsigned dev builds — see [Release Process](Release-Process.md)).

Try these five commands like a guided tour:

| Command | What you learn |
|---|---|
| `prism params` | The fixed rules: block time, cap, fees |
| `prism genesis --network refraction` | The exact first block of the future testnet |
| `prism emit --heights 0,100,100000` | How many PRSM exist at any height |
| `prism mine --blocks 3` | Watch blocks get mined & validated (demo PoW) |

## 🚀 Step 1 — Wallet setup (Refraction testnet, Phase 1+)

Target: under 5 minutes, **no seed phrase required**.

1. **Install & enroll biometrics.** Face ID / fingerprint / Windows Hello stays on your device forever. Nothing about it goes online.
2. **Choose a custody mode** — the one real decision:

   | Mode | Best for | How recovery works |
   |---|---|---|
   | **A. Solo MPC (default)** | Multi-device users | 2-of-3 shares across *your own* devices (phone + desktop + one more). Lose two? You'll be guided into Social mode for the future — the limitation is stated plainly up front (Decision D4: Prism holds no share ever). |
   | **B. Social MPC (recommended)** | Most people | Your phone + desktop + **4 invited friends** = 5 shares; any 3 sign. Friends see nothing about your funds — they hold opaque numbers. |
   | **C. Legacy seed** | Crypto veterans | Classic mnemonic. Big warning banner: *this is the irreversible option.* |

3. **Share ceremony:** scan QR codes with each friend (or send invite links); every transfer end-to-end encrypted; liveness-checked; if anyone drops off mid-ceremony, resume safely.
4. **First drip:** a faucet sends test-PRSM so you can practice sending/receiving with fake money.

## 💰 Step 2 — Receiving money

1. Tap **Receive** → pick account (e.g., "Client income") or sub-address → show QR.
2. Payer scans; within ~2 minutes the payment appears **pending**, then confirms after 10 blocks (~20 min) with a notification at whatever intensity you set (*calm / normal / loud*).
3. Every payment lands at a brand-new one-time address behind the scenes — the address you shared is safe to reuse because it never actually appears on-chain ([How Privacy Works](How-Privacy-Works.md)).

## 💸 Step 3 — Sending, the natural way

Type or say:

> **"Send $50 to Alex for the session, keep it private."**

What happens, visibly:

```text
① Intent compiled     "Pay Alex Rivera  ≈12.4 PRSM ($50 @ $4.03, locked 60 s)"
② Simulator preview   Fee: 0.0001 PRSM (~$0.001) · Private to everyone but Alex
③ Fraud check         ✓ destination verified in person 3 times · risk low
④ Confirm             fingerprint / face
⑤ MPC signing         your phone + desktop (+ contact if needed) co-sign
⑥ Broadcast           Dandelion++ relay → mempool → block ≤ 2 min
⑦ Receipt             copyable disclosure ID — proof this payment existed,
                      usable later if you ever need to show it
```

Ambiguity is asked about, never guessed ("Which Alex?" arrives as one tap-to-answer chip, not a form). Fiat amounts always show the rate, its source, and a 60-second lock with slippage guard.

## 🔐 Step 4 — Set up your safety net (do this day one)

- **Add recovery contacts** if you chose Solo mode — 3-of-5 takes ten minutes and prevents the "lost both devices" dead end.
- **Turn on the dead-man switch** (optional): choose beneficiary + attestators; if you're unreachable 365 days, signed-heartbeat absence + attestator quorum starts their claim, with 30-day grace pings to you throughout. Full guide: [Recovery & Inheritance](Recovery-and-Inheritance-Guide.md).
- **Set your comfort threshold** — amounts above it always wait out a veto window, even manual sends, if panic-lock is on.

## 🧾 Step 5 — When reality asks questions (taxes, landlords…)

*Privacy Tools → Prepare tax report* → pick period → review the side-by-side "reveals / doesn't reveal" panel → send the expiring proof package to your accountant's verifier. Walkthrough: [Preparing a Tax Disclosure](Preparing-a-Tax-Disclosure.md).

## ⚙️ Neuro-inclusive settings worth knowing

- **Notification intensity dials** per event class (payments in, confirmations, agent digests).
- **Progressive disclosure:** advanced panels (rings, decoys, raw intents) are OFF by default.
- **Pending states explain themselves:** "Waiting for block 4/10 — about 12 minutes left," not a spinner.
- **"Sleep on it" button** on flagged sends: 10-second pause card, reasons listed, no shame language.

---

## Where to next

[Fees & Payment Channels](Fees-and-Payment-Channels.md) · [Using the Wallet](Using-the-Wallet.md) · [FAQ](FAQ.md) · [Glossary](Glossary.md)
