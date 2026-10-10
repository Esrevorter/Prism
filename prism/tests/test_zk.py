"""Disclosure-circuit tests — spec §5.3 (five statements) + §5.4 lifecycle.

Covers the pinning-rule migration explicitly: verify_disclosure's public-input
check changed from "every non-listed pub row must be zero" to "every NONZERO
opened pub cell must equal the statement-derived intent (+ caller pins)".
These tests freeze BOTH directions of that rule and the circuit shapes it
depends on (gate counts, pub-row layouts). Run: python3 -m pytest prism/tests
(from the repo root; test_chain-style sys.path bootstrap keeps both roots OK).
"""
from __future__ import annotations

import copy
import time
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root: `prism` pkg

import pytest

from prism.zk import circuits as C
from prism.zk import field as fr
from prism.zk.plonk import (COL_A, COL_B, COL_C, COL_PUB, Assignment,
                            ColumnOpening, Proof, prove, public_inputs_digest,
                            verify)

NOW = 1_893_456_000   # fixed clock: Fri 2029-12-31 (deterministic; expiry tests use NOW+3600)
NONCE = bytes.fromhex("00112233445566778899aabbccddeeff")


def _now():
    return NOW


# ---------------------------------------------------------------------------
# Happy paths: prove + verify_disclosure for all five statements
# ---------------------------------------------------------------------------

def test_provenance_happy_path():
    claim = C.ProvenanceClaim(value=1234, mask=99, tag=7,
                              denylist_tags=[1, 2, 3, 5, 11],
                              expiry_unix=_now() + 3600, verifier_nonce=NONCE)
    key, _a, proof, stmt = C.prove_provenance(claim)
    assert verify(key, proof, expected_statement=stmt)
    assert C.verify_disclosure(key, proof, stmt=stmt, now_unix=_now())
    dec = C.decode_statement(stmt)
    assert dec["statement_type"] == C.STMT_PROVENANCE
    assert dec["public_inputs"]["denylist_count"] == 5


def test_solvency_happy_path():
    claim = C.SolvencyClaim(values=[10**6, 2 * 10**6, 3_456],
                            min_amount=2_500_000,
                            expiry_unix=_now() + 3600, verifier_nonce=NONCE)
    key, proof, stmt = C.prove_solvency(claim)
    assert verify(key, proof, expected_statement=stmt)
    assert C.verify_disclosure(key, proof, stmt=stmt, now_unix=_now())


def test_income_happy_path():
    vals = [11, 22, 33, 44]
    claim = C.IncomeClaim(values=vals, total=sum(vals),
                          expiry_unix=_now() + 3600, verifier_nonce=NONCE,
                          consented_counterparties=[b"alex", b"client"])
    key, proof, stmt = C.prove_income(claim)
    assert verify(key, proof, expected_statement=stmt)
    assert C.verify_disclosure(key, proof, stmt=stmt, now_unix=_now())


def test_reserve_happy_path():
    claim = C.ReserveClaim(values=[5_000_000, 1], min_amount=4_999_999,
                           expiry_unix=_now() + 3600, verifier_nonce=NONCE)
    key, proof, stmt = C.prove_reserve(claim)
    assert verify(key, proof, expected_statement=stmt)
    assert C.verify_disclosure(key, proof, stmt=stmt, now_unix=_now())
    # reserve is a distinct circuit id from solvency (keys are per-statement)
    assert key.circuit_id == C.STMT_RESERVE


def test_clean_exit_happy_path():
    claim = C.CleanExitClaim(values=[7_000, 3_000], exit_total=9_900, fee=100,
                             expiry_unix=_now() + 3600, verifier_nonce=NONCE)
    key, proof, stmt = C.prove_clean_exit(claim)
    assert verify(key, proof, expected_statement=stmt)
    assert C.verify_disclosure(key, proof, stmt=stmt, now_unix=_now())


# ---------------------------------------------------------------------------
# Frozen circuit shapes (the pinning rule depends on these exact pub rows)
# ---------------------------------------------------------------------------

def test_gate_counts_are_frozen_shapes():
    """The witness-loop alignment fixed specific gate counts; legacy tests
    asserted the OLD unsound selector shapes. Freeze the new ones here."""
    # provenance: 1 tag-binding gate + 2 gates per denylisted tag
    n_tags = 3
    key, _lay = C._provenance_layout(n_tags, 16)
    assert len(key.gates) == 1 + 2 * n_tags
    # structural pub binding: provenance binds the tag via a q_pub=-1 gate at
    # row 0 (identity a - pub = 0), the SAME convention every amount circuit
    # uses so _pub_amount_row can derive the pin row from the key alone.
    assert sum(g.q_pub % fr.Q != 0 for g in key.gates) == 1
    assert C._pub_amount_row(key) == 0

    k, bits = 2, 64
    skey = C._solvency_layout(k, bits, 256)
    # k chain gates + 1 inequality gate (q_pub=-1) + bits stage gates.
    # NOTE: the docstring's "2*bits" figure was from the OLD unsound layout
    # (separate binary-check + accumulator rows); the aligned witness loop
    # uses ONE linear doubling-recurrence gate per bit (binarity is forced
    # inductively by the recurrence identity with acc_0 pinned to 0).
    assert len(skey.gates) == k + 1 + bits
    pub_gates = [g for g in skey.gates if g.q_pub % fr.Q != 0]
    # TWO structural q_pub bindings: the inequality gate (acc−excess−pub=0)
    # and the range-check init stage (bit−2·0−acc_1−pub[row]=0 pinning
    # acc_0==0 to the pub cell). The amount-row discriminator must pick the
    # FIRST shape uniquely (ql_a=+1, ql_c=0).
    assert len(pub_gates) == 2
    assert pub_gates[0].ql_c % fr.Q == 0 and pub_gates[0].ql_a % fr.Q == 1
    # exactly ONE candidate public-amount row (the ambiguity guard in
    # _pub_amount_row must never trip for shipped layouts)
    assert C._pub_amount_row(skey) == 1 + k          # ex_row
    assert C._pub_amount_row(C._reserve_layout(k, bits, 256)) == 1 + k

    ikey = C._income_layout(k, 32, 256)
    # k chain gates + 1 equality gate (q_pub=-1) + k*(1 init + bits stages)
    assert len(ikey.gates) == k + 1 + k * (1 + 32)
    ipub = [g for g in ikey.gates if g.q_pub % fr.Q != 0]
    assert len(ipub) == 1                            # income binds via q_pub
    assert C._pub_amount_row(ikey) == ipub[0].row


def test_pub_amount_row_rejects_ambiguous_keys():
    """A hand-built key with two amount-shaped pub gates must raise, not
    silently pick one (this guard is what makes the pinning deterministic)."""
    from prism.zk.plonk import CircuitKey, Gate
    bad = CircuitKey(circuit_id="test.ambiguous", n=16,
                     gates=(Gate(row=3, ql_a=1, q_pub=fr.Q - 1),
                            Gate(row=9, ql_a=1, q_pub=fr.Q - 1)),
                     copies=())
    with pytest.raises(ValueError):
        C._pub_amount_row(bad)


# ---------------------------------------------------------------------------
# D3 expire-and-rotate at the verifier boundary
# ---------------------------------------------------------------------------

def test_expired_disclosure_rejected_at_verifier_boundary():
    claim = C.SolvencyClaim(values=[10, 20], min_amount=25,
                            expiry_unix=_now() + 3600, verifier_nonce=NONCE)
    key, proof, stmt = C.prove_solvency(claim)
    dec = C.decode_statement(stmt)
    assert C.verify_disclosure(key, proof, stmt=stmt,
                               now_unix=dec["expiry_unix"] - 1)
    # shipped D3 rule is strict: expiry_unix <= now_unix ⇒ dead (the proof
    # dies AT its expiry timestamp, not one second after — §5.4 registry-TTL
    # semantics). Freeze that boundary so a future off-by-one flip fails here.
    assert not C.verify_disclosure(key, proof, stmt=stmt,
                                   now_unix=dec["expiry_unix"])
    assert not C.verify_disclosure(key, proof, stmt=stmt,
                                   now_unix=dec["expiry_unix"] + 1)
    # ... but the PROOF itself remains mathematically valid forever (§5.4
    # honest-UX rule: SNARKs cannot be recalled; expiry lives at this boundary)
    assert verify(key, proof, expected_statement=stmt)


def test_malformed_statement_is_rejected():
    claim = C.SolvencyClaim(values=[10, 20], min_amount=5,
                            expiry_unix=_now() + 3600, verifier_nonce=NONCE)
    key, proof, stmt = C.prove_solvency(claim)
    assert not C.verify_disclosure(key, proof, stmt=b"gibberish",
                                   now_unix=_now())


# ---------------------------------------------------------------------------
# Honest prover refuses to lie
# ---------------------------------------------------------------------------

def test_denylisted_tag_refused():
    claim = C.ProvenanceClaim(value=1, mask=2, tag=5,
                              denylist_tags=[1, 5, 9],
                              expiry_unix=_now() + 60, verifier_nonce=NONCE)
    with pytest.raises(ValueError):
        C.prove_provenance(claim)


def test_below_threshold_refused():
    claim = C.SolvencyClaim(values=[1, 2], min_amount=10**9,
                            expiry_unix=_now() + 60, verifier_nonce=NONCE)
    with pytest.raises(ValueError):
        C.prove_solvency(claim)


def test_income_exact_sum_required():
    claim = C.IncomeClaim(values=[1, 2], total=4,
                          expiry_unix=_now() + 60, verifier_nonce=NONCE)
    with pytest.raises(ValueError):
        C.prove_income(claim)


def test_clean_exit_balance_required():
    claim = C.CleanExitClaim(values=[5], exit_total=5, fee=1,
                             expiry_unix=_now() + 60, verifier_nonce=NONCE)
    with pytest.raises(C.ProverError if hasattr(C, "ProverError") else Exception):
        C.prove_clean_exit(claim)


# ---------------------------------------------------------------------------
# Forging attempts (proof-system soundness at the opened-row level)
# ---------------------------------------------------------------------------

def _tamper_pub(proof: Proof, row: int, val: int) -> Proof:
    p2 = copy.deepcopy(proof)
    for op in p2.openings:
        if op.col == COL_PUB:
            op.values[row] = val % fr.Q
    p2.public_inputs_digest = public_inputs_digest(
        next(op.values for op in p2.openings if op.col == COL_PUB))
    return p2


def test_tampered_public_input_breaks_gate_or_binding():
    claim = C.SolvencyClaim(values=[100, 200], min_amount=150,
                            expiry_unix=_now() + 3600, verifier_nonce=NONCE)
    key, proof, stmt = C.prove_solvency(claim)
    bad = _tamper_pub(proof, C._pub_amount_row(key), 151)
    assert not verify(key, bad, expected_statement=stmt)


def test_tampered_witness_breaks_commitment_binding():
    claim = C.SolvencyClaim(values=[100, 200], min_amount=150,
                            expiry_unix=_now() + 3600, verifier_nonce=NONCE)
    key, proof, stmt = C.prove_solvency(claim)
    p2 = copy.deepcopy(proof)
    for op in p2.openings:
        if op.col == COL_A:
            op.values[1] = fr.add(op.values[1], 1)
    assert not verify(key, p2, expected_statement=stmt)


def test_wrong_statement_blob_rejected():
    claim = C.SolvencyClaim(values=[100, 200], min_amount=150,
                            expiry_unix=_now() + 3600, verifier_nonce=NONCE)
    key, proof, stmt = C.prove_solvency(claim)
    other = C.encode_statement(C.STMT_SOLVENCY, verifier_nonce=NONCE,
                               expiry_unix=_now() + 3600,
                               min_amount_shard=150, output_count=2)
    assert other == stmt  # re-encoding is canonical...
    swapped = C.encode_statement(C.STMT_RESERVE, verifier_nonce=NONCE,
                                 expiry_unix=_now() + 3600,
                                 min_amount_shard=150, output_count=2)
    assert not verify(key, proof, expected_statement=swapped)
    assert not C.verify_disclosure(key, proof, stmt=swapped, now_unix=_now())


# ---------------------------------------------------------------------------
# The NEW pinning rule: nonzero pub cells must match statement-derived intent
# ---------------------------------------------------------------------------

def test_pinning_accepts_structural_zero_rows():
    """Zero pub cells (e.g. the range-check gadget's acc_0==0 pin row) are
    accepted as-is under the new rule — the old rule would have demanded an
    explicit entry for them in expected_public_rows."""
    claim = C.SolvencyClaim(values=[100, 200], min_amount=150,
                            expiry_unix=_now() + 3600, verifier_nonce=NONCE)
    key, proof, stmt = C.prove_solvency(claim)
    pub = next(op.values for op in proof.openings if op.col == COL_PUB)
    acc0_row = C._pub_amount_row(key) + 1   # first stage row carries pub=0 pin
    assert pub[acc0_row] == 0
    assert C.verify_disclosure(key, proof, stmt=stmt, now_unix=_now(),
                               expected_public_rows={})


def test_pinning_rejects_unexpected_nonzero_pub_cell():
    """A nonzero pub cell NOT derivable from the statement must fail even
    though the underlying proof math still verifies (MITM re-binder model:
    attacker crafts a witness whose pub column has extra nonzero junk that
    happens to satisfy gates because most rows lack a q_pub selector)."""
    claim = C.SolvencyClaim(values=[100, 200], min_amount=150,
                            expiry_unix=_now() + 3600, verifier_nonce=NONCE)
    key, proof, stmt = C.prove_solvency(claim)
    # pick a row whose gate has NO q_pub term AND no copy constraint touching
    # pub: the stage rows i>=1 (gate is linear in a/b/c only). Row ex_row+2.
    junk_row = C._pub_amount_row(key) + 2
    a = Assignment.empty(key.n)
    vals = claim.values
    excess = sum(vals) - claim.min_amount
    # rebuild a SATISFYING witness, then poison one pub cell on a row whose
    # gate ignores pub (so plonk.verify still passes)
    row = 1
    acc = vals[0]
    a.set(COL_A, row, acc); a.set(COL_C, row, acc); row += 1
    for v in vals[1:]:
        a.set(COL_A, row, acc); a.set(COL_B, row, v)
        acc += v; a.set(COL_C, row, acc); row += 1
    ex_row = row
    a.set(COL_A, ex_row, acc); a.set(COL_B, ex_row, excess)
    a.set(COL_PUB, ex_row, claim.min_amount)
    row += 1
    run = 0
    for i in range(64):
        r = row + i
        bi = (excess >> (63 - i)) & 1
        a.set(COL_A, r, bi)
        if i == 0:
            a.set(COL_PUB, r, 0)  # structural zero pin (copy-bound to a-slot)
        else:
            a.set(COL_B, r, run)
        run = 2 * run + bi
        a.set(COL_C, r, run)
    # poison: the stage-row gate (ql_a=-1, ql_b=-2, ql_c=1) has q_pub == 0,
    # so this keeps ALL gate identities true while adding a NONZERO pub cell
    # nowhere in the statement-derived intent.
    a.set(COL_PUB, junk_row, 0xC0FFEE)
    forged = prove(key, a, statement=stmt)
    assert verify(key, forged, expected_statement=stmt)      # math checks...
    assert not C.verify_disclosure(key, forged, stmt=stmt,
                                   now_unix=_now())          # ...pinning does not


def test_caller_pins_are_enforced_both_directions():
    """Explicit expected_public_rows entries are checked against the opened
    column (nonzero want ⇒ must match), and nonzero cells must be wanted."""
    claim = C.ProvenanceClaim(value=1, mask=2, tag=7,
                              denylist_tags=[1, 2, 3],
                              expiry_unix=_now() + 3600, verifier_nonce=NONCE)
    key, _a, proof, stmt = C.prove_provenance(claim)
    # correct pin: the tag is q_pub-bound into pub[0] (row-0 gate a - pub = 0).
    assert C.verify_disclosure(key, proof, stmt=stmt, now_unix=_now(),
                               expected_public_rows={0: 7})
    # wrong pin ⇒ reject. NOTE: with the statement-derived intent now active
    # for provenance too (tag recovered from the blob's tag_digest), BOTH
    # directions of rejection are covered: a wrong explicit pin conflicts
    # with the derived intent, and a re-blobbed statement changes the intent.
    assert not C.verify_disclosure(key, proof, stmt=stmt, now_unix=_now(),
                                   expected_public_rows={0: 8})


# ---------------------------------------------------------------------------
# Nullifiers (D3 rotate semantics)
# ---------------------------------------------------------------------------

def test_nullifier_derivation_is_deterministic_and_ctx_bound():
    claim = C.SolvencyClaim(values=[100, 200], min_amount=150,
                            expiry_unix=_now() + 3600, verifier_nonce=NONCE)
    _key, _proof, stmt = C.prove_solvency(claim)
    nu1 = C.nullifier(C.STMT_SOLVENCY, stmt, b"wallet-seed-A")
    nu2 = C.nullifier(C.STMT_SOLVENCY, stmt, b"wallet-seed-A")
    nu3 = C.nullifier(C.STMT_SOLVENCY, stmt, b"wallet-seed-B")
    assert nu1 == nu2 and nu1 != nu3 and len(nu1) == 32
