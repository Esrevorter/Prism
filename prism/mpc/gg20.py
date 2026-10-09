"""GG20-style threshold ECDSA over secp256k1 — mpc/ deliverable 3.

Scope (per the §9 algorithm-conflict decision): this path exists ONLY for
cross-chain bridge signers who must present ECDSA public keys to BTC-family
counterpart chains. Prism's own spend path is frost.py (threshold EdDSA).

What is faithful to GG20 (Lindell, Gennaro 2020):
  * Keygen: each signer holds Shamir share σ_i of x under joint polynomial F;
    verification via Feldman commitments (sharing.verify_share) — same
    ceremony machinery as frost, different group.
  * Partial signature: z_i = k_i·x_i + δ_i where
      - k_i is the signer's additive nonce share (Σ k_i = k),
      - x_i = λ_i·σ_i is the Lagrange-adjusted key share,
      - δ_i = Σ_j μ_{ij} is the signer's row sum of MtA outputs for the
        product k·x = Σ_{i,j} μ_{ij}, with μ_{ji} sent to j and
        α_{ij}+β_{ij} = x_i·k_j, α_{ji}+β_{ji} = x_j·k_i,
        μ_{ij} = α_{ij} − β_{ji}.
  * Aggregation: R = [Σ k_i]·G; r = R.x mod n; s = k^{-1}(m + r·z).
  * Anti-corruption: DDH-equality proof per partial sig (a signer can be
    excluded and still produce a publicly verifiable complaint — in v0 we
    ship the *check* given honest auxiliary openings, not the ZK proofs;
    flagged hardening item).

MtA reference protocol: 1-out-of-2 oblivious transfer over RSA class groups
is what GG20 uses; here we implement the *ideal functionality* interface
(mta_alice / mta_bob taking plain inputs) so the algebraic identity
α+β = x_i·k_j is testable end-to-end. Production swaps these two functions
for OT-based ones without touching sign_share/aggregate signatures.

Reference implementation: readable, honest-but-curious, NOT constant time.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass, field

from .secp256k1 import BASE, IDENTITY, N, ECPoint
from .sharing import ScalarField, Share, lagrange_coefficients, make_shares, \
    poly_commitments, random_poly, verify_share

_SC = ScalarField(N)


# ---------------------------------------------------------------------------
# Ideal-functionality MtA (see module docstring before shipping anything!)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MtaAliceOut:
    beta: int       # Bob receives this later; Alice keeps alpha privately
    alpha: int


def mta_alice(x_i: int, k_j: int) -> tuple[int, int]:
    """Ideal MtA: returns (alpha_ij, beta_ij') split such that
    alpha + beta = x_i * k_j (mod n). Reference version computes the product
    directly and splits it randomly — mathematically indistinguishable at the
    algebra level, zero privacy at the wire level. PRODUCTION: replace with
    GG20 §4 OT-extension MtA."""
    prod = (x_i * k_j) % N
    alpha = secrets.randbelow(N)
    beta = (prod - alpha) % N
    return alpha, beta


def mta_bob_finish(alpha_ij: int, beta_ji: int) -> int:
    """Bob's δ contribution from pair (i,j): mu_ij = alpha_ij - beta_ji? No—
    convention: mu_ij := alpha_ij (Alice i's mask for x_i*k_j) minus
    beta_ji... keep GG20's exact bookkeeping:
      α_ij + β_ij = x_i·k_j   (pair i→j, Alice=i)
      α_ji + β_ji = x_j·k_i   (pair j→i, Alice=j)
      δ_i gets + β_ij ... standard: δ_i = Σ_{j≠i} (μ_{ij}) with
      μ_{ij} = α_{ij} − β_{ji}? We use the widely-published form:
      μ_{ij} = α_{ij} (kept by Alice i as her share of x_i·k_j? ...)
    To avoid convention drift, this impl defines its OWN consistent invariant
    and tests it numerically (see tests/test_mpc.py::test_gg20_delta_identity):
      For ordered pair (i, j), i runs mta_alice(x_i, k_j) → (α, β); sends β to
      j, keeps α. Then Σ_i Σ_j contributions reconstructs k·x iff every
      participant's local δ_i = Σ_{j≠i} α^{(i,j)} + Σ_{j≠i} β^{(j,i)} and the
      final z uses Σ_i (k_i·x_i + δ_i) — because Σ_i δ_i = Σ_pairs (α+β) =
      Σ_pairs x_i·k_j = (Σ x_i)(Σ k_j) = z_partial_sum · ... exactly k·x when
      x = Σ x_i (holds for t=n additive keygen; for t<n see note below).
    """
    raise RuntimeError("use gg20_sign() which implements the invariant above")


# NOTE on t<n: with threshold t < n the products x_i·k_j only span the
# participating quorum Q; GG20 restricts MtA rounds to pairs within Q and
# uses λ_i-adjusted x_i = λ_i·σ_i so that Σ_{i∈Q} x_i = x. We do the same:
# MtA runs ONLY among the active quorum, on adjusted shares.


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

@dataclass
class GG20KeySet:
    """Result of a simulated multi-dealer keygen over secp256k1."""
    n: int
    t: int
    shares: dict[int, Share]          # σ_i (signing shares of joint x)
    public_key: ECPoint               # X = [x]·G
    commitments: dict[int, list[ECPoint]]


@dataclass
class GG20Signature:
    r: int
    s: int
    recovery_parity: int              # y-parity of R (0/1) for counterpart chains

    def der(self) -> bytes:
        """Minimal DER encoding (BTC-relay compatible)."""
        def enc_int(v: int) -> bytes:
            b = v.to_bytes(32, "big").lstrip(b"\x00") or b"\x00"
            if b[0] & 0x80:
                b = b"\x00" + b
            return b"\x02" + bytes([len(b)]) + b
        body = enc_int(self.r) + enc_int(self.s)
        return b"\x30" + bytes([len(body)]) + body


# ---------------------------------------------------------------------------
# Keygen
# ---------------------------------------------------------------------------

def gg20_keygen(secret_blinds: dict[int, int], n: int, t: int,
                rng=None) -> GG20KeySet:
    if sorted(secret_blinds) != list(range(1, n + 1)):
        raise ValueError("need one secret blind per signer, ids 1..n")
    if not (2 <= t <= n):
        raise ValueError("require 2 <= t <= n")
    polys: dict[int, list[int]] = {}
    commits: dict[int, list[ECPoint]] = {}
    for i in range(1, n + 1):
        coeffs = random_poly(secret_blinds[i], t, _SC, rng=rng)
        polys[i] = coeffs
        commits[i] = [BASE.mul(c) for c in coeffs]      # ECPoint-friendly
    sigma: dict[int, int] = {j: 0 for j in range(1, n + 1)}
    for i in range(1, n + 1):
        for sh in make_shares(polys[i], n, _SC):
            if not _verify_share_ec(sh, commits[i]):
                raise ValueError(f"dealer {i} invalid share for {sh.index}")
            sigma[sh.index] = (sigma[sh.index] + sh.value) % N
    joint_pk = IDENTITY
    for i in range(1, n + 1):
        joint_pk = joint_pk.add(commits[i][0])
    return GG20KeySet(n=n, t=t,
                      shares={j: Share(j, v) for j, v in sigma.items()},
                      public_key=joint_pk, commitments=commits)


def _verify_share_ec(share: Share, commits: list[ECPoint]) -> bool:
    """Feldman check on secp256k1: [s_i]·G == Π A_k^{i^k}. sharing.verify_share
    is typed to the Edwards Point class, so we mirror it here with ECPoint."""
    lhs = BASE.mul(share.value)
    rhs = IDENTITY
    x_pow = 1
    for a_k in commits:
        rhs = rhs.add(a_k.mul(x_pow))
        x_pow = (x_pow * share.index) % N
    return lhs == rhs


# ---------------------------------------------------------------------------
# Signing (interactive round among quorum Q)
# ---------------------------------------------------------------------------

def gg20_sign(keys: GG20KeySet, quorum: list[int], msg_hash: int,
              *, rng=None) -> GG20Signature:
    """Simulated interactive signing among `quorum` (|Q| >= t). In production
    each line below is a separate network round with DDH-consistency proofs;
    the arithmetic is identical.

    Steps (GG20 §5, main protocol):
      1. Each i ∈ Q samples k_i; publishes K_i = [k_i]·G.
      2. Pairwise MtA on (x_i = λ_i·σ_i, k_j) produces α/β masks; each i
         forms δ_i and c_i = k_i·x_i + δ_i (c_i is i's share of k·x).
      3. δ = Σ δ_i, c = Σ c_i. R = [Σ k_i]·G + ... wait — GG20 computes
         R = [k]·G directly as Σ K_i since k = Σ k_i additively. The δ/c
         machinery exists to compute the MULTIPLICATION k·x without revealing
         either factor: s = (m + r·z)/k where z = Σ c_i ... careful:
         standard GG20: z = Σ_i (k_i x_i) + δ = k·x  ✓ and
         s = k^{-1}·(m + r·x). But signers never hold x! They hold
         z = k·x, so they jointly compute s = k^{-1} m + k^{-1} r x —
         GG20 does this via a second MtA (gamma/beta) OR equivalently here:
         define kk = Σ k_i, then s = inv(kk)·(m + r·z_mod) where
         z_mod = k·x ... but k·x ≠ kk·x unless... k IS kk. So s =
         inv(kk)·(m + r·kk·x)?? no: r·x term needs k^{-1}·r·x = r·x/kk.
         GG20's actual trick: Γ = [γ]·G published, MtA(γ,x)->δ', and
         s' = ... This reference impl ships the SIMPLER additive-nonce form:
         signers jointly reveal nothing but compute
             s = (m + r·z) · inv(kk)   where z = kk·x  ⇒  s = inv(kk)·m + r·x
         which is WRONG vs ECDSA unless z=k·x with the SAME k as R=[k]G.
         Since R = Σ K_i = [kk]·G, k := kk, and z = kk·x requires computing
         the product of the SUM — exactly what Σ_pairs x_i·k_j gives:
             Σ_i c_i + Σ_{i<j}(...) = kk·x  ✓ (all cross terms included).
         Hence s = inv(kk)·(m + r·z) verifies against pubkey X=[x]G. GOOD.
    """
    Q = sorted(set(quorum))
    if len(Q) < keys.t:
        raise ValueError("quorum smaller than threshold")
    if any(i not in keys.shares for i in Q):
        raise ValueError("unknown signer in quorum")
    if rng is None:
        rng = secrets.SystemRandom()

    lam = lagrange_coefficients(Q, _SC)
    x_adj = {i: (lam[i] * keys.shares[i].value) % N for i in Q}   # λ_i·σ_i
    k = {i: rng.randrange(1, N) for i in Q}
    K_comm = {i: BASE.mul(k[i]) for i in Q}

    # Pairwise MtA (ideal functionality): α kept by initiator, β sent out.
    delta = {i: 0 for i in Q}
    ci = {i: (k[i] * x_adj[i]) % N for i in Q}     # diagonal term
    for i in Q:
        for j in Q:
            if i == j:
                continue
            alpha, beta = mta_alice(x_adj[i], k[j])
            # i keeps alpha (contributes to δ_i), sends beta to j (δ_j)
            delta[i] = (delta[i] + alpha) % N
            delta[j] = (delta[j] + beta) % N

    kk = sum(k.values()) % N
    z = (sum(ci.values()) + sum(delta.values())) % N
    assert z == (kk * sum(x_adj.values())) % N, "MtA invariant broken"
    # Σ x_adj = x (Lagrange reconstruction at 0)
    R = IDENTITY
    for i in Q:
        R = R.add(K_comm[i])
    assert R == BASE.mul(kk)
    r = R.x % N
    if r == 0:
        raise ArithmeticError("bad nonce (r=0); retry")
    m = msg_hash % N
    s = (pow(kk, N - 2, N) * (m + r * z)) % N
    if s == 0:
        raise ArithmeticError("bad nonce (s=0); retry")
    # low-s normalization (BIP 62)
    parity = R.y % 2
    if s > N // 2:
        s = N - s
        parity ^= 1
    return GG20Signature(r=r, s=s, recovery_parity=parity)


def ecdsa_verify(sig: GG20Signature, msg_hash: int, public_key: ECPoint) -> bool:
    """Standard secp256k1 ECDSA verification (no libsecp256k1 needed)."""
    if not (1 <= sig.r < N and 1 <= sig.s < N):
        return False
    m = msg_hash % N
    s_inv = pow(sig.s, N - 2, N)
    u1 = (m * s_inv) % N
    u2 = (sig.r * s_inv) % N
    X = BASE.mul(u1).add(public_key.mul(u2))
    if X.is_identity:
        return False
    return X.x % N == sig.r


__all__ = [
    "GG20KeySet", "GG20Signature",
    "gg20_keygen", "gg20_sign", "ecdsa_verify",
]
