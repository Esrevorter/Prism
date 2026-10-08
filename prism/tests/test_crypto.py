"""prism/tests/test_crypto.py — crypto layer acceptance tests (spec.md §5, §13 P1).

Coverage (per the Phase-1 gate agreed with the founders):
  * Keccak-256/512 known-answer tests + differential fuzz vs pycryptodome
    across lengths {0,1,135,136,137,200,256} — the rho/pi lane-convention fix
    gate. NOTE: hash_to_scalar is keccak_512 -> mod L (RFC 8032 style), so we
    assert against THAT definition rather than a keccak_256 reduction.
  * hp()-style image base membership (pedersen.H and clsag.hp outputs lie in
    <H>, order L) — our H_p is scalar·H by construction, not SSWU (R1).
  * Ed25519 group ops vs RFC 8032 anchors (basepoint order, encode/decode).
  * Pedersen C = [v]G + [a]H binding, homomorphism, balance-check vector.
  * Stealth address round-trip: sender path and recipient scan path produce
    the same one-time key; x·G == P algebraic identity.
  * CLSAG sign/verify happy path + tamper rejection (message, response, ring,
    image) + linkability of key images + serialize/deserialize round-trip.
"""

from __future__ import annotations

import hashlib
import os
import random

import pytest

from prism.crypto.clsag import ClsagSignature, hp as clsag_hp, sign, verify
from prism.crypto.edwards import BASE, Point, decode, encode, on_subgroup
from prism.crypto.field import L, scalar_reduce
from prism.crypto.hashing import hash_to_scalar, keccak_256, keccak_512
from prism.crypto.pedersen import G, H, blind_hash, commit, parse_commitment, verify_opening
from prism.crypto.stealth import (
    compute_shared_secret,
    compute_shared_secret_receiver,
    derive_stealth_address,
    generate_tx_key_pair,
    keypair_from_seed,
    key_image,
    key_image_matches,
    one_time_private_key,
    public_key,
)

# ---------------------------------------------------------------------------
# Keccak: known answers (original Keccak padding 0x01, NOT SHA3's 0x06)
# ---------------------------------------------------------------------------

KECCAK256_EMPTY = "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"
KECCAK256_ABC = "4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45"
KECCAK512_EMPTY = (
    "0eab42de4c3ceb9235fc91acffe746b29c29a8c366b7c60e4e67c466f36a4304"
    "c00fa9caf9d87976ba469bcbe06713b435f091ef2769fb160cdab33d3670680e"
)


def test_keccak256_known_answers():
    assert keccak_256(b"").hex() == KECCAK256_EMPTY
    assert keccak_256(b"abc").hex() == KECCAK256_ABC


def test_keccak512_known_answer():
    assert keccak_512(b"").hex() == KECCAK512_EMPTY


@pytest.mark.parametrize("n", [0, 1, 135, 136, 137, 200, 256])
def test_keccak_differential_pycryptodome(n):
    """Gate from the rho/pi flat-lane fix: byte-exact vs Crypto.Hash.keccak."""
    try:
        from Crypto.Hash import keccak as _pk
    except ImportError:  # pragma: no cover
        pytest.skip("pycryptodome not installed")
    rng = random.Random(0xC0FFEE + n)
    msgs = [bytes(rng.randrange(256) for _ in range(n)) for _ in range(2)]
    if n == 0:
        msgs.append(b"")
    if n == 1:
        msgs += [b"a", b"\x00"]
    for m in msgs:
        for bits, fn in ((256, keccak_256), (512, keccak_512)):
            k = _pk.new(digest_bits=bits)
            k.update(m)
            assert fn(m) == k.digest(), f"keccak{bits} mismatch at len {n}"


def test_keccak_not_sha3_padding():
    """Accidental swap to SHA3 domain must be caught loudly."""
    assert keccak_256(b"") != hashlib.sha3_256(b"").digest()
    assert keccak_512(b"abc") != hashlib.sha3_512(b"abc").digest()


def test_keccak_multiblock_absorb():
    """Regression guard: messages spanning >1 rate block are fully absorbed."""
    m = bytes(range(256)) + bytes(range(100))  # 356B > rate(136)*2
    assert keccak_256(m) != keccak_256(m[:136])
    assert keccak_256(m) == keccak_256(bytes(m))  # deterministic


# ---------------------------------------------------------------------------
# hash_to_scalar — defined as keccak_512 -> little-endian int -> mod L
# ---------------------------------------------------------------------------

def test_hash_to_scalar_reduces_mod_l():
    for data in (b"", b"prism", keccak_256(b"x"), os.urandom(200)):
        s = hash_to_scalar(data)
        assert isinstance(s, int) and 0 <= s < L


def test_hash_to_scalar_matches_definition():
    d = b"prism-test-vector"
    expect = int.from_bytes(keccak_512(d), "little") % L
    assert hash_to_scalar(d) == expect


# ---------------------------------------------------------------------------
# Image base / hash-to-point membership (our H_p is scalar·H — spec R1 note)
# ---------------------------------------------------------------------------

def test_image_base_h_has_prime_order():
    assert not H.is_identity()
    assert H.mul(L).is_identity()
    assert H.is_on_curve()
    assert on_subgroup(H)


def test_clsag_hp_lands_in_subgroup_and_is_domain_separated():
    P = public_key(12345)
    q = clsag_hp(P)
    assert q.mul(L).is_identity()
    assert q != H                      # different message -> different point
    assert q == clsag_hp(P)            # deterministic
    other = clsag_hp(public_key(12346))
    assert q != other                  # domain-separated by input point


# ---------------------------------------------------------------------------
# Ed25519 group ops (RFC 8032 anchors)
# ---------------------------------------------------------------------------

def test_basepoint_order_and_identity():
    assert BASE.mul(L).is_identity()
    assert BASE.is_on_curve()
    assert decode(encode(BASE)) == BASE
    ident_enc = (1).to_bytes(32, "little")
    assert decode(ident_enc).is_identity()


def test_encode_decode_roundtrip_random_scalars():
    rng = random.Random(7)
    for _ in range(5):
        s = rng.randrange(1, L)
        p = BASE.mul(s)
        assert decode(encode(p)) == p


def test_group_law_consistency():
    rng = random.Random(11)
    r1, r2 = rng.randrange(1, L), rng.randrange(1, L)
    assert BASE.mul(r1).add(BASE.mul(r2)) == BASE.mul((r1 + r2) % L)
    assert BASE.mul(r1).sub(BASE.mul(r2)) == BASE.mul((r1 - r2) % L)
    assert BASE.mul(r1).neg() == BASE.mul((-r1) % L)


def test_invalid_point_decode_rejected():
    """Non-curve encodings must raise (small-subgroup / garbage gate)."""
    bad2 = bytes([0xFF] * 32)
    with pytest.raises(ValueError):
        decode(bad2, require_canonical=True)


# ---------------------------------------------------------------------------
# Pedersen commitments
# ---------------------------------------------------------------------------

def test_generators_distinct_and_prime_order():
    assert G != H
    assert G.mul(L).is_identity() and H.mul(L).is_identity()


def test_commitment_homomorphism():
    c1 = commit(1000, 11)
    c2 = commit(2000, 22)
    combined = c1.add(c2)
    direct = commit(3000, (11 + 22) % L)
    assert combined == direct


def test_blinding_changes_commitment():
    assert commit(50_000, 1) != commit(50_000, 2)


def test_balance_check_vector():
    """sum(in) == sum(out) iff values AND blinds cancel (RingCT core check)."""
    ins = [(1000, 11), (2500, 22)]
    outs = [(3000, 33), (500, 44)]
    total = commit(ins[0][0], ins[0][1])
    for v, a in ins[1:]:
        total = total.add(commit(v, a))
    for v, a in outs:
        total = total.sub(commit(v, a))
    assert total.is_identity()


def test_verify_opening_accepts_and_rejects():
    mask = blind_hash(b"mask-seed")
    c = commit(777, mask)
    assert verify_opening(c, 777, mask)
    assert not verify_opening(c, 778, mask)
    assert not verify_opening(c, 777, (mask + 1) % L)


def test_parse_commitment_roundtrip():
    c = commit(123, 456)
    assert parse_commitment(encode(c)) == c


# ---------------------------------------------------------------------------
# Stealth addresses: two derivation paths must agree
# ---------------------------------------------------------------------------

def test_stealth_roundtrip_sender_and_scan_paths_agree():
    a, A = keypair_from_seed(b"view-key")      # view secret/pub
    b, B = keypair_from_seed(b"spend-key")     # spend secret/pub
    r, R = generate_tx_key_pair()

    e_sender = compute_shared_secret(r, A)
    e_scan = compute_shared_secret_receiver(a, R)
    assert e_sender == e_scan                  # ECDH commutativity

    P = derive_stealth_address(e_sender, B)
    x = one_time_private_key(e_scan, a, b)
    assert public_key(x) == P                  # x·G == P: recipient can spend


def test_stealth_one_time_keys_unique_per_tx():
    _, A = keypair_from_seed(b"vk")
    _, B = keypair_from_seed(b"sk")
    r1, _ = generate_tx_key_pair()
    r2, _ = generate_tx_key_pair()
    p1 = derive_stealth_address(compute_shared_secret(r1, A), B)
    p2 = derive_stealth_address(compute_shared_secret(r2, A), B)
    assert p1 != p2


def test_key_image_linkable_and_wellformed():
    x = scalar_reduce(keccak_256(b"output-secret")) or 1
    I1 = key_image(x)
    I2 = key_image(x)
    assert I1 == I2                            # linkable across spends
    P = public_key(x)
    assert key_image_matches(P, I1)
    J = key_image((x + 1) % L)
    assert J != I1                             # distinct secrets -> distinct images


# ---------------------------------------------------------------------------
# CLSAG: sign/verify, tamper rejection, linkability, wire round-trip
# ---------------------------------------------------------------------------

def _ring(n: int, seed: int) -> tuple[list[bytes], list[int]]:
    rng = random.Random(seed)
    secrets = [rng.randrange(1, L) for _ in range(n)]
    encs = [encode(public_key(s)) for s in secrets]
    return encs, secrets


def _det_rng(seed: int = 0):
    state = {"t": seed}

    def nxt() -> int:
        state["t"] += 1
        return scalar_reduce(keccak_256(b"det-nonce-%d" % state["t"])) or 1

    return nxt


RING_SIZE = 16  # spec §5.1 minimum ring size


def test_clsag_sign_verify_happy_path_all_indices():
    encs, secrets = _ring(RING_SIZE, 1)
    msg = keccak_256(b"ringct-message")
    for i in (0, 3, RING_SIZE - 1):
        sig = sign(secrets[i], i, encs, msg, _det_rng(i))
        assert verify(sig, encs, msg), f"index {i} failed"


def test_clsag_wrong_message_rejected():
    encs, secrets = _ring(RING_SIZE, 2)
    msg = keccak_256(b"honest")
    sig = sign(secrets[5], 5, encs, msg, _det_rng())
    assert not verify(sig, encs, keccak_256(b"different"))


def test_clsag_tampered_response_rejected():
    encs, secrets = _ring(RING_SIZE, 3)
    msg = keccak_256(b"ringct")
    sig = sign(secrets[7], 7, encs, msg, _det_rng())
    bad = ClsagSignature(c0=sig.c0, s=list(sig.s), image=sig.image)
    bad.s[0] = (bad.s[0] + 1) % L
    assert not verify(bad, encs, msg)


def test_clsag_tampered_ring_rejected():
    encs, secrets = _ring(RING_SIZE, 4)
    msg = keccak_256(b"ringct")
    sig = sign(secrets[2], 2, encs, msg, _det_rng())
    other_encs, _ = _ring(RING_SIZE, 99)
    swapped = list(encs)
    swapped[9] = other_encs[9]
    assert not verify(sig, swapped, msg)


def test_clsag_tampered_image_rejected():
    encs, secrets = _ring(RING_SIZE, 5)
    msg = keccak_256(b"ringct")
    sig = sign(secrets[1], 1, encs, msg, _det_rng())
    other = sign(secrets[4], 4, encs, msg, _det_rng())
    assert not verify(ClsagSignature(c0=sig.c0, s=sig.s, image=other.image),
                      encs, msg)


def test_clsag_linkability_same_output_two_rings():
    """One spent output => identical key image even in different rings."""
    x = scalar_reduce(keccak_256(b"shared-output")) or 1
    P_enc = encode(public_key(x))
    encs_a, _ = _ring(RING_SIZE, 6)
    encs_b, _ = _ring(RING_SIZE, 7)
    ra = [P_enc] + encs_a
    rb = [P_enc] + encs_b
    sa = sign(x, 0, ra, keccak_256(b"tx-A"), _det_rng(1))
    sb = sign(x, 0, rb, keccak_256(b"tx-B"), _det_rng(2))
    assert sa.image == sb.image
    assert verify(sa, ra, keccak_256(b"tx-A"))
    assert verify(sb, rb, keccak_256(b"tx-B"))


def test_clsag_wire_serialize_roundtrip():
    encs, secrets = _ring(RING_SIZE, 8)
    msg = keccak_256(b"wire")
    sig = sign(secrets[11], 11, encs, msg, _det_rng())
    blob = sig.serialize()
    assert len(blob) == 32 * (1 + RING_SIZE)
    back = ClsagSignature.deserialize(blob, RING_SIZE, sig.image)
    assert back.c0 == sig.c0 and back.s == sig.s and back.image == sig.image
    assert verify(back, encs, msg)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
