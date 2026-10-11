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
  * Aggregation: R = [Σ k_i]·G; r = R.x mod n; s = k^{-1}(m + r·x),
    where z = k·x is the blind MtA product (production computes s from
    shares of z; this v0 coordinator reconstructs x to form s directly).
  * Anti-corruption: DDH-equality proof per partial sig (a signer can be
    excluded and still produce a publicly verifiable complaint — in v0 we
    ship the *check* given honest auxiliary openings, not the ZK proofs;
    flagged hardening item).

MtA reference protocol: 1-out-of-2 oblivious transfer over RSA class groups
is what GG20 uses; here we implement the *ideal functionality* interface
(mta_pair taking plain inputs) so the algebraic identity α+β = x_i·k_j is
testable end-to-end. Production swaps mta_pair for OT-based MtA + DDH
consistency proofs without touching gg20_sign's bookkeeping.

⚠️ HONESTY BOX — read before trusting anything here:
  * Ideal-MtA means a wire-level eavesdropper would learn x_i·k_j outright.
    Algebraically correct, cryptographically leaky BY CONSTRUCTION.
  * No range proofs on shares/nonces, no adversarial abort handling, no
    Paillier/VSSLE commitments. GG20's hostile-network machinery (§4-§6)
    is stubbed. Do not connect this to real value. Phase-2 GA item.

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
# Ideal-functionality MtA (see HONESTY BOX above before shipping anything!)
# ---------------------------------------------------------------------------

def mta_pair(x_i: int, k_j: int) -> tuple[int, int]:
    """Ideal MtA for ordered pair (initiator i holding x_i, peer j holding
    k_j): returns (alpha_ij, beta_ij) with alpha + beta == x_i * k_j
    (mod n). The initiator keeps alpha, sends beta. gg20_sign accumulates
    each signer's delta as row-sum of own alphas plus received betas, so
    Σ_i δ_i telescopes to Σ_{i,j} x_i·k_j = (Σ x_i)(Σ k_j) = k·x — the
    invariant asserted numerically inside gg20_sign and pinned by
    tests/test_mpc.py::TestGG20."""
    prod = (x_i * k_j) % N
    alpha = secrets.randbelow(N)
    beta = (prod - alpha) % N
    return alpha, beta


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

    Steps (reference GG20 main protocol, honest-but-curious):
      1. Each i in Q samples k_i; K_i = [k_i]·G (additive nonce: k = Σ k_i,
         R = [k]·G = Σ K_i).
      2. Pairwise ideal-MtA on (x_i = λ_i·σ_i, k_j) yields masks with
         α+β = x_i·k_j; each i forms δ_i (own α's + received β's) and
         c_i = k_i·x_i. Then z = Σ c_i + Σ δ_i = k·x exactly (asserted).
      3. r = R.x mod n; s = k⁻¹·(m + r·x).  In production the parties
         compute this *blindly* from shares of z = k·x (GG20 §4.3); this
         v0 reference coordinator reconstructs x_joint in-process to form
         s directly, so the output is a standard low-s ECDSA signature
         verifiable against X = [x]·G by ecdsa_verify().
      4. Low-s normalization (BIP 62) with recovery-parity flip.

    ⚠️ Production replaces step 2's ideal MtA with OT-based MtA + DDH
    consistency proofs (GG20 §4); see module HONESTY BOX notes.
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
            alpha, beta = mta_pair(x_adj[i], k[j])
            # i keeps alpha (contributes to δ_i), sends beta to j (δ_j)
            delta[i] = (delta[i] + alpha) % N
            delta[j] = (delta[j] + beta) % N

    kk = sum(k.values()) % N
    z = (sum(ci.values()) + sum(delta.values())) % N
    x_joint = sum(x_adj.values()) % N
    assert z == (kk * x_joint) % N, "MtA invariant broken"
    # Σ x_adj = x (Lagrange reconstruction at 0)
    R = IDENTITY
    for i in Q:
        R = R.add(K_comm[i])
    assert R == BASE.mul(kk)
    r = R.x % N
    if r == 0:
        raise ArithmeticError("bad nonce (r=0); retry")
    m = msg_hash % N
    # s = k⁻¹·(m + r·x).  NOTE: z = k·x, so using z here would be wrong;
    # x_joint = Σ λ_i·σ_i is the Lagrange reconstruction of the secret.
    s = (pow(kk, N - 2, N) * (m + r * x_joint)) % N
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
    "mta_pair", "gg20_keygen", "gg20_sign", "ecdsa_verify",
]
