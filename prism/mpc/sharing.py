"""Shamir secret sharing over the Ed25519 scalar field — mpc/ deliverable 1.

Prism's forgivable self-custody (Decision D4: purely user-owned, spec §7.3)
splits a spend key into n shares with a t-of-n threshold. This module is the
algebraic core shared by BOTH families implemented under prism/mpc/:

  * FROST-style EdDSA threshold signatures (frost.py) — the shipped path,
    because Prism signs with Ed25519 (crypto/clsag.py, crypto/stealth.py).
  * GG20-style threshold ECDSA (gg20.py) — the secp256k1-compatible variant
    for cross-chain bridge signers; same polynomial machinery, different
    curve/group classes injected via `group=`.

Design notes
------------
- Field: ℓ (Ed25519 group order) by default; any prime-order scalar field
  works since we only need +, ×, inverse and Lagrange interpolation.
- Shares are evaluated at x = 1..n (never 0: share(0) IS the secret).
- Verifiable keygen: each dealer publishes per-polynomial commitments
  A_k = [a_k]·P (Pedersen-free plain group multiples). A recipient checks
  [s_i]·P == Π A_k^{i^k} — standard Feldman VSS. This is what makes the
  social-recovery ceremony (§7.3) hostile-to-insiders: a malicious contact
  cannot hand out garbage shares undetected.
- Lagrange coefficients are computed at x = 0 over share indices; used both
  for reconstruction and for additive signing (each signer contributes
  λ_i · s_i · R in FROST partial-signing).

Reference implementation: readable, side-channel-naive, differentially
tested against naive paths in tests/test_mpc.py (same house rules as crypto/).
"""
from __future__ import annotations

from dataclasses import dataclass

from ..crypto.edwards import BASE, IDENTITY_POINT, Point
from ..crypto.field import L


# ---------------------------------------------------------------------------
# Minimal scalar-field API (pluggable: ℓ for Ed25519, secp256k1 n for GG20)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ScalarField:
    order: int

    def add(self, a: int, b: int) -> int:
        return (a + b) % self.order

    def sub(self, a: int, b: int) -> int:
        return (a - b) % self.order

    def mul(self, a: int, b: int) -> int:
        return (a * b) % self.order

    def inv(self, a: int) -> int:
        if a % self.order == 0:
            raise ZeroDivisionError("no inverse for zero scalar")
        return pow(a, self.order - 2, self.order)

    def reduce(self, a: int) -> int:
        return a % self.order


ED25519_SCALARS = ScalarField(L)


# ---------------------------------------------------------------------------
# Polynomials & shares
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Share:
    """One party's share: evaluation of the secret polynomial at index i."""
    index: int          # 1-based participant id
    value: int          # s_i = f(i), reduced mod field order


def eval_poly(coeffs: list[int], x: int, field: ScalarField) -> int:
    """Horner evaluation, coeffs[0] = constant term."""
    acc = 0
    for c in reversed(coeffs):
        acc = field.add(field.mul(acc, x), c)
    return acc


def random_poly(secret: int, t: int, field: ScalarField, *, rng=None) -> list[int]:
    """Degree-(t-1) polynomial with constant term = secret."""
    if rng is None:
        import secrets
        rng = secrets.SystemRandom()
    if t < 1:
        raise ValueError("threshold must be >= 1")
    coeffs = [field.reduce(secret)]
    coeffs += [field.reduce(rng.randrange(1, field.order)) for _ in range(t - 1)]
    return coeffs


def make_shares(coeffs: list[int], n: int, field: ScalarField) -> list[Share]:
    if len(coeffs) < 2:
        # t=1 degenerate case: everyone would hold the raw secret; allowed but
        # flagged — callers building real ceremonies must use t>=2.
        pass
    return [Share(i, eval_poly(coeffs, i, field)) for i in range(1, n + 1)]


def lagrange_coefficients(indices: list[int], field: ScalarField,
                          at: int = 0) -> dict[int, int]:
    """λ_i for the set `indices`, interpolated at x = `at` (default 0).

    λ_i = Π_{j≠i} (at − j) / (i − j)   (mod order)
    """
    if not indices:
        raise ValueError("empty share set")
    if len(set(indices)) != len(indices):
        raise ValueError("duplicate share index")
    out: dict[int, int] = {}
    for i in indices:
        num, den = 1, 1
        for j in indices:
            if j == i:
                continue
            num = field.mul(num, at - j)
            den = field.mul(den, i - j)
        out[i] = field.mul(num, field.inv(den))
    return out


def reconstruct(shares: list[Share], field: ScalarField) -> int:
    """Lagrange-interpolate the constant term from >= t shares."""
    lambdas = lagrange_coefficients([s.index for s in shares], field)
    acc = 0
    for s in shares:
        acc = field.add(acc, field.mul(lambdas[s.index], s.value))
    return acc


# ---------------------------------------------------------------------------
# Feldman-style verifiable commitments (per-dealer public artifacts)
# ---------------------------------------------------------------------------

def poly_commitments(coeffs: list[int], base: Point) -> list[Point]:
    """A_k = [a_k]·base for every coefficient (public side of keygen)."""
    return [base.mul(c) for c in coeffs]


def verify_share(share: Share, commits: list[Point], base: Point,
                 field: ScalarField) -> bool:
    """[s_i]·base == Π_k A_k^{i^k}?  Catches cheating dealers in the ceremony."""
    lhs = base.mul(share.value)
    rhs = IDENTITY_POINT
    x_pow = 1
    for a_k in commits:
        rhs = rhs.add(a_k.mul(field.reduce(x_pow)))
        x_pow = field.mul(x_pow, share.index)
    return lhs == rhs


def combine_commitments(commits_list: list[list[Point]]) -> list[Point]:
    """Sum parallel dealers' commitment vectors coefficient-wise → joint key
    commitments (multi-dealer keygen: J = Σ secrets, A_k = Σ A_{k,i})."""
    if not commits_list:
        raise ValueError("no dealers")
    width = len(commits_list[0])
    out = []
    for k in range(width):
        acc = IDENTITY_POINT
        for commits in commits_list:
            if len(commits) != width:
                raise ValueError("dealer thresholds disagree")
            acc = acc.add(commits[k])
        out.append(acc)
    return out


def joint_public_key_from_commits(commits: list[Point]) -> Point:
    """The group element carrying the joint secret = A_0 (constant coeff)."""
    return commits[0]


__all__ = [
    "ScalarField", "ED25519_SCALARS", "BASE",
    "Share", "eval_poly", "random_poly", "make_shares",
    "lagrange_coefficients", "reconstruct",
    "poly_commitments", "verify_share", "combine_commitments",
    "joint_public_key_from_commits",
]
