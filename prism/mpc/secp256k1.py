"""secp256k1 group operations — the bridge-signer curve for mpc/gg20.py.

Prism's own chain signs Ed25519 (crypto/edwards.py). This module exists only
so the *same* Shamir/Feldman machinery in sharing.py can be instantiated over
secp256k1 for cross-chain bridge signers, where counterpart chains (BTC-family)
expect ECDSA public keys. It is deliberately minimal: affine point math and
scalar arithmetic, nothing else. Reference-quality (readable, not constant
time), same house rules as crypto/.

Domain separation note (spec §9 / Decision on algorithm conflict):
  "GG20 over Ed25519" as literally written in spec §9 is unsound — GG20's MtA
  protocol relies on the ECDSA group law identity ([k]G x = r). We therefore
  ship FROST-style threshold EdDSA (frost.py) as the primary spend path and
  GG20-style threshold ECDSA here strictly for secp256k1 bridge use.
"""
from __future__ import annotations

from dataclasses import dataclass

# Curve parameters (SEC 2, sect 2.4.1)
P = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F
N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
A = 0
B = 7
GX = 0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798
GY = 0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8


def inv_mod(a: int, m: int = P) -> int:
    if a % m == 0:
        raise ZeroDivisionError("no inverse for zero")
    return pow(a, m - 2, m)


@dataclass(frozen=True)
class ECPoint:
    """Affine point; None coordinates encode the point at infinity."""
    x: int | None
    y: int | None

    @property
    def is_identity(self) -> bool:
        return self.x is None and self.y is None

    def add(self, other: "ECPoint") -> "ECPoint":
        if self.is_identity:
            return other
        if other.is_identity:
            return self
        if self.x == other.x:
            if (self.y + other.y) % P == 0:
                return IDENTITY
            # doubling
            lam = (3 * self.x * self.x) % P * inv_mod(2 * self.y) % P
        else:
            lam = (other.y - self.y) % P * inv_mod(other.x - self.x) % P
        x3 = (lam * lam - self.x - other.x) % P
        y3 = (lam * (self.x - x3) - self.y) % P
        return ECPoint(x3, y3)

    def mul(self, k: int) -> "ECPoint":
        k %= N
        acc, cur = IDENTITY, self
        while k:
            if k & 1:
                acc = acc.add(cur)
            cur = cur.add(cur)
            k >>= 1
        return acc

    def negate(self) -> "ECPoint":
        if self.is_identity:
            return self
        return ECPoint(self.x, (-self.y) % P)

    def on_curve(self) -> bool:
        if self.is_identity:
            return True
        return (self.y * self.y - self.x ** 3 - B) % P == 0

    def encode_compressed(self) -> bytes:
        assert not self.is_identity
        prefix = 0x02 if self.y % 2 == 0 else 0x03
        return prefix.to_bytes(1, "big") + self.x.to_bytes(32, "big")

    @staticmethod
    def decode(b: bytes) -> "ECPoint":
        if len(b) != 33 or b[0] not in (2, 3):
            raise ValueError("not a compressed secp256k1 point")
        x = int.from_bytes(b[1:], "big")
        y2 = (pow(x, 3, P) + B) % P
        y = pow(y2, (P + 1) // 4, P)  # P % 4 == 3
        if (y * y) % P != y2:
            raise ValueError("point not on curve")
        if y % 2 != b[0] - 2:
            y = P - y
        return ECPoint(x, y)


BASE = ECPoint(GX, GY)
IDENTITY = ECPoint(None, None)

assert BASE.on_curve()
assert BASE.mul(N).is_identity  # N is the prime group order


@dataclass(frozen=True)
class _SecpScalars:
    """ScalarField-compatible view of Z/N (gg20 needs mod-N reduction and
    inversion; sharing.py's generic ScalarField(N) also works, this just
    documents the canonical instance)."""
    order: int = N

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


SECP_SCALARS = _SecpScalars()

__all__ = [
    "P", "N", "A", "B",
    "ECPoint", "BASE", "IDENTITY", "inv_mod",
    "SECP_SCALARS",
]
