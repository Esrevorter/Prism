# Monetary Policy Rationale

*Why Prism's money rules look the way they do: 21M cap, halvings, tail emission, zero pre-mine, dev fund from future issuance. Binding spec: [`spec.md` §4 + Decision D1](../spec.md).*

---

## The parameters (consensus-fixed)

| Parameter | Value |
|---|---|
| Ticker / base unit | PRSM / **shard** = 1e-8 PRSM (8 decimals) |
| Max supply | **21,000,000 PRSM** hard cap via emission curve |
| Emission shape | Block reward **halves every 2 years** (315,360 blocks at 120 s) until approaching the cap, then a permanent **≈0.6%/year tail** continues forever |
| Pre-mine | **Zero.** Nobody — founders, investors, foundation — receives any genesis allocation |
| Team funding | Fixed small % carve-out of *each block reward* to a dev fund, released against **on-chain milestone vesting** |
| Block time | 120 seconds, adaptively retargeted over a 60-block window |

All of this is computed in `prism/chain/emission.py` and verifiable by anyone with one command:

```console
C:\> prism emit --heights 0,100,315360
```

## Why these choices (the reasoning, not just the numbers)

### Halving curve to 21M — the Bitcoin-shaped scarcity anchor
A fixed, predictable supply schedule is what makes a monetary asset *auditable in advance*: you can compute tomorrow's total supply today, which no fiat system and no discretionary treasury can offer. The halving cadence front-loads distribution while miners are essential to bootstrap security, then tapers gracefully. Choosing Monero-style "tail after cap" rather than Bitcoin-style "fees only after cap" comes down to one question we refused to leave unanswered: **what keeps miners honest when new coins stop existing?**

### Tail emission ≈0.6%/yr — paying for security forever (D1)
If block rewards hit exactly zero, chain security depends entirely on transaction fees. That's a real failure mode: fee revenue fluctuates with usage; a low-usage period plus a high-hashrate adversary equals an insecure chain — and worse, the market value of *all* coins becomes leveraged against a thin, volatile fee flow. A small, fixed, non-discretionary inflation tail (~0.6%/yr, consensus-coded, never adjustable by anyone) guarantees the security budget never collapses. Monero has run this experiment successfully for years; Prism adopts it deliberately as **Decision D1**.

The honest tradeoff: PRSM is *not* perfectly deflationary like BTC. After the cap, holding PRSM dilutes ~0.6% per year relative to new issuance. We chose continuous provable security over absolute scarcity aesthetics — and said so here rather than burying it.

### Zero pre-mine — the trust you can verify at genesis
Every coin that allocates value to insiders starts with two questions nobody can answer well: *how much did they get?* and *when will they dump?* Prism answers both with code: the genesis block mints nothing (`prism genesis` prints `"premine_shards": 0` — asserted, not assumed). Founders eat the same dog food, bought or mined like everyone else.

### Dev fund carved from future emission — funded but not privileged
Software needs maintainers; "no team funding" is just pre-mining by another name with worse transparency. The chosen design: a fixed small percentage of each block's reward flows to a dev-fund address, and releases require **milestone vesting recorded on-chain** — public, auditable, capped, and earned. No genesis grant, no back-room allocation, and the community can verify every shard against the emission formula.

### Fee policy belongs to economics too
Base fee fixed at 0.0001 PRSM with priority capped at 10× isn't just UX — it's monetary-policy-adjacent: it makes the cost of using the currency *predictable*, which is the precondition for micro-payments and channels ([Fees & Payment Channels](Fees-and-Payment-Channels.md)). Dynamic block size (median-last-10, soft target 2–4 MB) absorbs demand spikes without letting the spot price of blockspace spike with them.

## Supply math, visualized

```text
PRSM/block
   │\
   │ \        halving every 2 years (315,360 blocks)
   │  ╲__
   │     ╲____
   │          ╲─────────___
   │                       ────────────────  ← tail: constant ≈0.6%/yr, forever
   └────────────────────────────────────────────▶ height
   0            approach 21M cap
```

Cumulative supply converges toward 21M; thereafter balance grows linearly with the tail. Run `prism emit --heights …` for exact figures at any height — the CLI is the ground truth, this page is commentary.

## Comparisons people ask for

| | Bitcoin | Monero | **Prism** |
|---|---|---|---|
| Cap | 21M | none (tail) | 21M **+ tail** |
| Post-cap security | fees only | tail emission | tail emission |
| Premine | none | none (fair launch) | none, **explicitly asserted at genesis** |
| Team funding | donations/orgs | (community) | dev-fund carve-out w/ on-chain vesting |
| Fees | auction | auction-ish | **fixed base + capped priority** |

---

**Next:** [Chain Rules & Monetary Policy](Chain-Rules-and-Monetary-Policy.md) (the technical side) · [Glossary](Glossary.md)
