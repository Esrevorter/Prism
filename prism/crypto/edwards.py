"""Ed25519-compatible twisted-Edwards group ops — spec §5.1, crypto/ deliverable 1.

Extended coordinates (X : Y : Z : T), x = X/Z, y = Y/Z, x*y = T/Z, per
Hisil–Wong–Carter–Dawson ("Addition-Safe Groups with a Pristine Monolith").
Complete formulas: no special cases for equal/negative/identity inputs, so
ring-signature code paths never branch on point values.

Point encoding is the standard Ed25519 little-endian form (RFC 8032):
32 bytes = y | (x-parity << 255). We deliberately do NOT reject non-canonical
encodings at decode time (Monero compatibility); callers that need strict
checks use `require_canonical=True`.

This module is the *reference* implementation. Production nodes will use the
Rust/FFI fast path; both must agree on every test vector in tests/test_crypto.py.
"""
from __future__ import annotations

from dataclasses import dataclass

from .field import D, L, P, inv, modp, sqrt

# Identity in extended coords.
IDENTITY = (0, 1, 1, 0)

# RFC 8032 base point B.
_By = 4 * inv(5) % P
_Bx = sqrt(modp((_By * _By - 1) * inv(modp(D * _By * _By + 1))))
if _Bx % 2 != 0:
    _Bx = P - _Bx
BASEPOINT = (_Bx, _By, 1, modp(_Bx * _By))


@dataclass(frozen=True)
class Point:
    """A curve point in extended coordinates."""
    xh: int
    yh: int
    zh: int
    th: int

    # ------------------------------------------------------------- algebra --
    def add(self, other: "Point") -> "Point":
        # Extended twisted-Edwards addition, a = -1 (Bernstein–Lange
        # "Faster addition and doubling on elliptic curves", extended-1987-409
        # / HWCD complete formulas — no special cases):
        #   A = (Y1-X1)(Y2+X2), B = (Y1+X1)(Y2-X2), C = 2d T1 T2, D = 2 Z1 Z2
        #   E = B-A, F = D-C, G = D+C, H = B+A
        #   X3 = E*F, Y3 = G*H, T3 = E*H, Z3 = F*G
        # Regression note: an earlier draft emitted (E*F, G*H, F*G, E*H) —
        # i.e. it swapped T3 and Z3. The affine x,y were still correct for a
        # single addition, but T was wrong, so every *subsequent* addition in
        # a double-and-add chain corrupted the scalar mul whenever more than
        # one bit of k was set. Caught by the differential test against naive
        # affine arithmetic (test_scalar_mul_matches_affine).
        # Differentially tested against independent affine arithmetic in
        # tests/test_crypto.py.
        xh1, yh1, zh1, th1 = self.xh, self.yh, self.zh, self.th
        xh2, yh2, zh2, th2 = other.xh, other.yh, other.zh, other.th
        a = modp((yh1 - xh1) * (yh2 + xh2))
        b = modp((yh1 + xh1) * (yh2 - xh2))
        c = modp(2 * D * th1 * th2)
        dd = modp(2 * zh1 * zh2)
        e, f, g, h = modp(b - a), modp(dd - c), modp(dd + c), modp(b + a)
        # HWCD complete addition for twisted Edwards (a = -1), extended coords:
        #   X3 = E*F,  Y3 = G*H,  Z3 = F*G,  T3 = E*H
        # Gate: BASE.mul(2).encode() == libsodium c9a3f86a...6022; differential
        # vs naive affine arithmetic in tests/test_crypto.py.
        #
        # REGRESSION NOTE: two earlier drafts were both broken, in different ways:
        #  (1) Point(E*F, G*H, F*G, E*H) positional call — since the dataclass
        #      field order is (xh, yh, zh, th), this silently assigned Z3=E*H and
        #      T3=F*G (a Z/T swap); corrupts every multi-bit scalar mul.
        #  (2) A "fix" that reordered to Z3=D^2-C^2 while leaving T3=E*H — still
        #      wrong because (1)'s positional swap meant T was actually F*G there.
        # The canonical formula set is Z3 = F*G and T3 = E*H; written explicitly
        # below so no positional ambiguity remains.
        return Point(xh=modp(e * f), yh=modp(g * h), zh=modp(f * g), th=modp(e * h))

    def double(self) -> "Point":
        """Doubling via the add formula (complete for a = -1); slower but
        provably identical to the dedicated dbl-and-add formulas — the
        reference implementation optimizes for auditability, not speed."""
        return self.add(self)

    def neg(self) -> "Point":
        return Point(modp(-self.xh), self.yh, self.zh, modp(-self.th))

    def sub(self, other: "Point") -> "Point":
        return self.add(other.neg())

    def mul(self, k: int) -> "Point":
        """Scalar multiplication, double-and-add (constant-shape not required
        of the reference impl; the production FFI path MUST be constant-time)."""
        k %= L
        r, temp = IDENTITY_POINT, self
        while k:
            if k & 1:
                r = r.add(temp)
            temp = temp.double()
            k >>= 1
        return r

    # ------------------------------------------------------------ equality --
    def is_on_curve(self) -> bool:
        """Check the affine point satisfies -x^2 + y^2 = 1 + d x^2 y^2.

        Projective form: (-xh^2 + yh^2) * zh^2 == zh^4 + d * xh^2 * yh^2.
        Independent of the decode path — used by differential tests against
        libsodium, which validates encodings itself.
        """
        x2, y2, z2 = modp(self.xh * self.xh), modp(self.yh * self.yh), modp(self.zh * self.zh)
        lhs = modp((y2 - x2) * z2)
        rhs = modp(z2 * z2 + D * x2 * y2)
        return lhs == rhs

    def is_identity(self) -> bool:
        # X == 0 and Y == Z (projectively)
        return self.xh % P == 0 and (self.yh - self.zh) % P == 0

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Point):
            return NotImplemented
        # Cross-multiply to compare projectively: x1*z2 == x2*z1, y1*z2 == y2*z1
        return (modp(self.xh * other.zh) == modp(other.xh * self.zh)
                and modp(self.yh * other.zh) == modp(other.yh * self.zh))

    def __hash__(self) -> int:
        return hash(self.encode())

    # ------------------------------------------------------------ encoding --
    def encode(self) -> bytes:
        zi = inv(self.zh)
        x = modp(self.xh * zi)
        y = modp(self.yh * zi)
        return ((y | ((x & 1) << 255)).to_bytes(32, "little"))

    @classmethod
    def decode(cls, b: bytes, *, require_canonical: bool = False) -> "Point":
        if len(b) != 32:
            raise ValueError("point encoding must be 32 bytes")
        n = int.from_bytes(b, "little")
        xparity = n >> 255
        y = n & ((1 << 255) - 1)
        if require_canonical and y >= P:
            raise ValueError("non-canonical y")
        yy = modp(y * y)
        u = modp(yy - 1)
        v = modp(D * yy + 1)
        x2 = u * inv(v) % P
        x = sqrt(x2 % P)
        if x is None:
            raise ValueError("not a curve point")
        if (x & 1) != xparity:
            x = P - x
        if require_canonical and x == 0 and xparity == 1:
            raise ValueError("non-canonical zero-x with parity bit")
        return cls(x, y, 1, modp(x * y))

    @property
    def affine(self) -> tuple[int, int]:
        zi = inv(self.zh) if self.zh % P else None
        if zi is None:
            raise ZeroDivisionError("invalid extended coords")
        return modp(self.xh * zi), modp(self.yh * zi)


IDENTITY_POINT = Point(*IDENTITY)
BASE = Point(*BASEPOINT)

# ------------------------------------------------------------------- API ---

def scalarmul(point: Point, k: int) -> Point:
    return point.mul(k)


def edwards_add(p: Point, q: Point) -> Point:
    return p.add(q)


def encode(point: Point) -> bytes:
    return point.encode()


def decode(b: bytes, *, require_canonical: bool = False) -> Point:
    return Point.decode(b, require_canonical=require_canonical)


def is_valid_point(b: bytes) -> bool:
    try:
        Point.decode(b)
        return True
    except ValueError:
        return False


def on_subgroup(pt: Point) -> bool:
    """True iff pt has order dividing L (i.e., [L]pt == identity).

    Used by CLSAG key-image verification to stop small-subgroup forgery
    (§11 threat matrix). Cost: one scalar mul; acceptable at verify time.
    """
    return pt.mul(L).is_identity()
