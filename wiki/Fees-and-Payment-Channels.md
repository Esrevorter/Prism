# Fees & Payment Channels

*Why Prism costs a tenth of a cent, how the fee policy actually works, and when to use instant channels instead. Binding spec: [`spec.md` §10](https://github.com/Esrevorter/Prism/wiki/spec).*

---

## The fee you'll almost always pay

**Base fee: 0.0001 PRSM — fixed.** Roughly $0.001 at launch economics. It is not an auction; it doesn't float with congestion; two transactions cost exactly twice one transaction. Fee uncertainty is treated here as a *UX bug*, and the fix is boring arithmetic instead of bidding psychology.

Optional **priority fee**: integer multiples of base, hard-capped at **10×** (≈ $0.01). A transaction offering more than 10× base is simply non-standard — nodes won't relay it. Why cap it? So no whale can outbid an entire mempool, and so "get mined now" stays a small predictable dial rather than a panic auction.

Inside that band, blocks pack by priority density while **dynamic block size** (median-last-10 scaling, soft target 2–4 MB, penalties beyond) absorbs bursts — meaning base-fee transactions still clear within ~2 blocks at normal load even when usage spikes.

### What the money pays for
Fees + block emission compensate miners securing the chain (see [Monetary Policy Rationale](https://github.com/Esrevorter/Prism/wiki/Monetary-Policy-Rationale)). Every PRSM sent in fees leaves circulation permanently (burned), slightly offsetting tail inflation during heavy use.

## Practical confirmation guide

| Situation | What to do | Cost | Wait |
|---|---|---|---|
| Everyday send | Base fee | 0.0001 PRSM | ≤ 2 blocks (~4 min) typical |
| In a hurry | Fast (≤10× base) | ≤ 0.001 PRSM | next block |
| Receiving | free | — | spendable after 10 confirmations (~20 min maturity rule) |
| Tips / micropayments | **channel** | ≈ 0 | instant |
| Big batch day | channel or plain sends, either way trivial | — | — |

Gas abstraction note: fees are paid in PRSM itself — there is no separate gas token to acquire, hoard, or run out of.

## Payment channels: Lightning-style, for people who don't say "Lightning"

**The idea:** lock a small amount on-chain once, then exchange unlimited payments off-chain with your counterparty, each instantly and essentially free; close whenever, settling final balances on-chain (2 base-fee transactions total for open+close).

```text
on-chain                     off-chain (instant, ~free)              on-chain
   │ open (fund 50 PRSM)        ┌─ tip → ─┬─ payout → ─┐        │ close
   ▼═══════════════════════◄═══╪═════════╪═══════════════════╪════►══▼
  tx #1                        both hold signed balance updates, newest wins
```

**Prism specifics:**
- Bidirectional; commitment-based like LN but carrying *private* PRSM — channel states stay confidential, closing looks like any other transaction.
- **Watchtowers:** register an encrypted penalty blob with a third party. If your counterparty vanishes or tries to broadcast an old state, the watchtower submits the latest correct one (unilateral close with delay), and cheating closes get *penalized* — the cheat's signature lets you claim the whole channel balance (E3).
- Channel funds inherit the same privacy guarantees as on-chain outputs; capacity opening uses your normal coins.

**When channels shine for the target user:** recurring collaborator payouts (a contractor you pay weekly), platform micropayments (fractions of a cent that would be silly on-chain), point-of-sale tips, subscription metering. One-time large transfers can just go on-chain — the base fee makes that cheap enough already.

Status: **beta (Phase 3)** — channel + watchtower logic is implemented and tested in `prism/l2/channels.py` (Schnorr-signed state versions, cooperative close at exactly 2 base fees lifetime, stale-close penalty sweep, encrypted penalty blobs; see `tests/test_l2_fees_channels.py` and `property/phase3_smoke.py`). Fee-predictability hardening (`prism/l2/fees.py`: priority lattice N·BASE with 1 ≤ N ≤ 10, deterministic quotes, fee-density block packing) is enforced alongside the existing code paths in `chain/params.py` and node acceptance rules. Remaining before GA: real CLTV adapter transactions on the node mempool and GUI wiring on Refraction ([Roadmap](https://github.com/Esrevorter/Prism/wiki/Roadmap)).

## Merchant corner: `prsmr:` invoices

Payment links/QRs can embed requested amount + memo (`prsmr:` URI). Customers pay through the normal send pipeline; merchants accepting recurring customers migrate those flows into channels for zero-cost settlement. Merchant toolset ships Phase 4.

---

**Next:** [Using the Wallet](https://github.com/Esrevorter/Prism/wiki/Using-the-Wallet) · [Chain Rules (technical)](https://github.com/Esrevorter/Prism/wiki/Chain-Rules-and-Monetary-Policy) · [FAQ](https://github.com/Esrevorter/Prism/wiki/FAQ)
