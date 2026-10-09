# How Privacy Works

*What an outside observer actually sees on Prism, and the cryptography that makes each hiding work. Binding spec: [`spec.md` §5](../spec.md).*

---

## First: what does a stranger see?

A full Prism transaction, as visible to anyone running a node, looks roughly like this:

```jsonc
{
  "inputs": [ {
      "ring_members": ["out_ref_1", "…", "out_ref_16"],   // which is real? hidden
      "key_image":    "a3f1…",                            // spend marker, no identity
      "proof_clsag":  "…"                                 // signature, signer unknown
  } ],
  "outputs": [ {
      "stealth_address": "7d02…",                         // never used again, ever
      "commitment":      "C = v·H + r·G",                 // amount: a curve point
      "range_proof":     "…"                              // proves 0 ≤ v < 2⁶⁴ only
  } ],
  "fee_shard": 10000                                       // fee is the ONLY visible number
}
```

**Visible:** that *some* coins moved, a fixed-size ring of decoys, opaque commitments, one small fee.
**Hidden:** who sent, who received, how much, and (via Dandelion++ relay) which IP originated it.

Now, layer by layer.

---

## 1. Hidden amounts — Pedersen commitments + Bulletproofs+

The value in every output is sealed inside a **commitment**: `C = v·H + r·G`, where `v` is the amount and `r` is a random blinding factor only the owner knows. Like a sealed envelope welded shut: nobody can peek, and even the owner can't swap contents later because opening requires presenting exactly the `(v, r)` pair that closes the equation.

Two magic properties make a ledger out of envelopes:

- **Homomorphism:** you can add commitments without opening them. The chain checks *"sum of inputs − sum of outputs = fee commitment"* purely on curve points — value conservation with zero revealed values. Cheat by creating coins from nothing and the equation fails; every node catches it.
- **Range proofs (Bulletproofs+, 64-bit):** a sealed envelope could hide `v = -1,000,000`, which would let an attacker mint infinite money via subtraction. A range proof convinces verifiers that the hidden `v` lies in `[0, 2⁶⁴)` — without revealing `v`. Prism uses the Monero-lineage Bulletproofs+ construction: logarithmic-size inner-product argument, **no trusted setup**, ~352 bytes per proof for our single-output specialization (`crypto/bulletproof.py`).

Per Decision D2, the amount layer deliberately stays SNARK-free — Bulletproofs need no ceremony and shrink every block.

## 2. Hidden senders — ring signatures (CLSAG)

When spending, your input is placed in a **ring** with ≥16 (target 32) decoy outputs sampled from the whole unspent set. You produce one aggregate signature (CLSAG) that proves: *"the holder of the key to exactly one member of this ring authorized this spend"* — verifiers cannot tell which member. All 16 look equally guilty; the best an analyst gets is a 1-in-16 guess, refreshed every transaction.

Decoy sampling matters more than people realize: if rings pick decoys carelessly (e.g., always recent outputs), statistical fingerprinting re-links them. Prism samples from a **recency-weighted, popularity-aware distribution with age-bucket floors** so old outputs stay plausible decoys even as the chain grows.

One subtlety: how do nodes stop someone double-spending if inputs are anonymous? **Key images.** Each output's private key generates a unique public marker when spent; reuse the same coin twice and its image appears twice — instant rejection. One secret per coin → one image forever; the image reveals nothing about which ring member produced it. Sender-hiding and double-spend-proof coexist exactly through this trick.

## 3. Hidden receivers — stealth addresses

You publish one payment address, but it never appears on-chain. To pay you, the sender performs a one-time Diffie-Hellman handshake using your public **view key**: they derive a fresh random one-time address (which only you, scanning with your view key, can recognize and unlock). Result: the blockchain contains a brand-new address per payment — no reuse, no clustering, no "address book of the world" analysis. Your wallet scans blocks offline with the view key and reconstructs its own incoming payments while everyone else sees noise.

Two keys split duties: the **spend key** authorizes outgoing funds; the **view key** only reads incoming ones. This split is also the foundation of selective disclosure ([next page](The-Prism-Protocol.md)).

## 4. Hidden origin — Dandelion++ relay

Even perfect crypto leaks if the first node you broadcast from knows your IP. Prism relays transactions in two phases: for the first 5–8 hops, a transaction travels a secret random **stem** path (each peer tells only its one successor); then it **fluffs** normally to everyone. An observer watching the flood can't reliably trace back to the origin — the stem hid the moment of arrival at the flooding point.

## 5. What privacy does *not* hide (honest limits)

- **Fees** are visible (fixed base 0.0001 PRSM + optional priority ≤10×).
- **Timing & volume:** observers see block cadence and how much data moves. Correlating "Maya's studio got paid" with "someone paid something right after" is metadata heuristics — mitigated by batching/lateness norms in the wallet, not eliminated by math.
- **A global passive adversary** that records everything forever and has quantum-scale analysis tools degrades ring sizes over long horizons. Our threat model states this plainly ([Security Model](Security-Model.md)).
- **Compelled disclosure:** if *you* hand over your view key or a proof, privacy ends by design — that's the user-controlled half of "clarity on your terms."
- **Endpoint compromise:** malware reading your live wallet memory is out of scope beyond OS-provided enclave protections. We say so rather than pretend otherwise.

---

## The primitives in code

| Concern | Module | Scheme |
|---|---|---|
| Amount secrecy | `prism/crypto/pedersen.py` | `C = v·H + r·G` |
| Amount sanity | `prism/crypto/bulletproof.py` | Bulletproofs+ inner-product argument, 64-bit |
| Sender secrecy | `prism/crypto/clsag.py` | CLSAG ring signatures, key images |
| Receiver secrecy | `prism/crypto/stealth.py` | One-time DH addresses |
| Domain-separated challenges | `prism/crypto/hashing.py` | Keccak-style tagged hashes |
| Curve arithmetic | `prism/crypto/edwards.py`, `field.py` | Ed25519 group, scalars mod ℓ |
| Relay secrecy | (daemon, Phase 1+) | Dandelion++, epoch 64 blocks |

All pure Python, property-tested, and fuzz-checked — see [Cryptography Deep Dive](Cryptography-Deep-Dive.md) and [Testing & Quality](Testing-&-Quality.md).

---

**Next:** [The Prism Protocol: Selective Disclosure](The-Prism-Protocol.md) · [Security Model & Honest Limits](Security-Model.md)
