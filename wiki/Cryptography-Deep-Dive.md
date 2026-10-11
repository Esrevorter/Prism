# Cryptography Deep Dive

*From field arithmetic to ring signatures, range proofs, and SNARK circuits — what each module implements, which papers it follows, and how we know it works. Code: `prism/crypto/`, `prism/zk/`.*

---

## Stack overview (bottom-up)

```text
field.py      scalars mod ℓ (Ed25519 group order), canonical 32-byte LE encoding
edwards.py    Ed25519 curve points, extended twisted coords, [k]P, add/sub, compress
hashing.py    domain-separated Keccak-256 with string tags ("PrismXylo…")
   └── pedersen.py   commitments C = v·H + r·G, homomorphic add/sub/neg
        ├── stealth.py     one-time addresses via ECDH between spend/view keys
        ├── clsag.py       CLSAG linkable ring signatures + key images
        └── bulletproof.py Bulletproofs+ 64-bit range proofs (setup-free)
zk/           separate stack: BN-scalar field, PLONK-style polynomial IOP,
              disclosure circuits (provenance/solvency/income/reserve/clean-exit)
mpc/          threshold signing over the same key material:
   sharing.py Shamir split/verify/refresh · gg20.py GG20 ECDSA rounds
   frost.py   FROST EdDSA rounds · recovery.py quorum + timelock state machine
```

Everything is **pure Python standard library** (`int` bignum arithmetic, `hashlib`): no third-party crypto dependencies to audit or trust. This is a *reference implementation* — correctness and clarity prioritized over speed; production nodes will re-implement in Rust/C against these test vectors.

## 1. Curves & hashing

- **Ed25519** for everything on-chain (commitments, rings, stealth): base point `G`, cofactor-cleared ops, extended coordinates `(X:Y:Z:T)` for complete addition law (no exceptional cases → no side-channel-prone branches).
- **Nothing-up-my-sleeve generators:** `H` derived by hash-to-curve from `G`'s compressed encoding with a Prism tag — you can reproduce it and verify nobody picked a weak `H` (which would break Pedersen binding).
- **Domain separation everywhere:** every challenge hash prefixes an ASCII tag (`TAG_X`, `TAG_Y`, …) so a hash computed for one protocol purpose can never be replayed in another. Sounds pedantic; has saved real protocols.

## 2. Pedersen commitments (`pedersen.py`)

`C(v, r) = [v]·H + [r]·G`. Perfectly hiding (any `v` consistent with some `r`), computationally binding (opening requires solving discrete log). Homomorphic: `C(v₁,r₁) + C(v₂,r₂) = C(v₁+v₂, r₁+r₂)` — the chain's balance check is a pure curve equation:

```text
Σ input commitments − Σ output commitments == [fee]·H + [blinder]·G
```

Value conservation proven without any value revealed. The fee pseudo-input carries the known `[fee]·H` term.

## 3. Stealth addresses (`stealth.py`)

Recipient publishes `(A = [a]·G, B = [b]·G)` — spend pubkey, view pubkey. Sender picks random `r`:

```text
one-time address P = H(r·A)·G + B        with shared secret r·A = a·(r·G)
```

Only the recipient computes `H(a·R)·G + B` while scanning blocks with their view key `b`; outputs unlock with per-output private key `H(r·A) + a`. Result: unlinkable one-time destinations from a single public address. Sub-addresses use the same DH trick with index-derived offsets.

## 4. CLSAG ring signatures (`clsag.py`)

The Monero-lineage **CLSAG** (Short Linkable Spontaneous Aggregate Group) scheme, aggregated across all inputs of a transaction:

- For each input, a ring of decoy pubkeys; signer knows only one secret.
- Key image `I = [x]·Hp(P)` (per real key) makes double-spends detectable while revealing nothing about ring position.
- Aggregation: one joint challenge binds all rings; signature size grows linearly-but-small (per-member ~64 bytes, plus one aggregate scalar set) instead of naive per-ring signatures.
- Amount blinder aggregation ties each input's commitment opening into the same proof so signers can't mix mismatched secrets.

Verification = recompute challenge chain, check aggregate Schnorr equations, done. Property tests cover completeness (honest sigs verify), linkability (double spend detectable), and signer ambiguity (verifier can't distinguish real member statistically better than guessing).

## 5. Bulletproofs+ range proofs (`bulletproof.py`)

Proves `∃v ∈ [0, 2⁶⁴), r : C = [v]·H + [r]·G` with **no trusted setup**, ~352 bytes (11×32-byte wire fields per `RangeProof`). Construction (single-commitment specialization, n = 64):

```text
bit-vector a of v; nonces α, ρ, τ₁, τ₂, μ, σ₁, σ₂
A=[α]G, Ŝ=[α]H+[ρ]G ; challenges y,z from transcript
l₀ = a − z·1            r₀ = y_vec = (y,…,yⁿ)
l₁ = a⊙pow2 + z(a−pow2) r₁ = pow2⊙y_vec − δ·1 ,  δ = z−z²
t₁=⟨l₀,r₁⟩, t₂=⟨l₁,r₁⟩; T₁,T₂ blind them; γ, d̂ fold cross terms
tx̂ = ⟨l(x),r(x)⟩ at x ; S₁,S₂ commit nonces; IPA folded via
L,R halving rounds OR (this specialization) Schnorr-response form:
  sxr = σ₁+e·ρ, sxo = σ₂+e·μ ⇒ implicit L₁=[σ₁]G, L₂=[σ₂]G
Verifier checks two point equalities (V1/V2), e.g.
  V2: [tx̂]H + [taux]G == [z²]C + [μ̂]H + [d̂]G + x·T₁ + x²·T₂ + [δ]Ŝ − [z]A
```

Completeness/soundness/malleability tests run against random values and blinders; the verifier is exactly the two EC equalities — cheap for every node, forever.

## 6. Disclosure SNARKs (`zk/`)

Reference-grade PLONK-style polynomial IOP over a BN-family scalar field: selection/permutation arguments, quotient decomposition, Fiat-Shamir transcript, opening checks. Five circuits (source provenance, balance solvency, income attribution, reserve attestation, clean exit) compile to this common constraint format; statements encode public inputs canonically (fuzz-tested — malleable statement encodings are how "same proof, different meaning" bugs happen). Proof sizes constant per circuit regardless of witness size — the D2 rationale in action. Reference implementation trades prover speed for reviewability.

## 7. Threshold keys (`mpc/`)

- **Shamir sharing** with verifiable commitments (every share provably consistent with the public key); **proactive refresh** rotates shares without changing the key (90-day cadence).
- **GG20-style ECDSA** and **FROST-style EdDSA** round protocols implemented as message state machines — pausable, abort-safe, resumable ceremonies (spec §7.1).
- **Recovery coordinator** (`recovery.py`): quorum check → 72 h timelock → cancellation path for stale-share holders → re-sharing that revokes everything old. Pure logic layer; transports (QR, invite links, EE2EE channels) sit above it in the wallet.

## Known limitations (pre-audit honesty)

- Constant-time discipline: reference code uses Python `int` arithmetic — timing side-channels are acceptable *here* (test vectors) but the Rust/C production ports must implement blinding/regular algorithms. Flagged for the Phase-2 port spec.
- The PLONK instantiation is educational-grade (small domains, no KZG ceremony integration yet); circuits' *constraint logic* is the auditable artifact, the IOP plumbing gets hardened pre-mainnet.
- No formal verification yet; property testing + fuzzing only ([Testing & Quality](Testing-&-Quality.md)). Audits gate mainnet ([Security Model](Security-Model.md)).

---

**Next:** [Architecture Overview](Architecture.md) · [How Privacy Works](How-Privacy-Works.md) (the non-math tour) · [Testing & Quality](Testing-&-Quality.md)
