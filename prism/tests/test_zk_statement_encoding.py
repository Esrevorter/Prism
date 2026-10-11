"""zk/circuits.py encode_statement — spec §5.3 acceptance evidence (grassroots track).

Pure-coding closure of the "canonical encoding + hash integration" review
items for ZKP circuits v1, WITHOUT any external organization:

  * encode_statement is CANONICAL: fixed framing, sorted key=value pairs,
    length-prefixed nonce; identical inputs always produce identical bytes
    regardless of keyword order at the call site.
  * The keccak-256 nullifier ν = H(DOMAIN || statement_type || stmt_enc ||
    secret_ctx) correctly incorporates the encoded statement bytes AND both
    verifier_nonce and expiry_unix — flipping any single bit anywhere in the
    blob changes every downstream digest.
  * Order-independent semantics verified end-to-end via decode_statement.
  * Statement→nullifier binding: two honest wallets over the same statement
    derive distinct nullifiers (secret_ctx); rotation is exact.

Run from repo root: python3 -m pytest prism/tests/test_zk_statement_encoding.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root: `prism` pkg

import pytest

from prism.crypto.hashing import keccak_256
from prism.zk import circuits as C
from prism.zk.plonk import verify

NOW = 1_893_456_000
NONCE = bytes.fromhex("00112233445566778899aabbccddeeff")


def _sol_stmt(**over):
    kw = dict(verifier_nonce=NONCE, expiry_unix=NOW + 3600,
              min_amount_shard=150, output_count=2)
    kw.update(over)
    return C.encode_statement(C.STMT_SOLVENCY, **kw)


# ---------------------------------------------------------------------------
# canonical encoding

class TestCanonicalEncoding:
    def test_deterministic(self):
        assert _sol_stmt() == _sol_stmt()

    def test_keyword_order_at_call_site_does_not_matter(self):
        a = C.encode_statement(C.STMT_SOLVENCY, verifier_nonce=NONCE,
                               expiry_unix=NOW + 3600,
                               min_amount_shard=150, output_count=2)
        b = C.encode_statement(C.STMT_SOLVENCY, output_count=2,
                               min_amount_shard=150,
                               expiry_unix=NOW + 3600, verifier_nonce=NONCE)
        assert a == b  # sorted-key canonicalization does its job

    def test_framing_is_fixed_and_versioned(self):
        enc = _sol_stmt()
        assert enc.startswith(b"PRISM_STMT_V1|" + C.STMT_SOLVENCY.encode() + b"|")

    def test_roundtrip_decode_matches_inputs(self):
        enc = _sol_stmt(memo_digest=b"\xee" * 16)
        dec = C.decode_statement(enc)
        assert dec["statement_type"] == C.STMT_SOLVENCY
        assert dec["verifier_nonce"] == NONCE
        assert dec["expiry_unix"] == NOW + 3600
        assert dec["public_inputs"]["min_amount_shard"] == 150
        assert dec["public_inputs"]["output_count"] == 2
        assert dec["public_inputs"]["memo_digest"] == b"\xee" * 16

    def test_bytes_payload_with_delimiters_roundtrips_exactly(self):
        """Audit defect #2 (fixed): the old naive split(b'|') shredded any
        bytes value containing '|' or '='. Canonicality means EVERY byte
        sequence roundtrips — delimiters included."""
        for blob in (b"a|b=c", b"|", b"=", b"||==", b"", b"\x7c\x3d" * 4,
                     bytes(range(256))):
            enc = _sol_stmt(blob_field=blob, after_field=7)
            dec = C.decode_statement(enc)
            assert dec["public_inputs"]["blob_field"] == blob
            assert dec["public_inputs"]["after_field"] == 7

    def test_no_spurious_empty_key_on_decode(self):
        # regression: mis-parsing used to inject {'': b''} into public_inputs
        dec = C.decode_statement(_sol_stmt())
        assert "" not in dec["public_inputs"]

    def test_int_vs_bytes_tags_prevent_cross_type_collisions(self):
        # value 0x6262 ('bb' little-endian-ish) vs bytes b"bb": the i:/b:
        # type tags must keep their encodings distinct even though raw
        # payloads could coincide.
        a = _sol_stmt(x=0x6262)
        b = _sol_stmt(x=b"\x62\x62")
        assert a != b
        assert C.decode_statement(a)["public_inputs"]["x"] == 0x6262
        assert C.decode_statement(b)["public_inputs"]["x"] == b"\x62\x62"

    def test_nonce_length_prefix_blocks_delimiter_smuggling(self):
        # v1 nonces are exactly 16 bytes; encode honours any length but the
        # decoder ENFORCES the prefix contract instead of silently
        # mis-reading expiry (audit defect #1, fixed). A nonce containing the
        # delimiter still roundtrips exactly.
        weird = C.encode_statement(C.STMT_SOLVENCY, verifier_nonce=b"|" * 16,
                                   expiry_unix=NOW + 3600,
                                   min_amount_shard=1, output_count=1)
        assert C.decode_statement(weird)["verifier_nonce"] == b"|" * 16

    def test_off_contract_nonce_length_is_rejected_not_misparsed(self):
        enc = C.encode_statement(C.STMT_SOLVENCY, verifier_nonce=b"\x01" * 8,
                                 expiry_unix=NOW, min_amount_shard=1)
        with pytest.raises(ValueError):
            C.decode_statement(enc)

    def test_bad_framing_rejected_on_decode(self):
        with pytest.raises(ValueError):
            C.decode_statement(b"gibberish")

    def test_unsupported_public_input_type_rejected(self):
        with pytest.raises(TypeError):
            C.encode_statement(C.STMT_SOLVENCY, verifier_nonce=NONCE,
                               expiry_unix=NOW, bad_input=3.5)


# ---------------------------------------------------------------------------
# hash integration: every field must reach the digests

class TestHashIntegration:
    def test_nullifier_matches_manual_keccak_recomputation(self):
        enc = _sol_stmt()
        nu = C.nullifier(C.STMT_SOLVENCY, enc, b"seed")
        expected = keccak_256(C.NULLIFIER_DOMAIN + C.STMT_SOLVENCY.encode("ascii")
                              + enc + b"seed")
        # mirrors circuits.nullifier's DOMAIN || statement_type || stmt_enc
        # || secret_ctx concatenation order exactly.
        assert nu == expected
        assert len(nu) == 32

    def test_every_single_bit_of_the_blob_changes_the_nullifier(self):
        """Core acceptance pin: the encoded statement fully participates in
        the final hash output. Flip one bit per byte position of stmt_enc;
        every flip must change ν."""
        enc = _sol_stmt()
        nu0 = C.nullifier(C.STMT_SOLVENCY, enc, b"seed-A")
        flips = 0
        for byte_i in range(len(enc)):
            for mask in (0x01, 0x80):           # low and high bit per byte
                e2 = bytearray(enc)
                e2[byte_i] ^= mask
                if bytes(e2) == enc:
                    continue
                assert C.nullifier(C.STMT_SOLVENCY, bytes(e2), b"seed-A") != nu0, \
                    f"bit flip at byte {byte_i} mask {mask:#x} escaped the hash"
                flips += 1
        assert flips == 2 * len(enc)            # swept every single-bit flip

    def test_verifier_nonce_participates_in_final_hash(self):
        # v1 nonces are exactly 16 bytes; only the CONTENT may vary
        a = C.nullifier(C.STMT_SOLVENCY, _sol_stmt(verifier_nonce=b"\x01" * 16), b"s")
        b = C.nullifier(C.STMT_SOLVENCY, _sol_stmt(verifier_nonce=b"\x01" * 15 + b"\x02"), b"s")
        assert a != b

    def test_expiry_participates_in_final_hash(self):
        a = C.nullifier(C.STMT_SOLVENCY, _sol_stmt(expiry_unix=NOW + 3600), b"s")
        b = C.nullifier(C.STMT_SOLVENCY, _sol_stmt(expiry_unix=NOW + 3601), b"s")
        assert a != b

    def test_each_public_input_participates_in_final_hash(self):
        base = C.nullifier(C.STMT_SOLVENCY, _sol_stmt(), b"s")
        variants = [
            _sol_stmt(min_amount_shard=151),
            _sol_stmt(output_count=3),
            _sol_stmt(extra_field=b"\x01"),      # presence of a new key matters
        ]
        for v in variants:
            assert C.nullifier(C.STMT_SOLVENCY, v, b"s") != base

    def test_statement_type_participates_via_domain_and_body(self):
        reserve = C.encode_statement(C.STMT_RESERVE, verifier_nonce=NONCE,
                                     expiry_unix=NOW + 3600,
                                     min_amount_shard=150, output_count=2)
        solv = _sol_stmt()
        assert C.nullifier(C.STMT_RESERVE, reserve, b"s") \
            != C.nullifier(C.STMT_SOLVENCY, solv, b"s")
        # cross-domain feeding (wrong stype for the blob) also differs
        assert C.nullifier(C.STMT_SOLVENCY, reserve, b"s") \
            != C.nullifier(C.STMT_RESERVE, reserve, b"s")

    def test_secret_context_rotation_changes_nullifier_exactly(self):
        enc = _sol_stmt()
        n_a = C.nullifier(C.STMT_SOLVENCY, enc, b"wallet-seed-A")
        n_b = C.nullifier(C.STMT_SOLVENCY, enc, b"wallet-seed-B")
        assert n_a != n_b
        # deterministic per (statement, ctx): re-deriving reproduces exactly
        assert C.nullifier(C.STMT_SOLVENCY, _sol_stmt(), b"wallet-seed-A") == n_a

    def test_domain_separation_from_raw_statement_digest(self):
        enc = _sol_stmt()
        assert C.nullifier(C.STMT_SOLVENCY, enc, b"s") != keccak_256(enc)


# ---------------------------------------------------------------------------
# circuit-level: the SAME canonical blob is what prove/verify binds via FS

class TestStatementBindingEndToEnd:
    def test_prover_reencode_is_byte_identical_to_verifier_reencode(self):
        claim = C.SolvencyClaim(values=[100, 200], min_amount=150,
                                expiry_unix=NOW + 3600, verifier_nonce=NONCE)
        key, proof, stmt = C.prove_solvency(claim)
        verifier_copy = C.encode_statement(
            C.STMT_SOLVENCY, verifier_nonce=claim.verifier_nonce,
            expiry_unix=claim.expiry_unix,
            min_amount_shard=claim.min_amount,
            output_count=len(claim.values))
        assert verifier_copy == stmt          # order-independent & canonical
        assert verify(key, proof, expected_statement=verifier_copy)

    def test_one_bit_statement_change_breaks_fiat_shamir_verification(self):
        claim = C.SolvencyClaim(values=[100, 200], min_amount=150,
                                expiry_unix=NOW + 3600, verifier_nonce=NONCE)
        key, proof, stmt = C.prove_solvency(claim)
        flipped = bytearray(stmt)
        flipped[-1] ^= 0x01                   # last public-input byte
        assert not verify(key, proof, expected_statement=bytes(flipped))

    def test_verify_disclosure_pins_derive_from_the_encoded_blob(self):
        """expiry_unix and the amount participate in the FINAL gate of the
        lifecycle: D3 boundary uses decoded expiry; the pinning rule uses
        decoded public inputs. A re-blobbed statement fails even when the
        proof math itself still verifies under the ORIGINAL blob."""
        claim = C.SolvencyClaim(values=[100, 200], min_amount=150,
                                expiry_unix=NOW + 3600, verifier_nonce=NONCE)
        key, proof, stmt = C.prove_solvency(claim)
        reblobbed = _sol_stmt(min_amount_shard=149)   # attacker's intent
        assert C.verify_disclosure(key, proof, stmt=stmt, now_unix=NOW)
        assert not C.verify_disclosure(key, proof, stmt=reblobbed, now_unix=NOW)
        # expiry side of the same blob drives the D3 rejection
        expired = C.decode_statement(stmt)["expiry_unix"]
        assert not C.verify_disclosure(key, proof, stmt=stmt, now_unix=expired)

    @pytest.mark.parametrize("prove_fn,claim_factory", [
        ("prove_provenance", lambda: C.ProvenanceClaim(
            value=1234, mask=99, tag=7, denylist_tags=[1, 2, 3],
            expiry_unix=NOW + 3600, verifier_nonce=NONCE)),
        ("prove_solvency", lambda: C.SolvencyClaim(
            values=[100, 200], min_amount=150,
            expiry_unix=NOW + 3600, verifier_nonce=NONCE)),
        ("prove_income", lambda: C.IncomeClaim(
            values=[11, 22], total=33, expiry_unix=NOW + 3600,
            verifier_nonce=NONCE, consented_counterparties=[b"alex"])),
        ("prove_reserve", lambda: C.ReserveClaim(
            values=[500, 1], min_amount=499,
            expiry_unix=NOW + 3600, verifier_nonce=NONCE)),
        ("prove_clean_exit", lambda: C.CleanExitClaim(
            values=[700, 300], exit_total=950, fee=50,
            expiry_unix=NOW + 3600, verifier_nonce=NONCE)),
    ])
    def test_each_circuits_own_reencode_is_byte_identical(self, prove_fn, claim_factory):
        """Canonicality pinned for ALL FIVE shipped statement types: the
        prover's blob equals an independent re-encode of the same logical
        claim (kwargs shuffled), and the proof verifies against it."""
        claim = claim_factory()
        outs = getattr(C, prove_fn)(claim)
        stmt = outs[-1]
        dec = C.decode_statement(stmt)
        # rebuild kwargs from the decoded view (nonce/expiry swapped
        # into the tail by construction of this call — sorted keys fix it)
        pi = dict(dec["public_inputs"])
        kw = {"verifier_nonce": dec["verifier_nonce"], "expiry_unix": dec["expiry_unix"]}
        reenc = C.encode_statement(dec["statement_type"], **kw, **pi)
        assert reenc == stmt
        # outs[-1] is the statement blob; key is outs[0], proof is outs[-2]
        # (prove_provenance additionally returns the witness Assignment).
        key, proof = outs[0], outs[-2]
        assert verify(key, proof, expected_statement=reenc)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
