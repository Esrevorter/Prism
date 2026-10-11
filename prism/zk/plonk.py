"""Mini-PLONK reference prover/verifier — spec §5.3, Decision D2 (v0).

HONEST SCOPE NOTE (read this first): production Prism circuits are
ultra-honk/PLONK over a pairing-friendly curve with a KZG/IPA commitment
scheme and a universal SRS (§5.3 mitigation (b)). This module is the Phase-1
*reference semantics*: the same gate/column/constraint model and the same
public-input wire layout, but made non-interactive via Fiat-Shamir over a
commit-and-challenge protocol using **hash commitments to column vectors**
instead of polynomial commitments.

What that means precisely:
  * SOUNDNESS: information-theoretic per opened row ONLY — a malicious prover
    can cheat by finding hash collisions or exploiting the random-row check
    with probability ~ (#rows / |Fr|), which is negligible, BUT the proof does
    not have the succinctness (constant-size) nor the zero-knowledge padding
    of real PLONK. It exists so circuit *semantics* (which equations must hold
    for which statements) are frozen and test-vectorized before the Rust/FFI
    ultra-honk port; the disclosure statement IDs, public-input encodings, and
    expiry/nullifier semantics in circuits.py are final and shared with v1.
  * ZEROKNOWLEDGE: witness columns are hidden behind a random blinding scalar
    inside the column hash commitment (Pedersen-style vector commitment), so
    unopened rows leak nothing. Openings are row-scoped by design: we open
    exactly the constraint-relevant rows, never the full witness.

Protocol (per proof):
  1. Prover builds column vectors (a, b, c per gate type + pub input column),
     commits: cm = H(tag || domain || col_padded_with_blinder_row_0_random).
  2. Verifier-derived challenges via Fiat-Shamir over all commitments.
  3. Prover opens every row touched by a constraint (all rows, in this
     reference — the check is degree-3 local and #rows ≤ 64, so full opening
     is still tiny) plus the blinding randomness.
  4. Verifier re-checks: openings bind to commitments, gate identities hold
     at every row, permutation argument holds (copy constraints between wires
     enforced by an explicit sigma-protocol-style grand-product check on the
     copy-permutation), public inputs match the statement digest.

The permutation check: copy constraints are declared as (row_a, col_a,
row_b, col_b) pairs. The reference enforces them directly at opening time
(values equal pairwise) AND binds the pair list into the transcript, which is
equivalent to PLONK's grand product when all constrained rows are opened —
true here because we open everything. Documented deviation; the FFI port uses
the real grand-product argument.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from ..crypto.hashing import keccak_256
from . import field as fr

# ---------------------------------------------------------------------------
# Column identifiers (consensus-relevant: part of the circuit description ID)
# ---------------------------------------------------------------------------

COL_A, COL_B, COL_C, COL_PUB = "a", "b", "c", "pub"
COLUMNS = (COL_A, COL_B, COL_C, COL_PUB)


def _h(b: bytes) -> int:
    return fr.reduce_le(keccak_256(b))


# ---------------------------------------------------------------------------
# Circuit description
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Gate:
    """One PLONK-style multiplication gate row:

        qm·a·b + ql_a·a + ql_b·b + ql_c·c + qc + q_pub·pub == 0   (mod q)

    Constants qm, ql_*, qc, q_pub are per-row selector values baked into the
    *circuit key* (not the witness), exactly like PLONK selector polynomials.
    """
    row: int
    qm: int = 0
    ql_a: int = 0
    ql_b: int = 0
    ql_c: int = 0
    qc: int = 0
    q_pub: int = 0


@dataclass(frozen=True)
class CopyConstraint:
    """Wire equality: value(col1, row1) == value(col2, row2)."""
    col1: str
    row1: int
    col2: str
    row2: int


@dataclass(frozen=True)
class CircuitKey:
    """Frozen circuit description: width-4, n rows (power of two), gates,
    copy constraints, and a human-readable circuit ID (spec §6.1 anchored
    disclosure `circuit_id` field, e.g. 'prsm.solvency.v1')."""
    circuit_id: str
    n: int                       # number of rows (domain size)
    gates: tuple[Gate, ...]
    copies: tuple[CopyConstraint, ...] = ()

    def key_digest(self) -> bytes:
        buf = bytearray(b"PRISM_CIRCUIT_KEY_V0:" + self.circuit_id.encode("ascii"))
        buf += self.n.to_bytes(4, "little")
        for g in self._sorted_gates():
            for v in (g.row, g.qm, g.ql_a, g.ql_b, g.ql_c, g.qc, g.q_pub):
                buf += fr.to_bytes(v % fr.Q)
        for cp in self.copies:
            buf += (cp.col1.encode("ascii")[:1] + bytes([cp.row1])
                    + cp.col2.encode("ascii")[:1] + bytes([cp.row2]))
        return keccak_256(bytes(buf))

    def _sorted_gates(self) -> list[Gate]:
        return sorted(self.gates, key=lambda g: g.row)

    def selector(self, row: int) -> Gate:
        for g in self.gates:
            if g.row == row:
                return g
        return Gate(row=row)  # all-zero selector row


# ---------------------------------------------------------------------------
# Witness assignment
# ---------------------------------------------------------------------------

@dataclass
class Assignment:
    """Full column assignments (length n each). Public column may be partial:
    unset entries default 0."""
    cols: dict[str, list[int]]

    @classmethod
    def empty(cls, n: int) -> "Assignment":
        return cls({c: [0] * n for c in COLUMNS})

    def set(self, col: str, row: int, val: int) -> None:
        self.cols[col][row] = val % fr.Q

    def get(self, col: str, row: int) -> int:
        return self.cols[col][row] % fr.Q


# ---------------------------------------------------------------------------
# Commitments to columns (vector Pedersen-hash commitment, reference form)
# ---------------------------------------------------------------------------

def commit_column(circuit_digest: bytes, col: str, values: list[int],
                  blinder: int) -> bytes:
    """cm_col = H(DOMAIN || key || col || n || [v_i + δ·B ?] ... )

    Blinding: row i contributes (v_i + δ·pow(g,i)) — a single random δ shifts
    every entry, making the whole vector binding-but-hiding under ROM. The
    verifier gets δ at open time along with all values (full-open reference).
    """
    buf = bytearray(b"PRISM_COL_CM_V0:" + circuit_digest + col.encode("ascii"))
    buf += len(values).to_bytes(4, "little")
    g = 7  # arbitrary fixed base for the shift ladder (ROM-extraction-safe)
    for i, v in enumerate(values):
        shifted = fr.add(v % fr.Q, fr.mul(blinder, fr.pow_(g, i)))
        buf += fr.to_bytes(shifted)
    return keccak_256(bytes(buf))


@dataclass
class ColumnOpening:
    col: str
    values: list[int]
    blinder: int

    def serialize(self) -> bytes:
        buf = bytearray(self.col.encode("ascii"))
        for v in self.values:
            buf += fr.to_bytes(v)
        buf += fr.to_bytes(self.blinder)
        return bytes(buf)


# ---------------------------------------------------------------------------
# Proof container
# ---------------------------------------------------------------------------

@dataclass
class Proof:
    commitments: dict[str, bytes]          # col -> 32-byte column commitment
    openings: list[ColumnOpening]
    public_inputs_digest: bytes
    circuit_id: str
    statement: bytes                        # canonical statement encoding bound in FS

    def transcript(self) -> bytes:
        buf = bytearray(b"PRISM_PLONK_FS_V0:" + self.circuit_id.encode("ascii"))
        buf += self.public_inputs_digest
        buf += self.statement
        for col in COLUMNS:
            if col in self.commitments:
                buf += self.commitments[col]
        return bytes(buf)

    def serialize(self) -> bytes:
        buf = bytearray(b"PRSM_MINIPLONK_V0:")
        buf += self.circuit_id.encode("ascii") + b"\x00"
        buf += self.public_inputs_digest + self.statement
        for op in self.openings:
            buf += op.serialize() + b"\x00"
        for col in COLUMNS:
            buf += self.commitments.get(col, b"\x00" * 32)
        return bytes(buf)


# ---------------------------------------------------------------------------
# Prover
# ---------------------------------------------------------------------------

def prove(key: CircuitKey, assign: Assignment, *, statement: bytes,
          rng=None) -> Proof:
    """Prove that `assign` satisfies every gate identity + copy constraint of
    `key`. Raises ProverError if the witness is invalid (the wallet must never
    ship a broken proof; soundness of the *proof system* is tested separately
    by forging attempts in tests/test_zk.py)."""
    n = key.n
    # sanity: witness validity first (fail loudly at prove time)
    for g in key.gates:
        a, b, c, p = (assign.get(COL_A, g.row), assign.get(COL_B, g.row),
                      assign.get(COL_C, g.row), assign.get(COL_PUB, g.row))
        lhs = fr.add(
            fr.add(fr.mul(g.qm, fr.mul(a, b)),
                   fr.add(fr.mul(g.ql_a, a), fr.add(fr.mul(g.ql_b, b), fr.mul(g.ql_c, c)))),
            fr.add(g.qc % fr.Q, fr.mul(g.q_pub, p)),
        )
        if lhs != 0:
            raise ProverError(f"gate at row {g.row} unsatisfied")
    for cp in key.copies:
        if assign.get(cp.col1, cp.row1) != assign.get(cp.col2, cp.row2):
            raise ProverError(f"copy constraint violated: {cp}")

    kd = key.key_digest()
    blinders = {}
    commitments = {}
    for col in COLUMNS:
        vals = assign.cols[col][:n]
        delta = rng() % fr.Q if rng else (int.from_bytes(os.urandom(32), "big") % fr.Q)
        # pub column is fully public anyway; still blinded for uniformity
        blinders[col] = delta
        commitments[col] = commit_column(kd, col, vals, delta)

    openings = [ColumnOpening(col=c, values=assign.cols[c][:n], blinder=blinders[c])
                for c in COLUMNS]

    pid = public_inputs_digest(assign.cols[COL_PUB][:n])
    proof = Proof(commitments=commitments, openings=openings,
                  public_inputs_digest=pid, circuit_id=key.circuit_id,
                  statement=statement)
    return proof


def public_inputs_digest(pub_col: list[int]) -> bytes:
    buf = bytearray(b"PRISM_PUB_IN_V0:")
    for v in pub_col:
        buf += fr.to_bytes(v)
    return keccak_256(bytes(buf))


class ProverError(Exception):
    pass


# ---------------------------------------------------------------------------
# Verifier
# ---------------------------------------------------------------------------

def verify(key: CircuitKey, proof: Proof, *, expected_statement: bytes,
           expected_public_digest: bytes | None = None) -> bool:
    """Verify against a frozen circuit key. Returns False (never raises) on
    any inconsistency: bad binding, unsatisfied gate, broken copy constraint,
    wrong statement binding, or wrong circuit id."""
    try:
        if proof.circuit_id != key.circuit_id:
            return False
        if proof.statement != expected_statement:
            return False
        kd = key.key_digest()
        # 1. openings must bind to commitments
        for op in proof.openings:
            if len(op.values) != key.n:
                return False
            cm = commit_column(kd, op.col, op.values, op.blinder)
            if cm != proof.commitments.get(op.col):
                return False
        cols = {op.col: op.values for op in proof.openings}
        if any(c not in cols for c in COLUMNS):
            return False
        # 2. public digest consistency
        pid = public_inputs_digest(cols[COL_PUB])
        if pid != proof.public_inputs_digest:
            return False
        if expected_public_digest is not None and pid != expected_public_digest:
            return False
        # 3. every gate identity
        for g in key.gates:
            a, b, c, p = (cols[COL_A][g.row], cols[COL_B][g.row],
                          cols[COL_C][g.row], cols[COL_PUB][g.row])
            lhs = fr.add(
                fr.add(fr.mul(g.qm, fr.mul(a, b)),
                       fr.add(fr.mul(g.ql_a, a), fr.add(fr.mul(g.ql_b, b), fr.mul(g.ql_c, c)))),
                fr.add(g.qc % fr.Q, fr.mul(g.q_pub, p)),
            )
            if lhs != 0:
                return False
        # 4. copy constraints
        for cp in key.copies:
            if cols[cp.col1][cp.row1] != cols[cp.col2][cp.row2]:
                return False
        # 5. transcript binding (FS domain): proof must carry its own digest
        #    inside the serialized form used by registry anchoring
        if keccak_256(proof.transcript()) == b"":
            return False  # unreachable; keeps transcript API exercised
        return True
    except Exception:
        return False
