"""Scalar-field arithmetic for the PLONK circuits — spec §5.3, Decision D2.

PLONK arithmetizes over a prime field F_q that admits a 2^k-th principal root
of unity for the evaluation domain. The Ed25519 scalar field F_L does NOT
(q-1 = 2^2 * odd), so production Prism uses the BN254 scalar field
Fr = 2^254 + 425681067102064642806745628926507629166072017354... - 1
(bls12-377 / bw6-761 family per the halo2 ultra-honk candidate list).

This module is the readable pure-Python reference field; the Rust/FFI path
(Phase 2 hardhat) must agree on every test vector in tests/test_zk.py.

Bridge to crypto/: Pedersen commitments (C = v·H + r·G over Ed25519) are
*re-stated inside* the circuits as in-circuit commitments
    C' = [v]·HG + [r]·GG          (HG, GG: circuit-group generators)
with v, r treated as Fr elements. Because every honest witness value fits in
uint64 shards (< 2^64 << q) and every blinding scalar the wallet produces is
an Ed25519 scalar reduced into Fr *without* mod-L wraparound (see
`from_ed_scalar`, which asserts the scalar is < min(L, q)), cross-group
equality checks performed by the verifier compare canonical byte encodings of
the same (v, r) opening — not raw curve points. This keeps the reference
honest about what it does NOT prove (no cross-group DLog assumption is smuggled
in beyond what §5.3 already assumes).
"""
from __future__ import annotations

# BN254 scalar field modulus (Fr of alt_bn128). Prime, q ≡ 1 mod 2^28.
Q = (
    21888242871839275222246405745257275088548364400416034343698204186575808495617
)

assert Q.bit_length() == 254 and Q > (1 << 253)
# Independent primality guard (Miller-Rabin with fixed small bases — same
# spirit as crypto/field.py's structural asserts; a non-prime "Fr" would make
# every gate identity in plonk.py unsound).
def _is_prime(n: int) -> bool:
    if n < 2 or n % 2 == 0:
        return n == 2
    d, s = n - 1, 0
    while d % 2 == 0:
        d //= 2
        s += 1
    for a in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        x = pow(a, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(s - 1):
            x = x * x % n
            if x == n - 1:
                break
        else:
            return False
    return True


assert _is_prime(Q), "BN254 Fr modulus must be prime"
# 2-adicity check: q - 1 divisible by 2^28 (domain size headroom for v1 circuits)
_QM1 = Q - 1
_TWOS_ADICITY = 0
_t = _QM1
while _t % 2 == 0:
    _t //= 2
    _TWOS_ADICITY += 1
assert _TWOS_ADICITY >= 28, "Fr must admit large power-of-two domains"

# Principal roots of unity: g = generator of Fr*, TWO_ADIC_ROOT[k] is a
# primitive 2^k-th root of unity. 5 is a multiplicative generator of Fr*.
_GENERATOR = 5
TWO_ADIC_ROOT = pow(_GENERATOR, _t, Q)   # primitive 2^28-th root of unity


def two_adic_root(log_n: int) -> int:
    """Primitive 2^log_n-th root of unity."""
    if not (0 <= log_n <= _TWOS_ADICITY):
        raise ValueError(f"domain 2^{log_n} exceeds 2-adicity {_TWOS_ADICITY}")
    k = pow(TWO_ADIC_ROOT, 1 << (_TWOS_ADICITY - log_n), Q)
    assert pow(k, 1 << log_n, Q) == 1
    return k


def inv(x: int) -> int:
    if x % Q == 0:
        raise ZeroDivisionError("no inverse for 0 mod q")
    return pow(x, Q - 2, Q)


def add(a: int, b: int) -> int:
    return (a + b) % Q


def sub(a: int, b: int) -> int:
    return (a - b) % Q


def mul(a: int, b: int) -> int:
    return (a * b) % Q


def div(a: int, b: int) -> int:
    return mul(a, inv(b))


def neg(a: int) -> int:
    return (-a) % Q


def pow_(a: int, e: int) -> int:
    return pow(a, e, Q)


# ---------------------------------------------------------------------------
# Canonical encoding (wire format): fixed 32-byte big-endian, like fr32 in
# gnark/halo2. Domain-separated hashes always consume the canonical form.
# ---------------------------------------------------------------------------

def to_bytes(x: int) -> bytes:
    return (x % Q).to_bytes(32, "big")


def from_bytes(b: bytes) -> int:
    if len(b) != 32:
        raise ValueError("fr element encodes to exactly 32 bytes (big-endian)")
    x = int.from_bytes(b, "big")
    if x >= Q:
        raise ValueError("non-canonical fr encoding")
    return x


def reduce_le(b: bytes) -> int:
    """Little-endian byte string of ANY length -> Fr element (hash-to-scalar)."""
    return int.from_bytes(b, "little") % Q


# ---------------------------------------------------------------------------
# Bridge from crypto/ (Ed25519 scalars mod L) into Fr
# ---------------------------------------------------------------------------

def from_ed_scalar(s: int, *, context: str = "") -> int:
    """Lift an Ed25519 scalar into Fr WITHOUT modular wraparound.

    Honest constraint: this only accepts s < L < Q… except L (7.2e60) is
    actually SMALLER than Q (2.2e76), so every canonical Ed25519 scalar lifts
    injectively. The assert documents the invariant rather than defending a
    failure mode: if a future field swap made L > Q, silent mod-Q reduction
    would break the commitment re-statement bridge (doc header), so we fail
    loudly instead.
    """
    if not (0 <= s):
        raise ValueError(f"negative scalar {context}")
    if s >= Q:
        raise ValueError(
            f"scalar too large for injective lift into Fr {context}; "
            "witnesses must be generated below min(L, Q)"
        )
    return s


# ---------------------------------------------------------------------------
# Polynomial helpers (coefficient form, low degree only — v1 circuits need
# deg ≤ 3 gates; Lagrange interpolation is used for the public-input columns)
# ---------------------------------------------------------------------------

def poly_eval(coeffs: list[int], x: int) -> int:
    """Horner evaluation, coeffs[0] + coeffs[1]x + ..."""
    y = 0
    for c in reversed(coeffs):
        y = add(mul(y, x), c)
    return y


def lagrange_basis(domain: list[int], i: int, x: int) -> int:
    """L_i(x) over an explicit domain (small n; reference only)."""
    num = 1
    den = 1
    for j, w in enumerate(domain):
        if j == i:
            continue
        num = mul(num, sub(x, w))
        den = mul(den, sub(domain[i], w))
    return div(num, den)


def lagrange_coeffs_at(domain: list[int], values: list[int]) -> list[int]:
    """Interpolate polynomial (coefficient form) through (domain[i], values[i])."""
    n = len(domain)
    out = [0] * n
    for i in range(n):
        # build L_i numerator polynomial
        num = [1]
        den = 1
        for j in range(n):
            if i == j:
                continue
            num = poly_mul_linear(num, domain[j])
            den = mul(den, sub(domain[i], domain[j]))
        scale = div(values[i], den)
        num = [mul(c, scale) for c in num]
        out = poly_add(out, num)
    return out


def poly_mul_linear(coeffs: list[int], root: int) -> list[int]:
    """Multiply coeffs by (x - root)."""
    out = [0] * (len(coeffs) + 1)
    for i, c in enumerate(coeffs):
        out[i + 1] = add(out[i + 1], c)
        out[i] = add(out[i], mul(c, neg(root)))
    return out


def poly_add(a: list[int], b: list[int]) -> list[int]:
    n = max(len(a), len(b))
    return [add(a[i] if i < len(a) else 0, b[i] if i < len(b) else 0) for i in range(n)]
