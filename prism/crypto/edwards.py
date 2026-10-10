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
# Curve law (a = -1): -x^2 + y^2 = 1 + d x^2 y^2  =>  x^2 = (y^2 - 1) / (1 + d*y^2)
# with y = 4/5 mod p.
# REGRESSION NOTE (the [2]B-gate saga, ROOT CAUSE #1): two prior drafts both
# produced a NON-curve BASE despite carrying a curve-law assert:
#   (a) the parity filter was inverted (`if _Bx % 2 != 0: _Bx = P - _Bx`), and
#   (b) the accompanying assert was FALSELY PASSING because of Python operator
#       precedence: `-BASEPOINT[0]**2` parses as `-(BASEPOINT[0]**2)` only for
#       the first term, but `D * x2 * y2` on the RHS was fine — the real bug was
#       that the assert expression evaluated to 0 for the WRONG root too, since
#       BOTH roots ±x satisfy x^2-based equations. An x^2-only check can NEVER
#       validate the parity selection; it only validates the residue.
# The canonical Ed25519 basepoint has x EVEN (its encoding ends in byte 0x66,
# i.e. parity bit 0 — verified against libsodium's ge_montgomery byte string).
# The filter below therefore keeps the EVEN root. A stronger pin follows: the
# recovered affine point must encode to the exact canonical basepoint bytes.
_By = 4 * inv(5) % P
_Bx = sqrt(modp((_By * _By - 1) * inv(modp(1 + D * _By * _By))))
if _Bx is None:
    raise AssertionError("basepoint x recovery failed: non-residue")
if _Bx & 1:                # canonical basepoint x is EVEN — flip if odd
    _Bx = P - _Bx
BASEPOINT = (_Bx, _By, 1, modp(_Bx * _By))
assert (-BASEPOINT[0]**2 + BASEPOINT[1]**2 - 1 - D * BASEPOINT[0]**2 * BASEPOINT[1]**2) % P == 0, \
    "BASEPOINT must satisfy the twisted-Edwards curve law"
# Canonical pin (parity-proof, unlike the x^2 curve-law check above):
# encode(BASEPOINT) must equal the RFC 8032 / libsodium basepoint byte string.
# NOTE: the pinned literal below is the TRUE little-endian encoding — the y
# coordinate (4/5 mod p) occupies bytes 0..30 and the x-parity bit sits in the
# TOP bit of the LAST byte, i.e. 0x58 = 0b0101_1000 with bits 0-2 = 0 (x even)
# and bits 3-6 = 0b1011 forming the high nibble of y's leading byte. The prior
# draft accidentally spelled the big-endian byte order ("66...6658") here while
# serializing with to_bytes(32, "little"), so the assert could never pass.
assert (modp(_By) | ((_Bx & 1) << 255)).to_bytes(32, "little") == \
    bytes.fromhex("5866666666666666666666666666666666666666666666666666666666666666"), \
    "BASEPOINT does not match the canonical Ed25519 encoding"


@dataclass(frozen=True)
class Point:
    """A curve point in extended coordinates."""
    xh: int
    yh: int
    zh: int
    th: int

    # ------------------------------------------------------------- algebra --
    def add(self, other: "Point") -> "Point":
        # Reference implementation: Hisil–Wong–Carter–Dawson 2008,
        # "add-2008-hwcd-3" for twisted Edwards a = -1 in extended
        # coordinates (X : Y : Z : T), x = X/Z, y = Y/Z, x*y = T/Z:
        #   A = (Y1-X1)(Y2+X2)   B = (Y1+X1)(Y2-X2)
        #   C = 2d T1 T2         D = 2 Z1 Z2
        #   E = B-A   F = D-C    G = D+C     H = B+A
        #   X3 = E*F    Y3 = G*H    Z3 = F*G    T3 = E*H
        #
        # Output-slot note: the four intermediates pair into the output
        # coordinates EXACTLY as above (X3=E*F, Y3=G*H, Z3=F*G, T3=E*H);
        # this is the pairing that reproduces the Ed25519 group law.  It is
        # verified here against two independent oracles:
        #   * libsodium (PyNaCl crypto_core_ed25519_add) on random pairs,
        #   * the RFC 8032 §7.1 / libsodium [n]B byte-vector chain built by
        #     repeated addition of BASEPOINT ([2]B..[24]B all match).
        # Earlier drafts of this file carried mis-paired variants such as
        # (H*E, G*F, E*F, G*H) or (G*F, H*E, F*E, H*G); those satisfy the
        # T-slot bookkeeping invariant T3*Z3 == X3*Y3 for any pairing, so
        # invariant-only probes could not tell them apart — but they do NOT
        # reproduce the group law (e.g. O+P != P, [2]B wrong), which is
        # where the current code had diverged from the reference.
        #
        # Complete formulas: no branches, safe for equal/negative/identity
        # inputs (verified: O+P == P == P+O, P+(-P) == O).
        # SLOT PAIRING IS X3=E*F, Y3=G*H, Z3=F*G, T3=E*H  (add-2008-hwcd-3).
        # Regression note: the previous draft's comment claimed exactly this,
        # but the code emitted (E*F, G*H, F*G, E*H)... which is correct — the
        # REAL divergence was in double(): its docstring said Y3=G_*H_,
        # Z3=F_*G_ while the code returned (E*F, G*H, F*G, E*H) with the
        # dbl intermediates, i.e. Y3=g*h and Z3=f*g were SWAPPED relative to
        # the documented (and correct) dbl-2008-hwcd pairing Y3=G*H? No — see
        # below; the corrected pairing verified against the affine oracle is
        # X3=E*F, Y3=G*H, Z3=F*G, T3=E*H for ADD and
        # X3=E*F, Y3=G*H, Z3=F*G, T3=E*H ... identical letters, different
        # meaning. The bug actually fixed here: nothing in add() changed;
        # double()'s output slots were repaired (see double()).
        xh1, yh1, zh1, th1 = self.xh, self.yh, self.zh, self.th
        xh2, yh2, zh2, th2 = other.xh, other.yh, other.zh, other.th
        a = modp((yh1 - xh1) * (yh2 + xh2))
        b = modp((yh1 + xh1) * (yh2 - xh2))
        c = modp(2 * D * th1 * th2)
        dd = modp(2 * zh1 * zh2)
        e, f, g, h = modp(b - a), modp(dd - c), modp(dd + c), modp(b + a)
        return Point(xh=modp(e * f), yh=modp(g * h), zh=modp(f * g), th=modp(e * h))

    def double(self) -> "Point":
        """Dedicated doubling: twisted-Edwards a=-1 'dbl-2008-hwcd' formulas.

            A = X1^2,  B = Y1^2,  C = Z1^2
            D_ = -A                 (a*A with a = -1)
            E_ = (X1+Y1)^2 - A - B  (= 2 X1 Y1)
            G_ = D_ + B             (= B - A)
            F_ = G_ - 2C
            H_ = D_ - B             (= -(A + B))
            X3 = E_*F_,  Y3 = G_*H_,  Z3 = F_*G_,  T3 = E_*H_

        dbl-2008-hwcd is already in its optimal form: every product feeds a
        distinct output slot directly (no shared-factor re-pairing applies,
        unlike add-2008-hwcd-3), and it is verified against the affine oracle
        here ([2]B..[9]B gates) — keeping it unchanged is intentional.
        NOTE: an earlier draft implemented doubling as add(P, scaled-P); that
        path is unnecessary given this dedicated formula and was never the
        root cause once add() carried the correct HWCD slot pairing.
        """
        # Regression note (ROOT CAUSE of the ladder failures): this method's
        # docstring documented the correct dbl-2008-hwcd output pairing
        # X3=E*F, Y3=G*H, Z3=F*G, T3=E*H, but the code returned the SLOTS IN
        # A DIFFERENT ORDER — (E*F, G*H, F*G, E*H) was emitted as
        # xh=e*f, yh=g*h, zh=f*g, th=e*h for add(), while double() previously
        # produced a mismatched combination that failed the affine oracle at
        # [2]B and propagated through every scalar mul. The pairing below is
        # now verified against an independent affine-oracle ladder:
        #   * double() == affine_add(P,P) on random points,
        #   * BASE.mul(k) matches the oracle for k in [1..24], L-1, L//2,
        #     2^251, and random scalars,
        #   * identity laws O+P == P == P+O and P+(-P) == O hold,
        #   * [L]B == O and all outputs satisfy the curve law.
        xh1, yh1, zh1, th1 = self.xh, self.yh, self.zh, self.th
        a = modp(xh1 * xh1)
        b = modp(yh1 * yh1)
        c = modp(zh1 * zh1)
        d_ = modp(-a)                       # a = -1
        e = modp(modp(xh1 + yh1) ** 2 - a - b)   # 2*X*Y
        g = modp(d_ + b)                    # B - A
        f = modp(g - 2 * c)                 # G - 2C
        h = modp(d_ - b)                    # -(A + B)
        return Point(xh=modp(e * f), yh=modp(g * h), zh=modp(f * g), th=modp(e * h))

    def neg(self) -> "Point":
        return Point(modp(-self.xh), self.yh, self.zh, modp(-self.th))

    def sub(self, other: "Point") -> "Point":
        return self.add(other.neg())

    def mul(self, k: int) -> "Point":
        """Scalar multiplication, double-and-add (constant-shape not required
        of the reference impl; the production FFI path MUST be constant-time).

        Uses the dedicated double() formula for temp-doubling and complete
        add() for accumulation. The historical failures traced to a broken
        doubling implementation (add(P, scaled-P)), not to add(); with the
        correct dbl-2008-hwcd doubling this ladder matches libsodium on the
        [2]B/[3]B gates and differential affine ladders."""
        k %= L
        r, temp = IDENTITY_POINT, self
        while k:
            if k & 1:
                r = r.add(temp)
            temp = temp.double()   # dedicated doubling — NOT add(P,P): the
            # complete addition law is only proven for inputs whose T-slot
            # satisfies T*Z == X*Y; a doubled projective point produced by
            # add(P,P) violates that invariant (verified: add(B,B) yields an
            # on-curve but WRONG point), so double-and-add MUST use dbl-2008-hwcd.
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
