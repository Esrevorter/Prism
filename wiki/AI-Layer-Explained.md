# AI Layer Explained

*What Prism's intelligence layer does, what it refuses to do, and the walls that make "the AI can't steal your money" a cryptographic statement rather than a promise. Binding spec: [`spec.md` §8](../spec.md).*

---

## The one-sentence version

The AI turns *"send $50 to Alex for the mastering session, keep it private"* into an exact, safe, explained transaction — and then **stops before signing**, because everything irreversible still requires you.

## Why on-device is non-negotiable

A financial assistant that phones home is a surveillance device with good manners. Prism's deployment constraint: **all inference runs on your hardware**. No utterances, balances, or transaction content leave the device except standard P2P protocol traffic. Cloud fallback exists only as an explicit opt-in, redacted, and *never* used for signing-related reasoning. This isn't a privacy nicety — it's what makes the layer compatible with the rest of the design.

## The four components

| Component | What it does for you | How it works (short) | Size budget |
|---|---|---|---|
| **NL Intent Compiler** | Understands plain requests → strict JSON *Intent* | Distilled instruction-tuned LLM (≤3B params, INT4 quantized) + **deterministic grammar validator**: model output must parse into the strict Intent schema or the wallet falls back to a form UI | ≤2 GB RAM peak |
| **Fraud Detector** | Calmly flags suspicious destinations/patterns before you sign | Gradient-boosted trees + small sequence model over address-behavior features, plus a rule overlay for known scam patterns; local labels only | ≤50 MB |
| **Tx Simulator** | Shows exactly what will happen, in English, *before* anything is signed | Deterministic execution against a local chain snapshot + template explainer. Numeric claims come from here, never from the LLM | ≤200 MB |
| **UX Optimizer** | Adapts the interface to your habits (surfaces common actions, hides rarely-used depth) | On-device contextual bandit; learns nothing about you that leaves the device | ≤5 MB |

### The anti-hallucination boundary

This is the most important sentence in the page: **numbers in previews come from the simulator, not the language model.** The LLM may phrase explanations, but every amount, fee, and balance shown to you is computed deterministically. A hallucinating model produces wrong *prose*, never wrong *terms*.

### Ambiguity is handled by asking, never guessing

*"Send 50 to Alex"* — 50 dollars? 50 PRSM? Which Alex? The compiler never silently resolves ambiguity: unknown contact → one clarifying chip ("Did you mean Alex Rivera?"), fiat amounts → live oracle quote with a locked 60-second rate window and max-slippage guard. If parsing is even slightly off-schema, you get the form UI instead. Acceptance bar: ≥95% correct compile-or-safe-fallback on a 20-intent benchmark suite.

---

## Autonomous agents: autopilot with seatbelts

Recurring chores (round-ups, DCA, royalty-split reminders) run under **explicit permission grants**:

```jsonc
{ "agent": "auto_dutcher.v1", "capability": "buy_prsm_fiat_drip",
  "constraints": { "max_per_month_shard": 10000000000,        // ≤ $100/mo-ish
                   "destinations_whitelist": ["self_subaddr:dca"] },
  "veto_window_hours": 24, "valid_until": "..." }
```

Design rules, each with a reason:

1. **Allowlisted verbs only** (`transfer.self`, `buy_drip`, `channel_topup`, `report_generate`) — no arbitrary contract calls in v1. Smaller attack surface by construction.
2. **Policy enforced outside the LLM.** Grants compile to a policy engine checked by the *signer* at signing time. Even a fully compromised agent process cannot exceed grant bounds — the gatekeeper isn't the model.
3. **24-hour veto window** on every proposal by default; instant execution only for whitelisted recipients under your personal "comfort threshold." Proposals arrive in a quiet digest, not pings.
4. **Tamper-evident audit log.** Every action is recorded in a hash-chained local log with a plain-English explanation ("Bought 12.4 PRSM @ avg $4.03 under your $100/month plan"). One tap revokes any grant.

## Federated learning: getting smarter without peeking

Fraud patterns evolve faster than any team ships updates, so wallets learn collectively — **without user data leaving devices**:

- FedAvg aggregation with **client-side differential privacy** (ε ≤ 1.0 per round, Gaussian mechanism), norm clipping, and median-based robust aggregation against poisoning.
- Contributions routed through a mix network; rounds require ≥256 contributors; participation is strictly **opt-in per round**.
- v1 rewards are altruistic + reputation only — deliberately no token incentives, to avoid Sybil/account-farming surfaces (revisit post-launch).
- Governance: model cards published per release; emergency global FL pause flag; model bundles **signed**, hash-logged, and pinned — unsigned models refuse to load (supply-chain defense E16).

## What the AI layer categorically never does

- ❌ Derives keys, sees private keys or MPC shares, or sits inside the signing quorum.
- ❌ Signs or broadcasts anything irreversible without explicit informed confirmation (biometric gate) — except pre-authorized scoped agent actions, which always carry their veto window.
- ❌ Sends your financial content anywhere. On-device or nothing.
- ❌ Makes numeric claims from its own generation. That's the simulator's job.

If marketing copy ever promises more than this list allows, the copy is wrong — [Governance](Governance.md) covers how to hold us to it.

---

## In code

| Piece | Location | Status |
|---|---|---|
| Intent schema + grammar-validated compiler | `prism/wallet/intent.py` | ✅ Implemented, tested |
| Agent grants, veto windows, hash-chained log | `prism/wallet/agent_auth.py` | ✅ Implemented |
| Fraud scoring prototype | `prism/ai/fraud.py` | ✅ Prototype (Phase-2 GA) |
| Deterministic simulator + explainer | `prism/ai/simulator.py` | ✅ Implemented |
| DP federated-update client | `prism/ai/fl_client.py` | ✅ Implemented |
| Actual distilled LLM weights | shipped separately, signed | 🗓️ Phase 2 |

**Next:** [Using the Wallet](Using-the-Wallet.md) · [Security Model](Security-Model.md)
