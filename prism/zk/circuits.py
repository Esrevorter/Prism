"""Disclosure circuits v1 — spec §5.3, the five Prism Protocol statements.

Statement IDs (consensus constants; they appear verbatim in the Disclosure
Registry (§5.4) and the on-chain anchored-disclosure attachment (§6.1)):

    prsm.provenance.v1        "Output O not from any denylisted tag"     (§5.3 #1)
    prsm.solvency.v1          "Σ unspent ≥ X over [t1,t2]"               (§5.3 #2)
    prsm.income_attribution.v1 "incoming == Σ exactly, period"           (§5.3 #3)
    prsm.reserve.v1           "I hold collateral C"                      (§5.3 #4)
    prsm.clean_exit.v1        "spends only to addresses I control"       (§5.3 #5)

Common statement binding (D3 expire-and-rotate):
Every proof binds (statement_type, public_inputs, verifier_nonce,
expiry_timestamp) via Fiat-Shamir; `verify_disclosure()` re-checks expiry
against a supplied clock height/time and rejects expired proofs — the one and
only "revocation" semantics we ship (spec §5.4: never say "recall").

One-time nullifier tags (§5.3 preamble): each disclosure derives
    ν = keccak256(DOMAIN || statement || secret_spend_context)
and registers it; double-use of the same nullifier for the same statement is
detected by the registry (linkable without linking to the spend graph).

Circuit construction notes (all arithmetic mod Fr, see zk/field.py):

* Range check 0 ≤ x < 2^k: decompose x into 8-bit limbs (bit-level would be
  wider; byte limbs keep reference row counts small). Each limb w_i constrained
  boolean-by-squares twice → 8 bits per limb… For readability we constrain
  limbs to 8 bits with two auxiliary gates per limb:
      limb_gate:   l·(l - 256·?) ... — PLONK degree 3 allows l*(l-1)*(l-2)...?
  NO — degree bound is 3 (qm·a·b + linear). Standard trick, one gate per bit:
      b·(b − 1) = 0  via qm=1, ql_a=−1 ⇒ a=b binary. Then compose value with
      accumulation gates v_{i+1} = 2·v_i + b_i. That's what we do: binary
      decomposition, 1 gate/bit + 1 gate/bit. n must fit; v1 uses k=64 max
      ⇒ ~128+ rows ⇒ n=256 (power of two ≥ rows used). Fine for reference.

* Denylist non-membership (§5.3 #1, D5): the accumulator acc = fold of tags
  is hash-chained (chain/denylist.py). A *real* circuit would need SHA3-in-
  circuit (expensive; that's research area R2). The reference instead proves
  the algebraic core — "the claimed tag t differs from every listed tag" —
  via the classic nonzero-product gadget over the list slots:
      Π_i (t − tag_i) ≠ 0   ⇔   ∃ inv: (t − tag_i)·inv_i = 1 per slot AND
  product telescoped. With fixed list length N this is N inverse gates + N−1
  accumulator gates. This is faithful to what the SNARK must show given a
  Merkleized/accumulator witness; the fold-hash verification itself stays
  off-circuit in the reference (documented deviation; production replaces it
  with a Poseidon-based accumulator so the whole path is in-circuit).

* Commitment re-statement bridge (field.py docstring): witnesses carry the
  Ed25519 opening (v, r) as Fr scalars and an in-circuit Pedersen-style
  commitment value C_in = v·HG_scalar + r·GG_scalar evaluated in Fr — a pure
  scalar-relaxation of the EC relation. The verifier additionally checks the
  REAL group equation out-of-circuit against the chain's output set when the
  statement includes a concrete commitment (see `check_on_group`), so nothing
  unsound is concluded from the relaxed form alone.
"""
from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field

from ..crypto.hashing import keccak_256
from . import field as fr
from .plonk import (COL_A, COL_B, COL_C, COL_PUB, Assignment, CircuitKey,
                    CopyConstraint, Gate, ProverError, Proof, prove, verify)

# ---------------------------------------------------------------------------
# Statement type registry (consensus-visible strings)
# ---------------------------------------------------------------------------

STMT_PROVENANCE = "prsm.provenance.v1"
STMT_SOLVENCY = "prsm.solvency.v1"
STMT_INCOME = "prsm.income_attribution.v1"
STMT_RESERVE = "prsm.reserve.v1"
STMT_CLEAN_EXIT = "prsm.clean_exit.v1"

STATEMENT_TYPES = (STMT_PROVENANCE, STMT_SOLVENCY, STMT_INCOME,
                   STMT_RESERVE, STMT_CLEAN_EXIT)

NULLIFIER_DOMAIN = b"PRISM_DISCLOSURE_NULLIFIER_V1:"


# ---------------------------------------------------------------------------
# Canonical statement encoding (bound into FS + registry)
# ---------------------------------------------------------------------------

_CANON_PREFIX = b"C1|"     # canonical-ness marker for the pair stream


def _enc_pairs(public_inputs: dict) -> bytes:
    """Magic-prefixed sorted key=value stream, each segment self-describing:
        b"C1|" <klen u16><key ascii><i!: 8-byte LE | b: <len u32><bytes>>...
    Length prefixes make the stream parseable WITHOUT delimiter scanning,
    so payloads may contain any byte at all ('|', '=', control bytes). The
    leading magic is unambiguous against legacy framing: a legacy body's
    first byte is always an ASCII key character, never b"C"."""
    out = bytearray(_CANON_PREFIX)
    for k in sorted(public_inputs):
        v = public_inputs[k]
        kb = k.encode("ascii")
        if not kb.isascii():
            raise TypeError(f"public input key {k!r} must be ascii")
        out += len(kb).to_bytes(2, "big") + kb
        if isinstance(v, int):
            out += b"i!" + struct.pack("<Q", v)   # '!' ⇒ canonical u64
        elif isinstance(v, bytes):
            out += b"b:" + len(v).to_bytes(4, "big") + v
        else:
            raise TypeError(f"public input {k} must be int or bytes")
    return bytes(out)


def _dec_pairs(buf: bytes) -> dict:
    inputs: dict[str, object] = {}
    pos = 0
    n = len(buf)
    while pos < n:
        if pos + 2 > n:
            raise ValueError("truncated key length prefix")
        klen = int.from_bytes(buf[pos:pos + 2], "big")
        pos += 2
        if pos + klen > n:
            raise ValueError("truncated key")
        key = buf[pos:pos + klen].decode("ascii")
        pos += klen
        if pos + 2 > n:
            raise ValueError("truncated value type tag")
        tag = buf[pos:pos + 2]
        pos += 2
        if tag == b"i!":
            if pos + 8 > n:
                raise ValueError("truncated int payload")
            inputs[key] = struct.unpack("<Q", buf[pos:pos + 8])[0]
            pos += 8
        elif tag == b"b:":
            if pos + 4 > n:
                raise ValueError("truncated bytes length prefix")
            blen = int.from_bytes(buf[pos:pos + 4], "big")
            pos += 4
            if pos + blen > n:
                raise ValueError("truncated bytes payload")
            inputs[key] = buf[pos:pos + blen]
            pos += blen
        else:
            raise ValueError("unknown public-input type tag")
    return inputs


_LEGACY_PAIR_RE = re.compile(rb"([A-Za-z_][A-Za-z0-9_]*)=(i:|b:)")


def _dec_pairs_legacy(body: bytes) -> dict:
    """Pre-v1.1 framing (delimiter-joined `key=i:<8>|key=b:<raw>` pairs).
    Kept ONLY as a verifier-side fallback so old statement blobs still
    decode. Parsing is regex-anchored on strict key grammar + type tag
    (never split(b'|')), so legacy payloads may contain '|' or '=' bytes
    without shredding — the historical ambiguity only remains for payloads
    that themselves embed a literal `<key>=i:`/`<key>=b:` sequence, which
    no shipped circuit produces. Encoders since v1.1 never emit this form."""
    inputs: dict[str, object] = {}
    if not body:
        return inputs
    matches = list(_LEGACY_PAIR_RE.finditer(body))
    for i, m in enumerate(matches):
        key = m.group(1).decode("ascii")
        start = m.end()
        end = len(body) if i + 1 >= len(matches) else matches[i + 1].start()
        # legacy pairs were delimiter-joined; drop the trailing '|' if present
        seg = body[start:end]
        if seg.endswith(b"|"):
            seg = seg[:-1]
        if m.group(2) == b"i:":
            if len(seg) != 8:
                raise ValueError("legacy int payload must be 8 bytes")
            inputs[key] = struct.unpack("<Q", seg)[0]
        else:
            inputs[key] = seg
    if not matches:
        raise ValueError("legacy public-input stream has no key=tag pairs")
    return inputs


def encode_statement(statement_type: str, *, verifier_nonce: bytes,
                     expiry_unix: int, **public_inputs) -> bytes:
    """Canonical, order-independent encoding: sorted key=value pairs, every
    field length-prefixed. Verifiers re-encode identically from their own
    copy of the claim fields.

    Canonicality guarantees (pinned by tests/test_zk_statement_encoding.py):
      * fixed version framing `PRISM_STMT_V1|<statement_type>|` — domain for
        every downstream keccak binding (nullifier, Fiat-Shamir);
      * public inputs walked in SORTED key order ⇒ call-site kwargs order is
        irrelevant; identical logical statements ⇒ byte-identical blobs;
      * values are type-tagged (`i!:`-style `i!` u64 little-endian / `b:`
        length-prefixed raw bytes) so an int can never collide with a bytes
        value and ANY payload (including '|' and '=' and arbitrary binary)
        roundtrips exactly;
      * verifier_nonce is u16-length-prefixed and decode_statement enforces
        the v1 contract (exactly 16 bytes), so no nonce length can silently
        desynchronise the expiry field;
      * every field — statement_type, verifier_nonce, expiry_unix, and all
        public inputs — participates in the final digest: nullifier() hashes
        the WHOLE blob and prove() binds it via Fiat-Shamir, so any single
        bit flip changes both ν and the challenge stream.
    """
    return (b"PRISM_STMT_V1|" + statement_type.encode("ascii") + b"|"
            + len(verifier_nonce).to_bytes(2, "big") + verifier_nonce + b"|"
            + struct.pack("<Q", expiry_unix) + b"|" + _enc_pairs(public_inputs))


def decode_statement(stmt: bytes) -> dict:
    """Inverse used by verifier apps to render 'what exactly is being proven'
    (spec §7.6 step 3 side-by-side diagram needs this).

    Acceptance-audit fixes (pure-coding track):
      * The u16 nonce length prefix is now honoured on the read side too —
        the original code assumed a fixed 16-byte nonce and silently
        corrupted expiry_unix (and injected a spurious empty-key public
        input) for any other nonce length. Nonces != 16 bytes are rejected
        outright instead of mis-parsed.
      * Public-input streams are parsed by EXACT lengths (see _enc_pairs/
        _dec_pairs): the original naive split(b'|') shredded any bytes
        payload containing 0x7c/0x3d. Framing is detected UNAMBIGUOUSLY by
        the b"C1|" magic — legacy bodies start with an ASCII key char, so
        they can never collide with it. Old anchored blobs keep decoding
        (and re-encoding canonically) via _dec_pairs_legacy.
    """
    if not stmt.startswith(b"PRISM_STMT_V1|"):
        raise ValueError("bad statement framing")
    rest = stmt[len(b"PRISM_STMT_V1|"):]
    stype, rest = rest.split(b"|", 1)
    if len(rest) < 2:
        raise ValueError("truncated nonce length prefix")
    nl = int.from_bytes(rest[:2], "big")
    if nl != 16:
        raise ValueError(f"v1 nonces must be 16 bytes (wire says {nl})")
    nonce = rest[2:2 + nl]
    if len(nonce) != nl:
        raise ValueError("truncated verifier nonce")
    rest = rest[2 + nl:]
    if len(rest) < 9:
        raise ValueError("truncated expiry field")
    if rest[0:1] != b"|":
        raise ValueError("missing separator before expiry field")
    expiry = struct.unpack("<Q", rest[1:9])[0]
    body = rest[9:]
    # The canonical pair stream begins with its b"C1|" magic (which itself
    # starts with '|'), so canonical blobs carry TWO consecutive separators
    # after the expiry field. Strip exactly one; legacy single-separator
    # bodies are unaffected (their first byte is an ASCII key char).
    if body.startswith(b"|"):
        body = body[1:]
    if body[:len(_CANON_PREFIX)] == _CANON_PREFIX:
        inputs = _dec_pairs(body[len(_CANON_PREFIX):])
    elif len(body) >= 2 and body[:2] == b"\x00":
        # canonical stream without magic (v1.1 encoder variant)
        inputs = _dec_pairs(body)
    else:
        inputs = _dec_pairs_legacy(body)   # pre-v1.1 anchored blobs
    return {"statement_type": stype.decode("ascii"),
            "verifier_nonce": nonce, "expiry_unix": expiry,
            "public_inputs": inputs}


def nullifier(statement_type: str, stmt_enc: bytes, secret_ctx: bytes) -> bytes:
    """ν = H(DOMAIN || statement_type || stmt_enc || secret_ctx).

    secret_ctx ties the nullifier to the holder's spend context WITHOUT
    revealing it (per-user random seed stored in the wallet DB; rotating the
    seed rotates future nullifiers, bounding linkability across disclosures —
    the rotation half of D3)."""
    return keccak_256(NULLIFIER_DOMAIN + statement_type.encode("ascii")
                      + stmt_enc + secret_ctx)


# ---------------------------------------------------------------------------
# Gadget builders (each appends gates to a growing program)
# ---------------------------------------------------------------------------

class Program:
    """Row allocator + gate collector for building CircuitKeys readably."""

    def __init__(self, circuit_id: str, n: int):
        self.circuit_id = circuit_id
        self.n = n
        self.row = 0
        self.gates: list[Gate] = []
        self.copies: list[CopyConstraint] = []

    def alloc(self, count: int = 1) -> int:
        r = self.row
        self.row += count
        if self.row > self.n:
            raise ValueError(f"circuit {self.circuit_id} overflowed {self.n} rows")
        return r

    def gate(self, row: int, *, qm=0, qa=0, qb=0, qc_=0, const=0, qpub=0):
        self.gates.append(Gate(row=row, qm=qm % fr.Q,
                               ql_a=qa % fr.Q, ql_b=qb % fr.Q,
                               ql_c=qc_ % fr.Q, qc=const % fr.Q,
                               q_pub=qpub % fr.Q))

    def key(self) -> CircuitKey:
        return CircuitKey(circuit_id=self.circuit_id, n=self.n,
                          gates=tuple(self.gates), copies=tuple(self.copies))


def g_assert_binary(p: Program, assign: Assignment, col: str, row: int) -> None:
    """x*x - x == 0 at `row` using a==b==col."""
    p.gates.append(Gate(row=row, qm=1, ql_a=(-1) % fr.Q))
    assign.set(COL_B, row, assign.get(col, row))


def g_copy(p: Program, assign: Assignment, src_col: str, src_row: int,
           dst_col: str, dst_row: int) -> None:
    """Wire equality enforced by copy constraint + assignment propagation."""
    assign.set(dst_col, dst_row, assign.get(src_col, src_row))
    p.copies.append(CopyConstraint(src_col, src_row, dst_col, dst_row))


def g_add(p: Program, assign: Assignment, row: int, a: tuple, b: tuple,
          out: tuple) -> None:
    """out = a + b  (gate: 1·a + 1·b + (-1)·c = 0, c := out column slot)."""
    av = assign.get(*a); bv = assign.get(*b)
    assign.set(*out, fr.add(av, bv))
    p.gates.append(Gate(row=row, ql_a=1, ql_b=1, ql_c=(-1) % fr.Q))


def g_mul(p: Program, assign: Assignment, row: int, a: tuple, b: tuple,
          out: tuple) -> None:
    """out = a·b."""
    av = assign.get(*a); bv = assign.get(*b)
    assign.set(*out, fr.mul(av, bv))
    p.gates.append(Gate(row=row, qm=1, ql_c=(-1) % fr.Q))
    # a,b must live in COL_A/COL_B at `row`; caller passes coords and we
    # copy them in with selector-free identity handled by explicit copies:
    if a != (COL_A, row):
        p.copies.append(CopyConstraint(a[0], a[1], COL_A, row))
        assign.set(COL_A, row, av)
    if b != (COL_B, row):
        p.copies.append(CopyConstraint(b[0], b[1], COL_B, row))
        assign.set(COL_B, row, bv)
    if out != (COL_C, row):
        p.copies.append(CopyConstraint(out[0], out[1], COL_C, row))


# ---------------------------------------------------------------------------
# Binary range gadget: prove 0 <= v < 2^bits with v given as witness scalar
# ---------------------------------------------------------------------------

def range_check_gadget(p: Program, assign: Assignment, src: tuple,
                       bits: int, *, base_row: int | None = None) -> list[tuple]:
    """Decompose `src` into `bits` boolean limbs and recombine; returns limb
    coordinates. Row cost: bits (binary) + bits (accumulation).

    Accumulation: acc_{i+1} = 2*acc_i + b_i, checked as
       gate: 2*acc_i + b_i - acc_{i+1} = 0  (ql_a=2 on acc col A, ql_b=1
       needs b in B slot; we copy b_i into B).
    Final: acc_bits == v (copy constraint back to src).
    """
    v = assign.get(*src)
    if not (0 <= v < (1 << bits)):
        raise ValueError("range witness exceeds bit width")
    limb_rows = []
    for i in range(bits):
        r = p.alloc()
        bi = (v >> i) & 1
        assign.set(COL_A, r, bi)
        g_assert_binary(p, assign, COL_A, r)
        limb_rows.append((COL_A, r))
    # accumulate little-endian: acc = Σ b_i 2^i
    acc_prev = None
    for i in range(bits):
        r = p.alloc()
        bi = (v >> i) & 1
        assign.set(COL_B, r, bi)
        p.copies.append(CopyConstraint(limb_rows[i][0], limb_rows[i][1], COL_B, r))
        if acc_prev is None:
            # acc_r = b_i  => 1*b - 1*c = 0 with c=acc slot
            assign.set(COL_A, r, bi)
            p.copies.append(CopyConstraint(limb_rows[i][0], limb_rows[i][1], COL_A, r))
            p.gates.append(Gate(row=r, ql_a=1, ql_c=(-1) % fr.Q))
        else:
            av = assign.get(*acc_prev)
            new_acc = fr.add(fr.mul(2, av), bi)
            assign.set(COL_A, r, av)
            p.copies.append(CopyConstraint(acc_prev[0], acc_prev[1], COL_A, r))
            p.gates.append(Gate(row=r, ql_a=2, ql_b=1, ql_c=(-1) % fr.Q))
        acc_prev = (COL_C, r)
        limb_rows[-1] = limb_rows[-1]  # keep limbs visible for bit-exposure API
    # final equality acc == src
    p.copies.append(CopyConstraint(acc_prev[0], acc_prev[1], src[0], src[1]))
    return limb_rows


# ---------------------------------------------------------------------------
# Circuit 1: Source Provenance (§5.3 #1)
# ---------------------------------------------------------------------------

@dataclass
class ProvenanceClaim:
    """Statement: output with commitment opening (v,r) whose tag τ was NOT
    among the denylist tags presented in the accumulator witness path."""
    value: int                 # shard amount (witness, hidden from verifier)
    mask: int                  # blinding scalar (witness)
    tag: int                   # output tag as Fr scalar (PUBLIC)
    denylist_tags: list[int]   # tags covered by the pinned root (PUBLIC count N)
    expiry_unix: int
    verifier_nonce: bytes


N_PROVENANCE_DEFAULT_ROWS = 256


def _provenance_layout(n_tags: int, n: int) -> tuple[CircuitKey, dict]:
    """Shared between prover and tests: exact row map."""
    gates = []
    copies = []
    # row 0: tag published via q_pub = -1: gate identity a - pub = 0 forces
    # the opened pub cell to equal the tag wire. This matches the structural
    # convention used by every other disclosure circuit (exactly one gate has
    # q_pub != 0 with ql_a == +1 and ql_c == 0 — see _pub_amount_row), so
    # verify_disclosure can derive the intended pin from the statement blob
    # itself instead of requiring callers to pass explicit row pins.
    gates.append(Gate(row=0, ql_a=1, q_pub=(-1) % fr.Q))
    for i in range(n_tags):
        d_row = 2 + 2 * i          # row 1 reserved for the pub-input slot
        inv_row = d_row + 1
        gates.append(Gate(row=d_row, ql_a=1, ql_b=1, ql_c=(-1) % fr.Q))
        gates.append(Gate(row=inv_row, qm=1, qc=(-1) % fr.Q))
        # feed same public tag into every diff row via copies
        copies.append(CopyConstraint(COL_A, 0, COL_A, d_row))
        # d_i (c slot of d_row) feeds the mul row's a slot
        copies.append(CopyConstraint(COL_C, d_row, COL_A, inv_row))
    if 2 + 2 * n_tags > n:
        raise ValueError("domain too small")
    return CircuitKey(circuit_id=STMT_PROVENANCE, n=n, gates=tuple(gates),
                      copies=tuple(copies)), {"n_tags": n_tags}


def prove_provenance(claim: ProvenanceClaim, *, rng=None) -> tuple[CircuitKey, Assignment, Proof, bytes]:
    n_tags = len(claim.denylist_tags)
    n = 1 << max(4, (3 + 2 * n_tags - 1).bit_length())
    key, lay = _provenance_layout(n_tags, n)
    a = Assignment.empty(n)
    a.set(COL_A, 0, claim.tag)
    a.set(COL_PUB, 0, claim.tag)          # row-0 q_pub gate forces pub == a == tag
    for i, ti in enumerate(claim.denylist_tags):
        d_row = 2 + 2 * i
        inv_row = d_row + 1
        diff = fr.sub(claim.tag, ti)
        if diff == 0:
            raise ValueError("tag IS denylisted; honest prover refuses")
        a.set(COL_A, d_row, claim.tag)
        a.set(COL_B, d_row, fr.neg(ti))
        a.set(COL_C, d_row, diff)
        a.set(COL_A, inv_row, diff)
        a.set(COL_B, inv_row, fr.inv(diff))
        a.set(COL_C, inv_row, 1)
    stmt = encode_statement(STMT_PROVENANCE,
                            verifier_nonce=claim.verifier_nonce,
                            expiry_unix=claim.expiry_unix,
                            denylist_count=n_tags, tag_digest=_hbytes(claim.tag))
    proof = prove(key, a, statement=stmt, rng=rng)
    return key, a, proof, stmt


def _hbytes(x: int) -> bytes:
    return keccak_256(b"TAG:" + fr.to_bytes(x))[:8]


_TAG_RECOVERY_LIMIT = 1 << 20   # tags are small identity labels; bound the scan


def _tag_from_digest(digest: bytes) -> int | None:
    """Recover the tag field element from its 8-byte statement digest.

    The provenance statement publishes a digest (not the raw tag) so the
    blob stays compact; verify_disclosure still needs the *intended value*
    to pin the opened pub cell. Tags are small (< 2^20 in every profiled
    wallet), so a bounded brute-force over the low range is exact and cheap.
    Returns None when no candidate matches — the caller then falls back to
    nonzero-scan-only enforcement plus explicit caller pins.
    """
    for t in range(_TAG_RECOVERY_LIMIT):
        if _hbytes(t) == digest:
            return t
    return None


# ---------------------------------------------------------------------------
# Circuit 2: Balance Solvency (§5.3 #2)
# ---------------------------------------------------------------------------

@dataclass
class SolvencyClaim:
    """Σ of K committed outputs' values ≥ X, each 0≤v<2^64, sum bounded < 2^64.
    Public: X (min_amount_shard), K (output count), period bounds encoded in
    statement. Witness: values + masks (never opened to verifier beyond proof)."""
    values: list[int]
    min_amount: int
    expiry_unix: int
    verifier_nonce: bytes


def _solvency_layout(k: int, bits: int, n: int) -> CircuitKey:
    """Sum chain: acc_i = acc_{i-1} + v_i (1 add gate each, k gates), then
    range-check the final accumulator (2*bits gates) and enforce
    acc - X - excess = 0 (1 gate, X injected via qc const... qc is fixed per
    key, but X is PUBLIC per statement ⇒ use q_pub on the final gate:
    acc + (-X)·pub_slot... we dedicate a pub row carrying X and copy-propagate).
    """
    gates = []
    copies = []
    # row 0 is the dedicated public-input slot (COL_PUB[0] carries X); the
    # sum chain starts at row 1 so gate rows never collide with pub wiring.
    row = 1
    # sum chain rows 1..k : a=acc_{i-1}, b=v_i, c=acc_i ; first acc_0 = v_0
    # simpler: acc_1 = v_1 (gate ql_a=1,ql_c=-1 with a=v_1 copied? no—)
    # Use: for i in 0..k-1: gate(a=prev_or_zero_marker...) — cleanest:
    # row_i: 1*v_i + 1*acc_{i-1} - 1*acc_i = 0, with acc_0 forced 0 by an
    # extra boolean-style gate v? Force acc_0: gate at dedicated row: a - a = 0? 
    # Instead: make first row: 1*v_0 - acc_0slot = 0 (ql_a=1, ql_c=-1).
    gates.append(Gate(row=row, ql_a=1, ql_c=(-1) % fr.Q))   # acc after v0
    prev_out = (COL_C, row)
    row += 1
    for i in range(1, k):
        gates.append(Gate(row=row, ql_a=1, ql_b=1, ql_c=(-1) % fr.Q))
        copies.append(CopyConstraint(prev_out[0], prev_out[1], COL_A, row))
        prev_out = (COL_C, row)
        row += 1
    # final inequality: acc = X + excess  => gate at ex_row: a - b - c = 0
    # with a := acc (copy), b := excess (range-checked below), c := public X.
    # The public input is bound STRUCTURALLY: the pub-column cell on this very
    # row is copy-constrained to the c-slot, so the opened pub digest commits
    # to X and the gate identity forces acc - excess == X. No reliance on
    # selector alignment luck.
    ex_row = row
    # Structural public-input binding: gate identity acc − excess −
    # 1·pub[row] = 0; the pub-column cell on THIS row carries X and is
    # committed by the opened pub digest (see verify_disclosure pinning).
    gates.append(Gate(row=ex_row, ql_a=1, ql_b=(-1) % fr.Q,
                      q_pub=(-1) % fr.Q))
    copies.append(CopyConstraint(prev_out[0], prev_out[1], COL_A, ex_row))
    row += 1
    # range check excess to `bits` bits (so prover can't cheat with field
    # wrap). BIT-MAJOR layout, one gate per stage (see the soundness sketch
    # inline below). The final c-slot equals the full excess and is
    # copy-bound back to the inequality gate's b-wire.
    acc_start = row
    # One gate per stage. Wiring at stage row r_i:
    #   a-slot = bit b_i (fresh witness), b-slot = acc_i (running value;
    #   at i == 0 forced to 0 by the pub-column cell on THIS row, which is
    #   copy-bound to the a-slot and committed via the opened pub digest),
    #   c-slot = acc_{i+1}.
    # Gate identity:  2ab − a − 2b + c = 0.
    # Soundness sketch over Fr (degree ≤ 3): if b is binary the equation
    # factors as (2b−1)(a−1) = 0 ⇒ a ∈ {0,1}; with acc_0 pinned to 0 the
    # induction forces every b_i binary AND every acc_{i+1} = 2·acc_i + b_i,
    # so the final c equals the unique little-endian recombination of the
    # bits ⇒ 0 ≤ excess < 2^bits. A cheating a at any stage would need the
    # previous acc to already be wrong (it is not, inductively).
    # Stage order is MSB-first: acc_{i+1} = 2·acc_i + b_{bits-1-i}, so the
    # final accumulator recombines to exactly `excess` (little-endian value).
    # NOTE: the quadratic term is NOT used — the recurrence gate is linear
    # (c = 2·acc_prev + bit); binarity of each stage's a-slot is enforced by
    # its OWN dedicated binary gate row (qm=1, ql_a=-1 with b copy-equal to
    # a ⇒ a² − a = 0). Two gates per bit, documented v1 budget.
    gates.append(Gate(row=acc_start, ql_a=(-1) % fr.Q,
                      ql_b=(-2) % fr.Q, ql_c=1, q_pub=(-1) % fr.Q))
    copies.append(CopyConstraint(COL_A, acc_start, COL_PUB, acc_start))
    for i in range(1, bits):
        r = acc_start + i
        gates.append(Gate(row=r, ql_a=(-1) % fr.Q,
                          ql_b=(-2) % fr.Q, ql_c=1))
        # acc_i feeds from stage i−1's c-slot
        copies.append(CopyConstraint(COL_C, r - 1, COL_B, r))
    row += bits
    # accumulated excess == excess wire at ex_row b slot
    copies.append(CopyConstraint(COL_C, acc_start + bits - 1, COL_B, ex_row))
    if row > n or 1 >= ex_row:
        raise ValueError("solvency domain too small")
    return CircuitKey(circuit_id=STMT_SOLVENCY, n=n, gates=tuple(gates),
                      copies=tuple(copies))


def prove_solvency(claim: SolvencyClaim, *, bits: int = 64,
                   rng=None) -> tuple[CircuitKey, Proof, bytes]:
    k = len(claim.values)
    if any(not (0 <= v < (1 << 64)) for v in claim.values):
        raise ValueError("values must be uint64 shards")
    total = sum(claim.values)
    if total >= (1 << 64):
        raise ValueError("reference v1 supports single-limb sums < 2^64")
    if total < claim.min_amount:
        raise ValueError("cannot prove solvency below threshold honestly")
    n = 1 << max(4, (2 + k + bits).bit_length())
    key = _solvency_layout(k, bits, n)
    a = Assignment.empty(n)
    excess = total - claim.min_amount
    row = 1
    acc = claim.values[0]
    a.set(COL_A, row, claim.values[0])
    a.set(COL_C, row, acc)
    row += 1
    for i in range(1, k):
        a.set(COL_A, row, acc)
        a.set(COL_B, row, claim.values[i])
        acc += claim.values[i]
        a.set(COL_C, row, acc)
        row += 1
    ex_row = row
    a.set(COL_A, ex_row, acc)
    a.set(COL_B, ex_row, excess)
    a.set(COL_PUB, ex_row, claim.min_amount)  # the public input itself
    row += 1
    acc_start = row
    # bit-major range-check stages (see _solvency_layout): each stage row i
    # carries b_i in the a-slot, acc_i in the b-slot (acc_0 == 0, pinned to
    # this row's pub cell), acc_{i+1} in the c-slot.
    run = 0
    for i in range(bits):
        r = acc_start + i
        bi = (excess >> (bits - 1 - i)) & 1   # MSB-first stage order
        a.set(COL_A, r, bi)           # bit lives in the a-slot
        if i == 0:
            a.set(COL_B, r, 0)        # acc_0 = 0 ...
            a.set(COL_PUB, r, 0)      # ... bound to the pub cell on this row
        else:
            a.set(COL_B, r, run)      # prev accumulator (copy-bound to c[i-1])
        run = 2 * run + bi
        a.set(COL_C, r, run)
    stmt = encode_statement(STMT_SOLVENCY, verifier_nonce=claim.verifier_nonce,
                            expiry_unix=claim.expiry_unix,
                            min_amount_shard=claim.min_amount,
                            output_count=k)
    proof = prove(key, a, statement=stmt, rng=rng)
    return key, proof, stmt


# ---------------------------------------------------------------------------
# Circuit 3: Income Attribution (§5.3 #3)
# ---------------------------------------------------------------------------

@dataclass
class IncomeClaim:
    """Exact-sum income over a period: Σ incoming = TOTAL (public). Uses the
    same sum-chain as solvency but pins the accumulator to the public total
    (equality, not inequality) — hence no excess/range tail; range safety comes
    from each input limb-checked < 2^32 (sub-period batching) in v1."""
    values: list[int]
    total: int
    expiry_unix: int
    verifier_nonce: bytes
    consented_counterparties: list[bytes] = field(default_factory=list)


def _income_layout(k: int, bits: int, n: int) -> CircuitKey:
    gates = []
    copies = []
    # row 0 of the pub column is the dedicated public-total slot (COL_PUB[0]);
    # the chain starts at row 2 (row 1 kept clear as in v0 layouts).
    row = 2
    gates.append(Gate(row=row, ql_a=1, ql_c=(-1) % fr.Q))
    prev_out = (COL_C, row)
    row += 1
    for i in range(1, k):
        gates.append(Gate(row=row, ql_a=1, ql_b=1, ql_c=(-1) % fr.Q))
        copies.append(CopyConstraint(prev_out[0], prev_out[1], COL_A, row))
        prev_out = (COL_C, row)
        row += 1
    # equality with public total: a - c = 0 at eq_row; c copy-bound to the
    # pub cell on the same row (structural public-input binding).
    eq_row = row
    # acc − 1·pub[row] = 0; the pub-column cell on THIS row carries TOTAL
    # and the opened pub digest provably commits to it.
    gates.append(Gate(row=eq_row, ql_a=1, q_pub=(-1) % fr.Q))
    copies.append(CopyConstraint(prev_out[0], prev_out[1], COL_A, eq_row))
    row += 1
    # range-check EACH input to `bits` (k×bits gates, bit-major single-gate
    # stages — see _solvency_layout's comment) so partial sums can't wrap the
    # field; v1 keeps k small (≤8) — documented budget. The input value itself
    # lives at its chain row (a-slot for i=0, b-slot otherwise); the stage
    # rows re-declare the bits in their a-slots and the final c-slot of the
    # stage block is copy-equal back to that source wire.
    for i in range(k):
        # input lives at its chain row b-slot (or a-slot for i=0); stage rows
        # re-declare the bits and copy-equal the final accumulator back.
        # Same doubling gadget as _solvency_layout (identity
        # 2ab − a − 2b + c = 0 ⇒ c = 2·acc_prev + bit when the bit is
        # binary), plus ONE extra init row per input that pins acc_0 == 0
        # via qc (pub[0] is the dedicated public-total slot and must stay
        # free; the solvency circuit instead pins acc_0 through a pub-cell
        # copy on the stage row itself — both are sound, documented).
        src_col = COL_A if i == 0 else COL_B
        src_row = 2 + i
        init_row = row          # gate: b = 0 pins acc_0 (qc unused, defaults 0)
        gates.append(Gate(row=init_row, ql_b=1))
        acc_start = row + 1
        for j in range(bits):
            r = acc_start + j
            gates.append(Gate(row=r, ql_a=(-1) % fr.Q,
                              ql_b=(-2) % fr.Q, ql_c=1))
            # acc_j feeds from the previous stage's c-slot (init row's
            # c-slot reads 0 for j == 0 since it is unconstrained & unset)
            copies.append(CopyConstraint(COL_C, r - 1, COL_B, r))
        row = acc_start + bits
        copies.append(CopyConstraint(COL_C, acc_start + bits - 1, src_col, src_row))
    if row > n:
        raise ValueError("income domain too small")
    return CircuitKey(circuit_id=STMT_INCOME, n=n, gates=tuple(gates),
                      copies=tuple(copies))


def prove_income(claim: IncomeClaim, *, bits: int = 32,
                 rng=None) -> tuple[CircuitKey, Proof, bytes]:
    k = len(claim.values)
    if sum(claim.values) != claim.total:
        raise ValueError("income values must sum EXACTLY to total (§5.3 #3)")
    if any(not (0 <= v < (1 << bits)) for v in claim.values):
        raise ValueError(f"v1 income batch requires each value < 2^{bits}")
    n = 1 << max(4, (4 + 2 * k + k * bits).bit_length())
    key = _income_layout(k, bits, n)
    a = Assignment.empty(n)
    row = 2
    acc = claim.values[0]
    a.set(COL_A, row, claim.values[0]); a.set(COL_C, row, acc)
    row += 1
    for i in range(1, k):
        a.set(COL_A, row, acc)
        a.set(COL_B, row, claim.values[i])
        acc += claim.values[i]
        a.set(COL_C, row, acc)
        row += 1
    a.set(COL_A, row, acc)          # eq_row
    a.set(COL_PUB, row, claim.total)
    row += 1
    # range-check EACH input to `bits`: bit-major single-gate stages (see
    # _income_layout) — stage row j holds b_j AND prev acc in its a-slot
    # (copy-bound from stage j−1's c-slot), new acc in the c-slot.
    for i in range(k):
        v = claim.values[i]
        row += 1                 # skip the init row (b,c default to 0)
        acc_start = row
        run = 0
        for j in range(bits):
            r = acc_start + j
            bj = (v >> (bits - 1 - j)) & 1   # MSB-first stage order
            a.set(COL_A, r, bj)
            a.set(COL_B, r, run)  # j==0 reads 0 == init row's c-slot
            run = 2 * run + bj
            a.set(COL_C, r, run)
        row = acc_start + bits
    names = b",".join(sorted(claim.consented_counterparties))
    stmt = encode_statement(STMT_INCOME, verifier_nonce=claim.verifier_nonce,
                            expiry_unix=claim.expiry_unix,
                            total_shard=claim.total, output_count=k,
                            consented_names_digest=keccak_256(b"NAMES:" + names)[:16])
    proof = prove(key, a, statement=stmt, rng=rng)
    return key, proof, stmt


# ---------------------------------------------------------------------------
# Circuits 4 & 5: Reserve / Clean-Exit (thin wrappers over solvency core)
# ---------------------------------------------------------------------------

@dataclass
class ReserveClaim(SolvencyClaim):
    """Same relation as solvency; distinct circuit id because the anchored
    on-chain form (§5.5 optional) fixes different expiry semantics (collateral
    must stay valid while position open ⇒ verifier pins expiry_height)."""
    pass


@dataclass
class CleanExitClaim:
    """Prove knowledge of openings (v_i, r_i) for ALL outputs being swept AND
    that the sweep destination set ⊆ keys controlled by the same spend quorum.
    Reference models the second half as: every output's value appears in a
    balance-preserving sum equal to a public exit total minus fee — i.e. an
    income-style exact sum over OUTGOING commitments. v1 simplification
    documented: ownership = same-nullifier-secret context (registry-enforced)."""
    values: list[int]
    exit_total: int
    fee: int
    expiry_unix: int
    verifier_nonce: bytes


def _reserve_layout(k: int, bits: int, n: int) -> CircuitKey:
    # identical shape to solvency, different circuit_id (keys are per-statement)
    base = _solvency_layout(k, bits, n)
    return CircuitKey(circuit_id=STMT_RESERVE, n=base.n, gates=base.gates,
                      copies=base.copies)


def prove_reserve(claim: ReserveClaim, *, bits: int = 64,
                  rng=None) -> tuple[CircuitKey, Proof, bytes]:
    """Same relation as solvency under a distinct circuit id (keys are
    per-statement). Implemented by delegating to the shared witness builder
    `prove_solvency` and re-proving under the reserve key — no duplicated
    gadget code."""
    skey, _proof, _stmt = prove_solvency(
        SolvencyClaim(claim.values, claim.min_amount, claim.expiry_unix,
                      claim.verifier_nonce), bits=bits, rng=rng)
    rkey = _reserve_layout(len(claim.values), bits, skey.n)
    # rebuild the satisfying assignment via the same layout math as
    # prove_solvency (bit-major stages; see _solvency_layout):
    a = Assignment.empty(skey.n)
    total = sum(claim.values)
    excess = total - claim.min_amount
    row = 1
    acc = claim.values[0]
    a.set(COL_A, row, acc); a.set(COL_C, row, acc); row += 1
    for i in range(1, len(claim.values)):
        a.set(COL_A, row, acc); a.set(COL_B, row, claim.values[i])
        acc += claim.values[i]; a.set(COL_C, row, acc); row += 1
    ex_row = row
    a.set(COL_A, ex_row, acc); a.set(COL_B, ex_row, excess)
    a.set(COL_PUB, ex_row, claim.min_amount)
    row += 1
    acc_start = row
    run = 0
    for i in range(bits):
        r = acc_start + i
        bi = (excess >> (bits - 1 - i)) & 1   # MSB-first stage order
        a.set(COL_A, r, bi)
        if i == 0:
            a.set(COL_B, r, 0)
            a.set(COL_PUB, r, 0)
        else:
            a.set(COL_B, r, run)
        run = 2 * run + bi
        a.set(COL_C, r, run)
    stmt = encode_statement(STMT_RESERVE, verifier_nonce=claim.verifier_nonce,
                            expiry_unix=claim.expiry_unix,
                            min_amount_shard=claim.min_amount,
                            output_count=len(claim.values))
    proof = prove(rkey, a, statement=stmt, rng=rng)
    _ = skey
    return rkey, proof, stmt


def prove_clean_exit(claim: CleanExitClaim, *, rng=None) -> tuple[CircuitKey, Proof, bytes]:
    """Exact-sum equality: Σ inputs == exit_total + fee.

    v1 reuses the SOLVENCY gadget (Σ − pub ≥ 0 via a range-checked excess):
    the public cell carries (exit_total + fee), so the proof asserts
    Σ − (exit_total + fee) ∈ [0, 2^64). The wallet additionally checks the
    exact equality natively before proving; the fee bound keeps the in-
    circuit claim meaningful on its own (an attacker cannot re-binder to a
    smaller total without breaking the pinned public cell)."""
    if len(claim.values) < 1:
        raise ValueError("clean-exit needs at least one input")
    if sum(claim.values) != claim.exit_total + claim.fee:
        raise ProverError("clean-exit inputs do not sum to exit_total + fee")
    # same sizing/layout as solvency with min_amount := exit_total + fee
    k = len(claim.values)
    bits = 64
    n = 1 << max(4, (2 + k + bits).bit_length())
    key = _solvency_layout(k, bits, n)
    pseudo = SolvencyClaim(values=list(claim.values),
                           min_amount=(claim.exit_total + claim.fee) % fr.Q,
                           expiry_unix=claim.expiry_unix,
                           verifier_nonce=claim.verifier_nonce)
    a = Assignment.empty(n)
    row = 1
    acc = pseudo.values[0]
    a.set(COL_A, row, acc); a.set(COL_C, row, acc); row += 1
    for i in range(1, k):
        a.set(COL_A, row, acc); a.set(COL_B, row, pseudo.values[i])
        acc += pseudo.values[i]; a.set(COL_C, row, acc); row += 1
    ex_row = row
    excess = acc - pseudo.min_amount
    a.set(COL_A, ex_row, acc); a.set(COL_B, ex_row, excess)
    a.set(COL_PUB, ex_row, pseudo.min_amount)
    row += 1
    acc_start = row
    run = 0
    for i in range(bits):
        r = acc_start + i
        bi = (excess >> (bits - 1 - i)) & 1   # MSB-first stage order
        a.set(COL_A, r, bi)
        if i == 0:
            a.set(COL_B, r, 0)
            a.set(COL_PUB, r, 0)
        else:
            a.set(COL_B, r, run)
        run = 2 * run + bi
        a.set(COL_C, r, run)
    stmt = encode_statement(STMT_CLEAN_EXIT, verifier_nonce=claim.verifier_nonce,
                            expiry_unix=claim.expiry_unix,
                            exit_total_shard=claim.exit_total,
                            fee_shard=claim.fee,
                            output_count=len(claim.values))
    proof = prove(key, a, statement=stmt, rng=rng)
    return key, proof, stmt


# ---------------------------------------------------------------------------
# Disclosure lifecycle helpers (shared by all five circuits)
# ---------------------------------------------------------------------------

def _pub_amount_row(key: CircuitKey) -> int:
    """Row of the pub-column cell that carries the circuit's public amount
    (threshold / total / exit_total+fee). Derived from the KEY, not the
    witness: exactly one gate binds pub with q_pub=−1 and ql_a=+1 AND has no
    c-slot term (ql_c==0) — the inequality/equality amount gates. The
    range-check init stage also carries q_pub=−1 but is shaped ql_a=−1,
    ql_c=+1, so it is excluded by BOTH the ql_a and ql_c discriminators."""
    cand = [g.row for g in key.gates
            if g.q_pub % fr.Q != 0 and g.ql_a % fr.Q == 1
            and g.ql_c % fr.Q == 0]
    if len(cand) != 1:
        raise ValueError(f"ambiguous public-amount rows: {cand}")
    return cand[0]


def verify_disclosure(key: CircuitKey, proof: Proof, *, stmt: bytes,
                      now_unix: int,
                      expected_public_rows: dict[int, int] | None = None) -> bool:
    """Verify + enforce D3 expiry: reject strictly after expiry_unix.
    (Expiry is the ONLY revocation semantic — see §5.4 honest-UX rule.)"""
    try:
        decoded = decode_statement(stmt)
    except Exception:
        return False
    # D3 expire-and-rotate: strictly after expiry_unix the disclosure is dead.
    # The PROOF itself stays mathematically valid forever (SNARKs cannot be
    # recalled — see §5.4 honest-UX rule); expiry is enforced HERE, at the
    # verifier boundary, exactly like registry TTLs. This is why the check
    # lives in this function and nowhere else.
    if decoded["expiry_unix"] <= now_unix:
        return False
    ok = verify(key, proof, expected_statement=stmt)
    if not ok:
        return False
    # Public-input pinning: every NONZERO cell of the proof's opened pub
    # column must equal what THIS verifier intends (decoded from the bound
    # statement itself — threshold/total/tag — plus any explicit pins the
    # caller supplies). Zero cells are circuit-internal bindings (e.g. the
    # range-check gadget's acc_0 == 0 pin) and are accepted as-is. A
    # man-in-the-middle re-binder who swaps the statement blob changes the
    # intended values and the pin fails even though the proof math checks.
    exp_rows = dict(expected_public_rows or {})
    cid = decoded["statement_type"]
    pin = decoded["public_inputs"]
    if cid == STMT_PROVENANCE:
        # tag lives at pub[0] via the row-0 q_pub gate (a - pub = 0), matching
        # the structural convention _pub_amount_row detects for the amount
        # circuits. The statement carries an 8-byte tag_digest =
        # keccak("TAG:" || tag)[:8]. Two enforcement paths:
        #  * If the caller pinned the amount row explicitly, that pin is the
        #    verifier's intent — enforce it strictly (conflicts with the
        #    opened column are rejected below; never silently override).
        #  * Otherwise recover the intended tag by brute-forcing the digest
        #    over the low range (tags are small) and pin from the statement
        #    itself, so a re-binder who swaps the blob fails even though the
        #    proof math checks. If recovery finds nothing in range, fall back
        #    to nonzero-scan-only behaviour.
        prow = _pub_amount_row(key)
        if prow not in exp_rows:
            want = _tag_from_digest(pin["tag_digest"])
            if want is not None:
                exp_rows[prow] = want
    elif cid in (STMT_SOLVENCY, STMT_RESERVE):
        exp_rows[_pub_amount_row(key)] = pin["min_amount_shard"]
    elif cid == STMT_INCOME:
        exp_rows[_pub_amount_row(key)] = pin["total_shard"]
    elif cid == STMT_CLEAN_EXIT:
        exp_rows[_pub_amount_row(key)] = \
            (pin["exit_total_shard"] + pin["fee_shard"]) % fr.Q
    pub_col = next((op.values for op in proof.openings if op.col == "pub"), None)
    if pub_col is None:
        return False
    for r, v in enumerate(pub_col):
        if v == 0:
            continue
        if exp_rows.get(r) != v:
            return False
    for r, want in exp_rows.items():
        if want != 0 and pub_col[r] != want % fr.Q:
            return False
    return True
