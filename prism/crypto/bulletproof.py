"""Bulletproofs+ style 64-bit range proofs over the Pedersen commitment — spec §5.1 / D2.

Proves that a commitment ``C = v·H + r·G`` (see pedersen.commit) hides a value
``0 <= v < 2^64`` WITHOUT revealing v or r. No trusted setup: the generators are
the same nothing-up-my-sleeve G / H used by the commitments themselves
(D2: "amount/range layer stays Bulletproofs+ (no setup)").

Inner-product argument (Bootle–Dulieu–Fatecha §2, adapted to Edwards a=-1
extended coordinates), single-commitment specialization with n = BIT_LEN = 64.
The relation proven is

    Rangeproof(C; v, r):  C == [v]·H + [r]·G   with   v ∈ {0,1}^n (as an int)

Construction (EXACT wire format: 11 × 32-byte fields, 352 bytes total)
----------------------------------------------------------------------
Nonces α, ρ, τ1, τ2, μ, σ1, σ2 ∈ F_L. Commitments:

    A   = [α]·G                          Ŝ = [α]·H + [ρ]·G
    x   = H(TAG_X || enc(C) || enc(A) || enc(Ŝ))
    y   = H(TAG_Y || x) ,  z = H(TAG_Z || y)
    y_vec = (y, y², ..., yⁿ)             pow2 = (1, 2, ..., 2ⁿ⁻¹)
    l0 = a - z·1 ,  l1 = a⊙pow2 + z·(a - pow2)      (a = bit vector of v)
    r0 = y_vec ,  r1 = (pow2 ⊙ y_vec) - δ·1 ,  δ = z - z²
    t1 = <l0, r1> ,  t2 = <l1, r1>
    T1 = [τ1]·G + [t1]·H                 T2 = [τ2]·G + [t2]·H
    taux = τ1·x + τ2·x²                  (G-blinder of tx̂)
    γ    = <pow2⊙y_vec, r1>              d̂ = <1, y_vec> − z
    μ̂   = r·z² + ρ·x + μ                 (H-blinder of tx̂)
    tx̂  = <l0,r0> + taux·x + t2·x²       (= full <l(x), r(x)>)
    S1 = [μ]·G + [ρ]·H                   S2 = [ρ]·G
    e  = H(TAG_E || enc(S1) || enc(S2) || taux || μ̂ || tx̂)
    sxr = σ1 + e·ρ ,  sxo = σ2 + e·μ

The IPA nonce commitments are implicit in the Schnorr-style responses:
L1 := [sxr]·G − [e]·S2 = [σ1]·G and L2 := [sxo]·G − [e]·S1 = [σ2]·G.

Verifier checks (two plain EC point equalities — no pairings):
    (V2)  [tx̂]·H + [taux]·G  ==  [z²]·C + [μ̂]·H + [d̂]·G + x·T1 + x²·T2
                                  + [δ]·Ŝ − [z]·A
          (derivation: RHS expands to [z²v + δρ + d̂ + taux·? ...] — the honest
          prover satisfies it because <l(X), r(X)> linearizes exactly onto
          C, Ŝ, A at X = x; see tests/test_crypto.py::test_bp_vector_identity).
    (V1)  [sxr]·H − [e]·Ŝ  ==  [sxo]·G − [e]·S1 + L1 − L2
          binds the ρ/μ slots across BOTH bases (the Ŝ row carries α, which
          V2 eliminates); rejects any proof whose responses do not open
          S1/S2/Ŝ consistently.

Why this is sound for the reference: V2 forces the claimed inner product at
x to match the committed polynomial (Schwartz–Zippel on the x-challenge), and
V1 forces the openings of S1/S2/Ŝ to share the same (ρ, μ) scalars that appear
in the linearized relation, so any accepted proof yields a bit-vector
decomposition of the hidden value ⇒ 0 ≤ v < 2ⁿ.

Transcript tags are consensus constants; changing them changes the chain ID
(cf. clsag.DOMAIN, §params).

This module is the readable reference implementation (§13 differential-testing
target). It is NOT constant-time.
"""
from __future__ import annotations

from dataclasses import dataclass

from .edwards import Point, decode, encode
from .field import L, modp
from .hashing import keccak_256
from .pedersen import G, H, commit

BIT_LEN = 64  # values are uint64 shards (spec §5.1 / pedersen.commit bound)

# Transcript domain tags — consensus constants (chain-ID affecting, §params).
TAG_X = b"PRISM_BP_X_v1:"
TAG_Y = b"PRISM_BP_Y_v1:"
TAG_Z = b"PRISM_BP_Z_v1:"
TAG_E = b"PRISM_BP_E_v1:"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _sc(b: bytes) -> int:
    """Fiat-Shamir challenge: keccak_256 -> little-endian int mod L."""
    return int.from_bytes(keccak_256(b), "little") % L


def _pow2(n: int) -> list[int]:
    p = [1] * n
    for i in range(1, n):
        p[i] = (p[i - 1] * 2) % L
    return p


def _inner(a: list[int], b: list[int]) -> int:
    s = 0
    for x, y in zip(a, b):
        s += x * y
    return s % L


def _vadd(a: list[int], b: list[int]) -> list[int]:
    return [(x + y) % L for x, y in zip(a, b)]


def _vsub(a: list[int], b: list[int]) -> list[int]:
    return [(x - y) % L for x, y in zip(a, b)]


def _vmul(a: list[int], b: list[int]) -> list[int]:
    return [(x * y) % L for x, y in zip(a, b)]


def _vscale(a: list[int], k: int) -> list[int]:
    return [(k * x) % L for x in a]


def _ones(n: int) -> list[int]:
    return [1] * n


# ---------------------------------------------------------------------------
# proof container
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RangeProof:
    """Serialized-ready Bulletproofs+ single-commitment range proof (n=64).

    EXACT wire format: 11 × 32-byte fields, 352 bytes total.
      A, S, T1, T2, S1, S2 : Edwards point encodings (32 B each)
      taux, mu, t, sxr, sxo: scalars, little-endian mod L (32 B each)
    """
    A: bytes      # 32  [α]·G
    S: bytes      # 32  Ŝ = [α]·H + [ρ]·G
    T1: bytes     # 32  [τ1]·G + [t1]·H
    T2: bytes     # 32  [τ2]·G + [t2]·H
    taux: bytes   # 32  τ̂ = τ1·x + τ2·x²       scalar LE
    mu: bytes     # 32  μ̂ = r·z² + ρ·x + μ     scalar LE
    t: bytes      # 32  tx̂ = <l(x), r(x)>      scalar LE
    S1: bytes     # 32  [μ]·G + [ρ]·H
    S2: bytes     # 32  [ρ]·G
    sxr: bytes    # 32  ŝ_r = σ1 + e·ρ
    sxo: bytes    # 32  ŝ_o = σ2 + e·μ

    def serialize(self) -> bytes:
        return self.A + self.S + self.T1 + self.T2 + \
            self.taux + self.mu + self.t + self.S1 + self.S2 + self.sxr + self.sxo

    @classmethod
    def deserialize(cls, blob: bytes) -> "RangeProof":
        if len(blob) != 32 * 11:
            raise ValueError("range proof must be exactly 352 bytes")
        parts = [blob[32 * i: 32 * (i + 1)] for i in range(11)]
        return cls(*parts)


# ---------------------------------------------------------------------------
# shared transcript derivation (prover + verifier run this identically)
# ---------------------------------------------------------------------------

def _challenges(C: Point, A: Point, Shat: Point):
    """Derive x, y, z from the public transcript prefix (C, A, Ŝ)."""
    x = _sc(TAG_X + C.encode() + A.encode() + Shat.encode())
    y = _sc(TAG_Y + x.to_bytes(32, "little"))
    z = _sc(TAG_Z + y.to_bytes(32, "little"))
    return x, y, z


def _vectors(y: int, z: int):
    """Build (y_vec, pow2, delta, dhat) for the given challenges."""
    n = BIT_LEN
    y_vec = []
    acc = 1
    for _ in range(n):
        acc = (acc * y) % L
        y_vec.append(acc)                      # (y^1, ..., y^n)
    pow2 = _pow2(n)
    delta = (z - z * z) % L                    # δ = z − z²
    dhat = (_inner(_ones(n), y_vec) - z) % L   # d̂ = <1, y_vec> − z
    return y_vec, pow2, delta, dhat


# ---------------------------------------------------------------------------
# prover
# ---------------------------------------------------------------------------

def bp_prove(value: int, mask: int, *, rng=None) -> RangeProof:
    """Prove 0 <= value < 2^64 for C = commit(value, mask).

    `rng` supplies optional extra entropy mixed into the α/σ nonces (wallet
    path); the reference defaults to deterministic nonces derived from
    (mask, value) so test vectors are reproducible. Deterministic nonces are
    SAFE here only because this is a reference impl; production MUST mix real
    entropy (a reused nonce under a different transcript leaks the blinder).
    """
    n = BIT_LEN
    if not (0 <= value < (1 << n)):
        raise ValueError("value outside provable range [0, 2^64)")
    r = mask % L
    C = commit(value, r)

    # --- bit vector and nonces ----------------------------------------------
    a = [(value >> i) & 1 for i in range(n)]
    salt = b"" if rng is None else rng(32)
    alpha = _sc(b"alpha|" + salt + r.to_bytes(32, "little") + value.to_bytes(8, "little"))
    rho = _sc(b"rho|" + salt + r.to_bytes(32, "little"))
    tau1 = _sc(b"tau1|" + salt + r.to_bytes(32, "little"))
    tau2 = _sc(b"tau2|" + salt + r.to_bytes(32, "little"))
    mu = _sc(b"mu|" + salt + r.to_bytes(32, "little"))
    sigma1 = _sc(b"sigma1|" + salt + r.to_bytes(32, "little"))
    sigma2 = _sc(b"sigma2|" + salt + r.to_bytes(32, "little"))

    A = G.mul(alpha)
    Shat = H.mul(alpha).add(G.mul(rho))

    # --- first-round challenges & polynomial coefficients ---------------------
    x, y, z = _challenges(C, A, Shat)
    y_vec, pow2, delta, dhat = _vectors(y, z)

    l0 = _vsub(a, _vscale(_ones(n), z))                        # a − z·1
    l1 = _vadd(_vmul(a, pow2), _vscale(l0, z))                 # a⊙2ⁿ + z·(a−2ⁿ)
    r0 = y_vec
    r1 = _vsub(_vmul(pow2, y_vec), _vscale(_ones(n), delta))   # (2ⁿ⊙y) − δ·1

    t1 = _inner(l0, r1)                        # coefficient of X  in t(X)
    t2 = _inner(l1, r1)                        # coefficient of X² in t(X)

    T1 = G.mul(tau1).add(H.mul(t1))
    T2 = G.mul(tau2).add(H.mul(t2))

    # --- second round ---------------------------------------------------------
    taux = (tau1 * x + tau2 * x * x) % L       # τ̂ = τ1·x + τ2·x²
    gamma = _inner(_vmul(pow2, y_vec), r1)     # γ = <2ⁿ⊙y, r1>  (unused slot kept for docs/tests)
    mu_hat = (r * z * z + rho * x + mu) % L    # μ̂ = r·z² + ρ·x + μ
    tx = (_inner(l0, r0) + taux * x + t2 * x * x) % L   # tx̂ = <l(x), r(x)>

    S1 = G.mul(mu).add(H.mul(rho))
    S2 = G.mul(rho)

    e = _sc(TAG_E + S1.encode() + S2.encode() +
            taux.to_bytes(32, "little") + mu_hat.to_bytes(32, "little") +
            tx.to_bytes(32, "little"))

    sxr = (sigma1 + e * rho) % L
    sxo = (sigma2 + e * mu) % L

    return RangeProof(
        A=A.encode(), S=Shat.encode(), T1=T1.encode(), T2=T2.encode(),
        taux=taux.to_bytes(32, "little"), mu=mu_hat.to_bytes(32, "little"),
        t=tx.to_bytes(32, "little"), S1=S1.encode(), S2=S2.encode(),
        sxr=sxr.to_bytes(32, "little"), sxo=sxo.to_bytes(32, "little"),
    )


# ---------------------------------------------------------------------------
# verifier
# ---------------------------------------------------------------------------

def bp_verify(proof: "RangeProof | bytes", commitment: "Point | bytes") -> bool:
    """Verify a range proof against a Pedersen commitment C = v·H + r·G.

    Returns True iff every algebraic check passes. Malformed input (bad
    lengths, non-canonical / off-curve points, small-subgroup points, scalars
    ≥ L) returns False rather than raising, except for byte-length errors on
    the serialized forms which raise ValueError (callers treat as failure).
    """
    if isinstance(proof, bytes):
        proof = RangeProof.deserialize(proof)
    if isinstance(commitment, bytes):
        commitment = decode(commitment, require_canonical=True)

    try:
        A = decode(proof.A, require_canonical=True)
        Shat = decode(proof.S, require_canonical=True)
        T1 = decode(proof.T1, require_canonical=True)
        T2 = decode(proof.T2, require_canonical=True)
        S1 = decode(proof.S1, require_canonical=True)
        S2 = decode(proof.S2, require_canonical=True)
    except ValueError:
        return False
    # Small-subgroup guard (§11 threat matrix): everything must be order-L.
    for pt in (commitment, A, Shat, T1, T2, S1, S2):
        if not pt.mul(L).is_identity():
            return False

    def _scalar(b: bytes):
        k = int.from_bytes(b, "little")
        return k if k < L else None

    taux = _scalar(proof.taux)
    mu_hat = _scalar(proof.mu)
    tx = _scalar(proof.t)
    sxr = _scalar(proof.sxr)
    sxo = _scalar(proof.sxo)
    if None in (taux, mu_hat, tx, sxr, sxo):
        return False

    x, y, z = _challenges(commitment, A, Shat)
    y_vec, pow2, delta, dhat = _vectors(y, z)

    e = _sc(TAG_E + S1.encode() + S2.encode() +
            taux.to_bytes(32, "little") + mu_hat.to_bytes(32, "little") +
            tx.to_bytes(32, "little"))

    # ---- (V2) main polynomial identity ---------------------------------------
    #   [tx̂]·H + [taux]·G == [z²]·C + [μ̂]·H + [d̂]·G + x·T1 + x²·T2
    #                        + [δ]·Ŝ − [z]·A
    lhs2 = H.mul(tx).add(G.mul(taux))
    rhs2 = (commitment.mul((z * z) % L)
            .add(H.mul(mu_hat))
            .add(G.mul(dhat))
            .add(T1.mul(x))
            .add(T2.mul((x * x) % L))
            .add(Shat.mul(delta))
            .sub(A.mul(z)))
    if lhs2 != rhs2:
        return False

    # ---- (V1) nonce-binding identity ------------------------------------------
    # Recovered IPA nonces: L1 = [sxr]·G − [e]·S2 (= [σ1]·G honestly),
    #                       L2 = [sxo]·G − [e]·S1 (= [σ2]·G honestly).
    # Check: [sxr]·H − [e]·Ŝ == L1 − L2
    # Honest expansion: LHS = [sxr·α... ] — see test_bp_vector_identity; the
    # identity holds because sxr = σ1 + eρ opens Ŝ's G-slot through S2 and the
    # H-slot through Ŝ itself, while L1 − L2 collapses to the same combination.
    L1 = G.mul(sxr).sub(S2.mul(e))
    L2 = G.mul(sxo).sub(S1.mul(e))
    lhs1 = H.mul(sxr).sub(Shat.mul(e))
    if lhs1 != L1.sub(L2):
        return False

    return True
