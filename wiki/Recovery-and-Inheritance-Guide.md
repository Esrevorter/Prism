# Recovery & Inheritance Guide

*Everything about keeping access when things go wrong — key shares, social recovery, duress protections, and the dead-man switch. Binding spec: [`spec.md` §7.4–7.5, §7.8, §9](../spec.md); code: `prism/mpc/recovery.py`.*

---

## How your key actually works (30 seconds)

Your spending authority is a number split into **shares** (Shamir secret sharing with verifiable commitments). Any *threshold* subset can co-sign; fewer than threshold learn nothing and can do nothing:

| Mode | Shares | Threshold | Who holds them |
|---|---|---|---|
| Solo MPC | 3 | 2 | Your own devices only |
| Social MPC | 5 | 3 | Your devices + up to 4 chosen contacts |

Contacts hold opaque numbers — they cannot view balances, transactions, or anything else. Being a recovery contact costs them nothing but a rare approval tap. Per Decision D4, **Prism itself never holds any share** — there is no company backdoor leg to subpoena, leak, or mismanage.

Shares **proactively refresh** every 90 days (or after any recovery): the underlying key stays the same while every share value changes — a stolen old share quietly becomes worthless over time.

## Losing a device (the everyday case)

Nothing happens to your funds. Grab the replacement device, re-enroll biometrics, and the wallet starts re-sharing against your remaining quorum in the background. If you've dropped below comfort (e.g., lost one of two solo devices), the app says so plainly and walks you through adding legs.

## Losing *access* — full social recovery flow

1. **Start recovery** on a new device: identity attestation via fresh biometric enrollment (+ old-share challenge token if you have any fragment).
2. **Quorum approves:** each contact gets a simple prompt — *"Confirm this is Maya recovering her wallet? Code: 4821"* — they verify out-of-band (a quick call beats trusting a screen).
3. **The 72-hour timelock.** Even after quorum approval, recovered shares cannot move funds for 72 hours. Throughout: loud warnings on every registered device, push/email notifications, and a visible countdown. **Anyone holding any stale share can cancel the recovery** during the window. This kills the "stolen phone + rushed coerced friends" attack: theft needs silence, and Prism refuses to be silent.
4. **Re-share & revoke:** a brand-new share set is generated; every old share dies. The attacker's fragment is now mathematically noise.

### Anti-collusion options

- Require ≥1 **device** share inside any recovery subset (so contacts alone can't sweep you).
- Trusted-quorum-subsets: name which combinations you accept for recovery.
- Duress behavior: forced recovery still burns the full 72 h in an alarm state that's visibly screaming — rescuers see it too.

## Duress & decoy wallet

Set a **duress PIN**: unlocking with it opens a plausible low-balance decoy wallet instead of showing failure or panic. Optional **panic lock** freezes sends above your comfort threshold pending the veto window — even manual ones — covering the "forced transfer right now" scenario (E10). Alarms default to private (your devices only; contact-notification opt-in per group) so a coerced user isn't inadvertently outed — aggressiveness validated in the Phase-3 neuro-inclusive study.

## Inheritance — the dead-man switch

Goal: if you die or become permanently incapacitated, chosen people get your funds **without you ever trusting a custodian while alive**.

Setup: beneficiary public key + M-of-K attestators (contacts, lawyer, or an oracle service) + inactivity window (default **365 days**) + grace pings every 30 days.

Mechanics:
1. Your wallet normally sends **signed heartbeat messages** (privacy-preserving liveness — no on-chain inference needed). Missed heartbeats accumulate silently.
2. After the full window without heartbeats **and** attestator-quorum confirmation, the beneficiary's special share activates.
3. Grace pings throughout mean false positives ("app broke, I'm fine") resolve by simply opening the wallet.
4. Edge case E12: death *during* a veto/grace period resolves after the full inactivity window with attestator quorum — no half-activated states.

On-chain inactivity remains the fallback signal for users who never send heartbeats (research area R3/R6 territory; v1 defaults as written).

## Backups vs recovery (don't confuse them)

Your encrypted cloud snapshot (address book, disclosure registry, settings) restores *context*, never spend rights — restoring onto a new device still requires quorum. Conversely, quorum without the snapshot loses your labels but not your money. Both halves are deliberately incomplete alone.

## If you chose Legacy Seed mode

There is no recovery path — that's what "legacy" means. The wallet warns at creation and again before first large send (E11). We recommend MPC modes precisely because losing 24 words shouldn't immolate a lifetime of work.

---

### Quick reference

| Situation | What to do |
|---|---|
| Phone lost/stolen | Nothing urgent — order replacement, re-enroll, keep calm (single share = useless) |
| All devices gone | Social recovery: gather 3-of-5 approvals → wait 72 h → new shares |
| Recovery started that you didn't authorize | Cancel immediately from any device/share-holder; rotate contacts if pressured |
| Under coercion | Duress PIN → decoy wallet; stall; recovery timelock buys intervention time |
| Planning ahead | Set dead-man switch + comfort threshold + verified contacts *today* |

**Next:** [Security Model](Security-Model.md) · [Using the Wallet](Using-the-Wallet.md) · [FAQ](FAQ.md)
