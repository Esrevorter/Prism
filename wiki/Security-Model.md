# Security Model & Honest Limits

*Who we defend against, what actually protects you, and — most importantly — what we do NOT claim. Binding spec: [`spec.md` §11](../spec.md).*

---

## Guiding principle

A privacy product that oversells its guarantees creates two harms: users make risky decisions based on false comfort, and the whole field earns deserved cynicism. So this page is written differently from typical security marketing: every defense comes with its residual risk stated plainly.

## Adversaries we assume, and what stops them

| Threat | What happens on Prism | Residual risk (honest) |
|---|---|---|
| **Double-spend attempt** | Every spend publishes a unique *key image*; a second spend of the same coin shows the same image → consensus rejects it outright (E1). | None at protocol level; UI shows depth confidence anyway. |
| **51% reorg / chain reversal** | 10-block spend maturity + depth-based confidence in the wallet; exchanges advised ≥30 blocks; channel closes protected by penalty transactions (E2). | Deep reorgs can reverse very-recent confirmations — normal PoW risk, priced into confirmation UX. |
| **Transaction graph analysis** | Amounts hidden (commitments), senders hidden (rings ≥16, CLSAG), receivers one-time (stealth), origin IP hidden (Dandelion++). Analyst sees per-tx ≤1/16 odds, refreshed each spend. | Global passive adversaries with long horizons and quantum-scale compute degrade ring anonymity over decades — acknowledged, not hand-waved (§11.2). Mass-decoy attacks mitigated by recency-weighted sampling with age floors. |
| **Stolen device (theft)** | Local key share is enclave-wrapped and biometric-gated; one below-threshold share is mathematically useless for 2-of-3+/3-of-5 schemes (E8). | Malware that reads live wallet memory is out of scope beyond OS protections — stated, not promised. |
| **Lost device** | Social recovery via quorum of your own shares/contacts; **72 h timelock**, loud warnings to all devices, any stale-share holder can cancel; then proactive re-sharing revokes old shares (E9). | Collusion of a full quorum of malicious contacts + timing luck is the main attack path — mitigated by requiring ≥1 device share in recovery subsets (user-configurable), still studied as R3. |
| **Coercion / duress** | Duress PIN opens a plausible decoy wallet; forced recovery still takes 72 h in visible alarm state; optional "panic lock" freezes sends above comfort threshold even manually (E10). | No system defeats a determined armed adversary at the meat-puppet layer; decoys reduce incentive, delays create intervention windows. |
| **Sanctioned inbound funds** | They arrive; local risk label flags them; spending them doesn't expose unrelated outputs. Flagging ≠ chain blacklist (E17). | Choosing to cooperate is the user's disclosure decision — provenance circuits exist exactly for that ([Prism Protocol](The-Prism-Protocol.md)). |
| **Malicious smart-contract-style traps** | v1 has no general contract VM — nothing to approve blind. The simulator previews every send before signing. | Fiat-quote oracles are display-only with slippage guards; phishing still possible socially — fraud model assists, humans decide. |
| **Supply-chain attacks (models, updates)** | Signed model bundles, transparency log of hashes, pinned versions; unsigned refuses to load (E16). Poisoned federated updates countered by clipping + robust aggregation + reputation decay (E15). | Signing-key compromise would be serious — key custody and ceremony design covered in audits. |
| **Broken ZK circuit post-release** | Circuit IDs versioned; verifiers reject deprecated IDs; anchored proofs carry their ID so affected disclosures are auditable (E6). | Pre-mainnet audits by ≥2 external firms for v1 circuits; separate per-release circuit audits. |

## The failure-mode catalog

Spec §11.1 enumerates eighteen concrete scenarios (E1–E18) with handling — from decoy exhaustion (E4) and denylist disputes (E5) to NL miscompilation ("send 50" vs "$50", E13) and LLM hallucinated explanations (E14). When auditing or threat-modeling, read [that table directly in spec.md](../spec.md); every row maps to code paths and tests.

## What we explicitly do NOT claim

- ❌ **"Untraceable to anyone."** Against a global passive adversary recording everything forever, ring-based systems weaken over long time horizons. We mitigate; we don't promise omniscience-proof anonymity.
- ❌ **"Quantum-safe."** Discrete-log assumptions (Ed25519, Pedersen, CLSAG) are broken by a large fault-tolerant quantum computer. Migration path is research area R1; today's design is classically-hard only.
- ❌ **"Recallable disclosures."** Expire-and-rotate, nothing more (Decision D3) — see [Prism Protocol](The-Prism-Protocol.md).
- ❌ **"Compromise-immune endpoints."** Your device and your choices (seed mode! phishing!) remain the weakest links; the wallet exists to soften them, not delete them.
- ❌ **"Audited."** Not yet. Phase-1 code is pre-audit; mainnet gates on formal audits of consensus + RingCT plus ≥2 independent ZKP circuit reviews. Until then: reference implementation, test nets, zero real value.

## Defense-in-depth summary (what makes theft hard, layered)

```text
  attacker wants your coins ──▶ must pass ALL of:
    1. threshold of MPC shares        (single device stolen? below quorum = useless)
    2. biometric gate on local share  (enclave-wrapped, never exported)
    3. 72 h recovery timelock         (every honest share-holder can cancel)
    4. your explicit sign-off         (AI cannot sign; policy engine outside the LLM)
    5. consensus validity             (key images, range proofs, balance equations)
```

Each layer fails open toward *delay and visibility*, never toward silent loss. That's the "forgiving by default" principle expressed as a threat model.

## Bug bounty & disclosure

Program launches Phase 2. Until then: security issues → private report through repository SECURITY contact; expect acknowledgment within 72 h, coordinated disclosure up to 90 days. Fuzzing harnesses (chain parsing, MPC protocol) run continuously in CI — see [Testing & Quality](Testing-&-Quality.md).

---

**Next:** [Recovery & Inheritance Guide](Recovery-and-Inheritance-Guide.md) · [Monetary Policy Rationale](Monetary-Policy-Rationale.md)
