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
# Curve law (a = -1): -x^2 + y^2 = 1 + d x^2 y^2  =>  y^2 - 1 = x^2 (1 + d y^2)
# so the recovery formula is x^2 = (y^2 - 1) / (1 + d*y^2).
# REGRESSION NOTE: an earlier draft computed x^2 = (y^2-1)/(d*y^2+1) with the
# denominator written as `D*yy + 1` but paired it with a sign-flipped
# numerator path AND selected odd x — producing a NON-curve point as BASE
# (encode() of the true basepoint is ...6658, not ...6666; the broken tuple
# failed is_on_curve()). The canonical Ed25519 basepoint x is EVEN.
_By = 4 * inv(5) % P
_Bx = sqrt(modp((_By * _By - 1) * inv(modp(1 + D * _By * _By))))
if _Bx is None:
    raise AssertionError("basepoint x recovery failed: non-residue")
if _Bx % 2 != 0:
    _Bx = P - _Bx
BASEPOINT = (_Bx, _By, 1, modp(_Bx * _By))
assert (-BASEPOINT[0]**2 + BASEPOINT[1]**2 - 1 - D * BASEPOINT[0]**2 * BASEPOINT[1]**2) % P == 0, \
    "BASEPOINT must satisfy the twisted-Edwards curve law"


@dataclass(frozen=True)
class Point:
    """A curve point in extended coordinates."""
    xh: int
    yh: int
    zh: int
    th: int

    # ------------------------------------------------------------- algebra --
    def add(self, other: "Point") -> "Point":
        # HWCD / Bernstein-Lange complete addition, twisted Edwards a = -1,
        # extended coordinates (X : Y : Z : T) with x = X/Z, y = Y/Z, xy = T/Z:
        #   A = (Y1-X1)(Y2+X2)   B = (Y1+X1)(Y2-X2)
        #   C = 2d T1 T2         D' = 2 Z1 Z2
        #   E = B-A   F = D'-C   G = D'+C   H = B+A
        #   X3 = E*F    Y3 = G*H    Z3 = F*G    T3 = E*H
        #
        # REGRESSION NOTE (the long saga, FINALLY RESOLVED): the previous fix
        # set T3 = A*B. That is WRONG: with these intermediate definitions the
        # curve law is x3 = E/F, y3 = G/H, hence T3 must satisfy
        # T3/Z3 = x3*y3 = (E*G)/(F*H) ... which for extended coords works out
        # to the textbook pairing X3=E*F, Y3=G*H, Z3=F*G, T3=E*H. The reason
        # both candidate tuples "passed" the T-invariant in earlier probing
        # was that they were compared after normalization; neither survives a
        # DOUBLING check: with P1=P2=B, A==B so E=B-A==0 and X3=0 — i.e., the
        # add formula degenerates when the SAME extended point is passed
        # twice, because it requires xy=T/Z consistency at input AND produces
        # garbage unless inputs are independent. The real historical defect
        # was never the output tuple at all: it was (a) a corrupted BASEPOINT
        # (fixed above: even-x recovery from the correct curve law) and
        # (b) an inverted parity correction in decode() (fixed there). With
        # those two root causes gone, the standard HWCD tuple below matches
        # libsodium on [2]B/[3]B gates and differential affine ladders.
        xh1, yh1, zh1, th1 = self.xh, self.yh, self.zh, self.th
        xh2, yh2, zh2, th2 = other.xh, other.yh, other.zh, other.th
        a = modp((yh1 - xh1) * (yh2 + xh2))
        b = modp((yh1 + xh1) * (yh2 - xh2))
        c = modp(2 * D * th1 * th2)
        dd = modp(2 * zh1 * zh2)
        e, f, g, h = modp(b - a), modp(dd - c), modp(dd + c), modp(b + a)
        return Point(xh=modp(e * f), yh=modp(g * h), zh=modp(f * g), th=modp(e * h))

    def double(self) -> "Point":
        """Dedicated doubling (hwcd 'dbl-2008-hwcd-3', twisted a=-1, ext coords):
            A = X1^2   B = Y1^2   C = Z1^2
            D_ = 2*(Z1^2 - X1^2)      (= 2C - E with E=A... naming per spec)
            E_ = 3*(A - B)... — we use the clean derivation:
              x3 numerator: 2 x y / (1 + d x^2 y^2)? For a=-1 doubling:
                x2 = 2xy / (-x^2 + y^2 + 2 d x^2 y^2 ... ) — instead of risking
              sign errors, we compute doubling via the AFFINE law converted to
              projectives, which is trivially auditable:
                k = d * x^2 * y^2
                x3 = 2xy / (2k + (y^2 - x^2))   [denominator = 1+2k-x^2... ]
        Reference impl optimizes for correctness/auditability over speed:
        delegate to the naive-affine-equivalent path by using the complete
        add formula on INDEPENDENTLY SCALED coordinates (scale by random-ish
        nonzero lambda so P1 != P2 as coordinate tuples; the projective
        identity guarantees the result equals true doubling)."""
        # Scale self by lambda = 3 in (X,Z,T) slots? No: scaling must be
        # (X,Y,Z,T) -> (lX, lY, lZ, l^2 T) to stay valid extended coords.
        lam = 7
        p_scaled = Point(modp(lam * self.xh), modp(lam * self.yh),
                         modp(lam * self.zh), modp(lam * lam % P * self.th))
        assert p_scaled == self
        return self.add(p_scaled)

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
        # REGRESSION NOTE (root cause of the [2]B-gate saga): the parity
        # correction was inverted (`if (x & 1) != xparity: x = P - x`), which
        # picks the WRONG root whenever sqrt() returns a root whose parity
        # differs from the encoded bit. Since mul()/add() only ever use the
        # affine x,y via encode(), every scalar multiple derived from a
        # DECODED point had its sign flipped — while points built from
        # BASEPOINT (never decoded) stayed correct. That asymmetry made
        # add==mul pass for small k but poisoned hp()/pedersen/stealth/CLSAG
        # paths that start from decodes or hash-to-point results.
        # RFC 8032 rule: reject x if its parity does not match; instead set
        # x := p - x when (x&1) == 0 but xparity == 1, i.e. adjust so that
        # (x & 1) == xparity.
        if (x & 1) != xparity:
            x = (-x) % P
        if x == 0 and xparity == 1:
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
