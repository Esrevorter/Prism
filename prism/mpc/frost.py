"""FROST-style threshold EdDSA over Ed25519 — mpc/ deliverable 2.

This is Prism's PRIMARY spend path (spec §9; see secp256k1.py docstring for
why "GG20 over Ed25519" as literally written was rejected as unsound).

Keygen (multi-dealer, DKG-lite):
  Every signer i picks a long-lived private scalar x_i and a degree-(t-1)
  polynomial f_i with f_i(0) = x_i, publishes Feldman commitments
  A_{i,k} = [a_k]·G, and sends share f_i(j) privately to peer j. Each
  recipient verifies every inbound share against the sender's public
  commitments BEFORE accepting (sharing.verify_share) — a cheating contact is
  caught at the ceremony, which is what makes §7.3 hostile-to-insiders.
  Joint secret:      x = Σ_i x_i          (nobody ever learns x)
  Joint public key:  X = Σ_i A_{i,0} = [x]·G
  Local signing share for j: σ_j = Σ_i f_i(j) = F(j) where F = Σ_i f_i is the
  joint polynomial. NOTE: σ_j doubles as F(j), so partial signatures can be
  formed from stored state alone — no per-signing round for share proofs in v0
  (hardening item: add ZKPs of consistent share, GG20-style, before GA).

Signing (two rounds, RFC 9052 shape):
  Round 1: signer i samples nonce b_i, broadcasts B_i = [b_i]·G.
  Round 2: R = Σ B_i; ε = H(R || X || msg) mod L  — byte-identical to the
           single-key EdDSA challenge convention (SHA-512, RFC 8032), so an
           aggregated signature verifies with STOCK code:
               [z]·G == R + ε·X,   z = Σ_i z_i
               z_i = b_i + ε·λ_i·σ_i        (mod ℓ)
  Correctness: Σ z_i = Σ b_i + ε·Σ λ_i·F(i) = ρ + ε·F(0) = ρ + ε·x
             and  R + ε·X = [ρ]G + [ε·x]G = [ρ + ε·x]G ✓
             (interpolation over any ≥t indices reconstructs F(0); fewer than
             t shares contribute nothing learnable — Shamir guarantee).

Because the wire format is indistinguishable from ordinary EdDSA, the wallet
TOPOLOGY itself stays private on-chain — forgivable self-custody without a
trace of MPC.

Reference implementation caveats (flagged in README):
  * honest-but-curious model; no ZKP of shared secret in v0;
  * nonces are one-time — reusing b_i across signings leaks shares linearly;
    the production store must persist-and-delete atomically.
"""
from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass

from ..crypto.edwards import BASE, IDENTITY_POINT, Point
from ..crypto.field import L, scalar_reduce
from .sharing import (ED25519_SCALARS, Share, combine_commitments,
                      lagrange_coefficients,
                      make_shares, poly_commitments, random_poly, verify_share)

_F = ED25519_SCALARS


# ---------------------------------------------------------------------------
# Challenge — MUST match single-key EdDSA (RFC 8032 convention)
# ---------------------------------------------------------------------------

def _challenge(r_bytes: bytes, pk_bytes: bytes, msg: bytes) -> int:
    """ε = SHA-512(R || X || M) reduced mod ℓ."""
    return scalar_reduce(hashlib.sha512(r_bytes + pk_bytes + msg).digest())


# ---------------------------------------------------------------------------
# Keygen ceremony
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CeremonyResult:
    shares: dict[int, Share]             # index -> local signing share σ_i
    public_key: Point                    # joint X
    commitments: dict[int, list[Point]]  # dealer -> A_{i,*} (audit artifacts)
    joint_poly_at: dict[int, int]        # index -> F(i) (== σ_i.value)

    def joint_commitments(self) -> list[Point]:
        """Coefficient-wise sum of all dealers' Feldman vectors — the public
        artifact every share (initial or resharded) must verify against.
        Pass THIS into recovery.create_recovery_state(), not one dealer's
        vector."""
        return combine_commitments([self.commitments[i]
                                    for i in sorted(self.commitments)])


def run_keygen(secret_blinds: dict[int, int], n: int, t: int,
               rng=None) -> CeremonyResult:
    """Simulated full ceremony over signers 1..n. `secret_blinds[i]` is
    signer i's private scalar x_i (in production generated on-device and
    never transmitted — only f_i(j) goes out, and only to j).

    Raises ValueError if any produced share fails Feldman verification.
    """
    if sorted(secret_blinds) != list(range(1, n + 1)):
        raise ValueError("need exactly one secret blind per signer, ids 1..n")
    if not (1 <= t <= n):
        raise ValueError("require 1 <= t <= n")
    if t < 2:
        raise ValueError("t=1 degenerates to everyone holding the secret; "
                         "ceremonies must use t>=2")
    polys: dict[int, list[int]] = {}
    commits: dict[int, list[Point]] = {}
    for i in range(1, n + 1):
        coeffs = random_poly(secret_blinds[i], t, _F, rng=rng)
        polys[i] = coeffs
        commits[i] = poly_commitments(coeffs, BASE)

    sigma: dict[int, int] = {j: 0 for j in range(1, n + 1)}
    for i in range(1, n + 1):
        for sh in make_shares(polys[i], n, _F):
            if not verify_share(sh, commits[i], BASE, _F):
                raise ValueError(f"dealer {i} produced invalid share for {sh.index}")
            sigma[sh.index] = _F.add(sigma[sh.index], sh.value)

    joint_pk = IDENTITY_POINT
    for i in range(1, n + 1):
        joint_pk = joint_pk.add(commits[i][0])

    shares = {j: Share(j, sigma[j]) for j in range(1, n + 1)}
    return CeremonyResult(shares=shares, public_key=joint_pk,
                          commitments=commits, joint_poly_at=dict(sigma))


# ---------------------------------------------------------------------------
# Signing
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class NoncePair:
    secret_b: int
    comm_B: Point


@dataclass(frozen=True)
class PartialSignature:
    index: int
    z: int


def generate_nonce(*, rng=None) -> NoncePair:
    if rng is None:
        rng = secrets.SystemRandom()
    b = rng.randrange(1, L)
    return NoncePair(b, BASE.mul(b))


def aggregate_nonces(nonces: list[NoncePair]) -> tuple[Point, bytes]:
    R = IDENTITY_POINT
    for np_ in nonces:
        R = R.add(np_.comm_B)
    return R, R.encode()


def sign_share(index: int, signer_indices: list[int], share_value: int,
               nonce: NoncePair, msg: bytes, R_enc: bytes,
               public_key: Point) -> PartialSignature:
    """z_i = b_i + ε·λ_i·σ_i  (mod ℓ)."""
    lambdas = lagrange_coefficients(signer_indices, _F)
    lam = lambdas[index]
    eps = _challenge(R_enc, public_key.encode(), msg)
    z = _F.add(nonce.secret_b, _F.mul(_F.mul(eps, lam), share_value))
    return PartialSignature(index, z)


def aggregate_signature(partials: list[PartialSignature], R_enc: bytes) -> bytes:
    z = 0
    for p in partials:
        z = _F.add(z, p.z)
    return R_enc + z.to_bytes(32, "little")


def sign_threshold(ceremony: CeremonyResult, signer_indices: list[int],
                   msg: bytes, *, rng=None) -> bytes:
    """Convenience end-to-end two-round flow for the reference tests."""
    if len(signer_indices) < 2:
        raise ValueError("threshold signing needs >= 2 participants")
    if any(i not in ceremony.shares for i in signer_indices):
        raise ValueError("unknown signer index")
    nonces = [generate_nonce(rng=rng) for _ in signer_indices]
    R, R_enc = aggregate_nonces(nonces)
    partials = []
    for idx, np_ in zip(signer_indices, nonces):
        sh = ceremony.shares[idx]
        partials.append(sign_share(idx, signer_indices, sh.value, np_,
                                   msg, R_enc, ceremony.public_key))
    return aggregate_signature(partials, R_enc)


def verify_signature(sig: bytes, msg: bytes, public_key: Point) -> bool:
    """Stock EdDSA check: [z]·G == R + ε·X. Interoperable with any verifier
    following the same challenge convention (see _challenge)."""
    if len(sig) != 64:
        return False
    try:
        R = Point.decode(sig[:32])
    except ValueError:
        return False
    z = int.from_bytes(sig[32:], "little")
    if z >= L:
        return False
    eps = _challenge(sig[:32], public_key.encode(), msg)
    lhs = BASE.mul(z)
    rhs = R.add(public_key.mul(eps))
    return lhs == rhs


__all__ = [
    "CeremonyResult", "NoncePair", "PartialSignature",
    "run_keygen", "generate_nonce", "aggregate_nonces", "sign_share",
    "aggregate_signature", "sign_threshold", "verify_signature",
]
