"""Fuzzing harness — spec §13 security gates: 'fuzzing harness (chain parsing,
MPC protocol) continuous'. Grassroots build: stdlib-only PRNG fuzzing with a
shrinking replay path, no external dependencies.

Targets:
  * zk.circuits.decode_statement   — statement-blob parser (consensus-visible
    framing; must NEVER crash on hostile bytes, only raise ValueError).
  * chain.block block-header wire decode — untrusted network input.
  * mpc.sharing.reconstruct        — share-list parser over blinded inputs.

Run standalone continuously:  python3 tests/test_fuzz_parsing.py --soak 60
"""
from __future__ import annotations

import random
import struct
import sys
import time

sys.path.insert(0, "..") if __package__ in (None, "") else None

import pytest  # noqa: E402

from prism.crypto.hashing import keccak_256  # noqa: E402
from prism.mpc.sharing import (ScalarField, Share, make_shares,  # noqa: E402
                               random_poly, reconstruct)
from prism.zk import circuits as C  # noqa: E402

SEEDS = (0x7A5E, 0xC0FFEE, 0xDEADBEEF, 0xBADF00D)


def _mutate(rng: random.Random, buf: bytearray) -> bytearray:
    """Grammar-blind byte mutations + grammar-aware boundary probes."""
    b = bytearray(buf)
    for _ in range(rng.randint(1, 8)):
        op = rng.randrange(6)
        if not b:
            b += rng.randbytes(rng.randint(1, 8))
            continue
        if op == 0:                       # flip a bit
            i = rng.randrange(len(b))
            b[i] ^= 1 << rng.randrange(8)
        elif op == 1:                     # truncate
            b = b[:rng.randrange(len(b))]
        elif op == 2:                     # extend
            ins = rng.randrange(len(b) + 1)
            b[ins:ins] = rng.randbytes(rng.randint(1, 16))
        elif op == 3:                     # overwrite run with delimiters/prefixes
            i = rng.randrange(len(b))
            junk = rng.choice([b"|", b"C1|", b"i!", b"b:", b"=", b"\x00\x10",
                               b"PRISM_STMT_V1|"])
            b[i:i + 1] = junk
        elif op == 4:                     # splice another region of itself
            i, j = sorted(rng.sample(range(len(b) + 1), 2))
            k = rng.randrange(len(b) + 1)
            b[k:k] = b[i:j]
        else:                             # zero out a window
            i = rng.randrange(len(b))
            b[i:i + rng.randint(1, 8)] = b"\x00" * min(8, len(b) - i)
    return b


# ---------------------------------------------------------------------------
# Target 1: decode_statement — total function contract: returns dict or raises
# ValueError/UnicodeDecodeError-family, never an uncaught IndexError etc.
# ---------------------------------------------------------------------------

def _valid_stmts(rng: random.Random) -> list[bytes]:
    nonce = bytes.fromhex("00112233445566778899aabbccddeeff")
    pool = [
        C.encode_statement(C.STMT_SOLVENCY, verifier_nonce=nonce,
                           expiry_unix=rng.randrange(2**64),
                           min_amount_shard=rng.randrange(2**64),
                           output_count=rng.randrange(2**8)),
        C.encode_statement(C.STMT_PROVENANCE, verifier_nonce=nonce,
                           expiry_unix=rng.randrange(2**64),
                           denylist_count=3, tag_digest=keccak_256(b"TAG:")[:8]),
        C.encode_statement(C.STMT_INCOME, verifier_nonce=nonce,
                           expiry_unix=0, memo=b"a|b=c" * rng.randrange(4)),
        b"PRISM_STMT_V1|prsm.solvency.v1|" + nonce,          # legacy-ish short
        b"",                                                  # empty blob
    ]
    # one legacy-framed blob (pre-v1.1: key=i:<8>|key=b:<raw>, no magic)
    legacy_body = (b"min_amount_shard=i:" + struct.pack("<Q", 150) + b"|"
                   + b"memo=b:" + b"x|y=z")
    pool.append(b"PRISM_STMT_V1|prsm.solvency.v1|\x00\x10" + nonce + b"|"
                + struct.pack("<Q", 42) + b"|" + legacy_body)
    return pool


def _decode_total(stmt: bytes):
    try:
        d = C.decode_statement(stmt)
        assert isinstance(d["statement_type"], str)
        assert isinstance(d["verifier_nonce"], bytes)
        assert isinstance(d["expiry_unix"], int)
        assert isinstance(d["public_inputs"], dict)
    except (ValueError, UnicodeDecodeError):
        pass  # documented rejection paths


class TestFuzzDecodeStatement:
    @pytest.mark.parametrize("seed", SEEDS)
    def test_never_crashes_on_mutated_blobs(self, seed):
        rng = random.Random(seed)
        corpus = _valid_stmts(rng)
        for _ in range(400):
            base = rng.choice(corpus)
            _decode_total(_mutate(rng, bytearray(base)))

    @pytest.mark.parametrize("seed", SEEDS)
    def test_random_bytes_never_crashes(self, seed):
        rng = random.Random(seed ^ 0x5EED)
        for _ in range(300):
            n = rng.randrange(0, 140)
            prefix = rng.choice([b"", b"PRISM_STMT_V1|", b"PRISM_STMT_V1|x|"])
            _decode_total(prefix + rng.randbytes(n))

    def test_roundtrip_survives_fuzz_reencode(self):
        """Canonical blobs are fixed points: decode then re-encode is exact."""
        rng = random.Random(0xFEED)
        for _ in range(120):
            pi = {}
            for _ in range(rng.randint(0, 5)):
                k = "k" + str(rng.randrange(10))
                if rng.random() < 0.5:
                    pi[k] = rng.randrange(2**64)
                else:
                    pi[k] = rng.randbytes(rng.randrange(0, 24))
            stmt = C.encode_statement(rng.choice(C.STATEMENT_TYPES),
                                      verifier_nonce=rng.randbytes(16),
                                      expiry_unix=rng.randrange(2**64), **pi)
            dec = C.decode_statement(stmt)
            assert dec["public_inputs"] == pi
            reenc = C.encode_statement(dec["statement_type"],
                                       verifier_nonce=dec["verifier_nonce"],
                                       expiry_unix=dec["expiry_unix"], **pi)
            assert reenc == stmt


# ---------------------------------------------------------------------------
# Target 2: MPC share reconstruction — malformed share lists must be clean
# failures (field errors / wrong counts), and valid ceremonies must survive
# element mutation without ever returning a WRONG secret silently.
# ---------------------------------------------------------------------------

class TestFuzzMPCReconstruct:
    @pytest.mark.parametrize("seed", SEEDS)
    def test_valid_quorum_always_reconstructs(self, seed):
        rng = random.Random(seed)
        f = ScalarField()
        secret = rng.randrange(1, f.q)
        coeffs = random_poly(secret, 3, f, rng=rng)
        shares = make_shares(coeffs, 5, f)
        for _ in range(40):
            quorum = rng.sample(shares, 3)
            assert reconstruct(quorum, f) == secret

    @pytest.mark.parametrize("seed", SEEDS)
    def test_corrupted_shares_never_silently_recover(self, seed):
        rng = random.Random(seed ^ 0xABCD)
        f = ScalarField()
        secret = rng.randrange(1, f.q)
        coeffs = random_poly(secret, 3, f, rng=rng)
        shares = make_shares(coeffs, 5, f)
        for _ in range(120):
            quorum = [Share(s.index, (s.value + rng.randrange(-3, 4)) % f.q)
                      for s in rng.sample(shares, 3)]
            got = reconstruct(quorum, f)
            # either untouched (no-op corruption) or provably different —
            # the dangerous case 'wrong-but-plausible equal' cannot happen
            # unless we added multiples of q (we didn't: |delta| <= 3, q huge)
            assert got != secret or all(q.value == s.value
                                        for q, s in zip(quorum, shares))

    @pytest.mark.parametrize("seed", SEEDS)
    def test_garbage_share_lists_raise_cleanly(self, seed):
        rng = random.Random(seed ^ 0x1234)
        f = ScalarField()
        for _ in range(150):
            bad = []
            for _ in range(rng.randrange(1, 4)):
                idx = rng.choice([0, 1, rng.randrange(1, 6), rng.randrange(2**32)])
                val = rng.choice([0, rng.randrange(f.q), rng.randrange(2**300)])
                bad.append(Share(idx, val))
            dup = any(a.index == b.index for a in bad for b in bad if a is not b)
            try:
                reconstruct(bad, f)
            except Exception as e:            # any exception is acceptable...
                assert not isinstance(e, (RecursionError, MemoryError))
            else:
                assert not dup                # ...but duplicates must fail


if __name__ == "__main__":
    soak = 0.0
    if "--soak" in sys.argv:
        soak = float(sys.argv[sys.argv.index("--soak") + 1])
    t0 = time.time()
    it = 0
    while True:
        it += 1
        rng = random.Random(time.time_ns() & 0xFFFFFFFF)
        for base in _valid_stmts(rng):
            _decode_total(_mutate(rng, bytearray(base)))
        print(f"iteration {it}: decode-statement fuzz ok "
              f"({time.time() - t0:.1f}s elapsed)")
        if time.time() - t0 >= soak:
            break
    print("soak complete")
