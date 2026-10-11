# Running a Node

*How to operate Prism infrastructure when the network exists — node classes, why your choice matters for privacy, and what's already runnable today. Binding spec: [`spec.md` §4](../spec.md); code: `prism/chain/`.*

---

## ⚠️ Reality check (October 2026)

There is no live P2P network yet — daemon networking lands in Phase 1 with the **Refraction** testnet. What runs *today*: the complete rules engine via the CLI (`prism genesis`, `prism mine --blocks N`, `prism emit`, `prism params`) and the full validation suite. This page documents the operating model so you can prepare; commands marked 🚧 activate with the daemon.

## The three node classes

| Class | Validates? | Serves decoys? | Disk | Who runs it |
|---|---|---|---|---|
| **Full** | ✅ every rule | ✅ entire history | whole chain | Everyone who cares about privacy; wallets default here |
| **Pruned** | ✅ | ❌ old outputs discarded | small | Space-constrained users; fine for sending/receiving *with caveats* |
| **Indexer** | ✅ + view-key-scoped index | optional | full + index | Privacy-conscious accountants/exchanges running disclosure verifiers |

### Why pruned nodes are a privacy decision, not just a disk one

Ring signatures need **decoys** — old outputs from other people's wallets. A pruned node literally cannot supply good historical decoys, which is why wallets default to archival/full peers (edge case E4): connecting to pruned peers degrades your anonymity set silently. Practical rules:

- Run your wallet against a **full node** — ideally your own.
- If you must run pruned for space, understand you're helping decentralization while weakening your own decoy pool; the wallet will steer around it but prefers full peers.
- Decoy sampling uses recency-weighted distributions with age-bucket floors, so long-dormant outputs remain plausible — that math only works if someone keeps the history. You being that someone matters.

## Hardware guidance (planned targets)

```text
Full node   : 2 CPU cores, 4 GB RAM, SSD w/ headroom for UTXO growth, stable uplink
Indexer     : same + per-viewkey index storage; consider NVMe for scan throughput
Mining      : RandomX scales with general-purpose CPUs; ~2–4 GB RAM per thread context
              (memory-hard by design — GPU/ASIC advantage structurally limited)
```

Storage-rent mechanics for UTXO growth were explicitly **rejected for v1** (§10.1: UX hostility outweighs savings) — plan disk for growth, revisit at scale.

## Operating commands 🚧 (daemon phase)

```bash
prismd --network refraction                 # sync & validate
prismd --rpc-bind 127.0.0.1:19734          # local RPC for your wallet
prismd --restricted-rpc                     # public-facing: hide diagnostics
prism-cli status                            # height, difficulty, mempool, peer map
```

Today's equivalents (fully working):

```console
C:\> prism genesis --network refraction    # inspect deterministic first block
C:\> prism mine --blocks 5                 # exercise the validation pipeline locally
C:\> python -m pytest prism/tests/test_chain.py -q   # the rules, verified on your machine
```

## Network hygiene that protects your users

- **Dandelion++:** stem/fluff relay is default-on; don't expose topology telemetry publicly (`--restricted-rpc`).
- **Fee policy enforcement:** relay transactions honoring base-fee floor and priority ≤10× cap; >cap is non-standard (E-policy in `node.py` acceptance rules).
- **Denylist roots are pass-through data:** headers carry them; consensus ignores their content ([Governance](Governance.md) D5). Never build auto-enforcement into your node — that's how "privacy coin" dies socially.
- **Version voting:** signal upgrades deliberately; windows are 181 blocks with ≥90-day notice policies — read release notes before flipping bits.

## Watchtowers (Phase 3+, channel guardians)

Optional service role: hold encrypted penalty blobs for payment channels, submit correct latest state if a counterparty vanishes or cheats (E3). Runs as a separate light service beside a full node; blobs stay encrypted — watchtower operators learn nothing about balances. Age/ToS gates apply to hosted instances; self-hosting always allowed.

## Becoming useful before mainnet

1. Run the reference tests on your hardware — portability findings are contributions.
2. Join Refraction testnet when announced ([Roadmap](Roadmap.md)) and keep a **full** node.
3. Report sync/validation anomalies with logs (issue label `node`).

---

**Next:** [Chain Rules & Monetary Policy](Chain-Rules-and-Monetary-Policy.md) · [Testing & Quality](Testing-&-Quality.md) · [Contributing](Contributing.md)
