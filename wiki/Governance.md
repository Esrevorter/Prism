# Governance

*Who decides what on Prism, how each power is checked, and where the honest seams are. Binding sources: [`spec.md` §12–§14](../spec.md) (D5 especially).*

---

## The principle

Prism separates three things that centralized crypto keeps fusing: **the protocol** (rules enforced by code), **maintenance** (people writing that code), and **funding** (a stream from future emission). Each gets its own accountability mechanism, and none of them gets a kill switch over your money.

## Layer 1 — Consensus rules: code + votes, no executives

- Monetary parameters (21M cap, halving cadence, tail rate, base fee) are **consensus-fixed**; changing any of them requires a hard fork with miner version-bit voting (181-block windows), ≥90 days' announced notice, and 2-week grace. Nobody can quietly print.
- Upgrades are validated against a frozen-consensus testing rig before activation to prevent bricking (E18).
- Node operators decide which software they run; miners decide which chain to extend. Standard PoW politics, deliberately unglamorous.

## Layer 2 — Maintenance & funding: the dev fund, audited in daylight

Development is paid from a fixed small percentage carve-out of *each block reward*, released only via **on-chain milestone vesting**. Consequences you can verify:

- Every dev-fund shard traces to published emission math — no side allocations exist to discover later.
- Milestones and releases are public artifacts; the community can inspect whether claimed work matches shipped code.
- Pre-mainnet, the carve-out design itself gets audited (Phase 4 gate).

The founding team also steers architecture through the **RFC/amendment process**: proposed changes to `spec.md` circulate as numbered RFCs; the decision log (§14) is append-only — when something like RFC-0001 corrected the halving interval to 315,360 blocks, it was recorded as an amendment, not a silent edit. Disagree with a direction? Fork the MIT-licensed code; that's the ultimate governance check.

## Layer 3 — Denylist curation: the Compliance Council (Decision D5)

The most dangerous power in any privacy coin is deciding "who's sanctioned." Prism's containment design:

1. An elected **Prism Compliance Council** — 7 seats, staggered 18-month terms, public signing policy — signs the *official* weekly accumulator root with ≥5-of-7 signatures.
2. **The chain never enforces it.** Denylist roots appear in block headers as reference data; consensus validates math, not politics. Funds from listed sources still arrive and remain spendable ([Security Model](Security-Model.md) E17).
3. **Parallel lists are first-class.** Any organization may publish roots; disclosure proofs reference a specific root hash, and verifiers choose which list(s) they accept. Users/verifiers can pin any list — a rogue council changes nothing for those who don't consume its roots.
4. **Misbehavior is checkable:** every root ships with named signatories' signatures; publication is mandatory and auditable after the fact.
5. Election mechanics, slashing/removal rules, and detailed listing policy are research area **R7** — explicitly unfinished rather than quietly defaulted. That honesty is part of the design.

## Layer 4 — AI safety governance

- Model bundles: signed, transparency-logged hashes, pinned versions; wallets refuse unsigned models (E16).
- Federated learning: per-round opt-in, DP accounting, robust aggregation, emergency global FL pause flag in governance config (E15).
- Model cards published per release; the anti-hallucination boundary (simulator owns numbers) is a product invariant, not a preference ([AI Layer](AI-Layer-Explained.md)).

## What governance is NOT on Prism

- ❌ No company-held keys or recovery leg (D4) — nothing to subpoena at the vendor.
- ❌ No token-holder voting over consensus parameters — supply holders vote with nodes/miners like everyone else; there's no governance-token class.
- ❌ No foundation ability to freeze, claw back, or blacklist on-chain.
- ⚠️ Not a DAO theater either: elections, councils, and vesting are real-world institutions with published policies; the code limits their reach rather than granting it.

## Regulatory posture ([`spec.md` §12](../spec.md))

Foundation position: **privacy software, non-custodial** — no company-held keys, no universal access. Selective disclosure is the compliance bridge: users satisfy tax/audit obligations *themselves*, without trusting intermediaries. Code launches under an OSI permissive license (MIT preferred; BSD-2 alternative pending final legal pick — non-blocking). Entity formation deferred pending counsel (research area R4). Hosted conveniences (watchtowers, invite relays) carry age/ToS gates; the core protocol stays permissionless.

## How to hold this accountable

- Read the spec sections cited above; every governance claim maps to code paths and tests.
- Watch the append-only decision log for new D-items and amendments.
- Run your own node; pin your own denylist roots; compare official roots against parallel publishers.
- Contribute or challenge via RFCs ([Contributing](Contributing.md)).

---

**Next:** [Roadmap](Roadmap.md) · [The Prism Protocol](The-Prism-Protocol.md) · [FAQ](FAQ.md)
