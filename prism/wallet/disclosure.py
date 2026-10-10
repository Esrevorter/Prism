"""Disclosure Registry + off-chain Verifier v0 — spec §5.2/§5.4/§5.5, §6.2, §7.6.

Phase-1 deliverable (spec §16): *"Disclosure Registry + scoped view-key
mechanics (off-chain verifier v0)."*

What v0 is — and what it honestly is not
----------------------------------------
Full PLONK disclosure circuits (§5.3) are Phase-2 work gated on the zk
backend; Decision D2 fixes the *choice* of SNARKs, not their availability in
this repo today. Verifier **v0** therefore validates the enumeration form of
the statements that matter for the §7.6 tax-season flow and §7.3 step 6
(receipt disclosure id):

    "payments totalling Σ over [t1,t2] were received by me"
        (income_attribution / payment_existence)

A disclosure package contains: a **scoped view key** V(s,t), the list of
claimed transactions with their public fields, per-output value/blinding
openings and link scalars, a verifier nonce, and an expiry. The verifier
re-checks everything against public chain state it holds:

  1. expiry timestamp not passed (§5.4 rule 1);
  2. verifier-nonce binding (a package is unusable at any other auditor);
  3. every claimed txid exists in the chain index, at a block timestamp
     inside the declared period (§5.2 V(s,t) bounds);
  4. recipient ownership via the scoped view key (§5.2 row 4);
  5. commitment consistency C = v·H + r·G opens exactly, so Σ revealed
     equals the claimed total without trusting the prover's arithmetic;
  6. no duplicate key images across the claimed set (double-spend evidence
     touching attributed income invalidates the statement silently).

Soundness note: forging an ownership claim requires producing k with
k·(c·a)·G == P − B for an output one does not receive — i.e. computing
(a·?) from the published s = c·a alone, an ECDLP. A dishonest prover can
only mislead about *which* outputs to enumerate or omit ones; the values
revealed are pinned to chain commitments by the opening check. What v0
does NOT provide (documented, not hidden): hiding beyond the enumerated
set (the auditor sees the enumeration), non-enumerated balance solvency,
or nullifier tags (§5.3 one-time-nullifier design lands with the circuits).

Scoped view keys (§5.2 V(s,t), rotation per §5.4 rule 2)
--------------------------------------------------------
``scoped_secret s = c·a mod L``, ``c = H(domain ‖ root_key ‖ scope_id)``,
with ``a`` the wallet view secret. For a claimed output the wallet publishes
``k = Hs(e)/c mod L`` (it knows both), making the auditor-side test linear:

    T = k·s   ⇒   T·G + B == P          (same geometry as full scan:
                                         Hs(e)·G + B == P, since k·s = Hs(e))

Rotation: a fresh scope_id yields an unrelated multiplier c′, hence an
unrelated s′; a leaked old scoped key gives nothing about post-rotation
packages because each package re-derives its own k under its own c.

Registry semantics (D3: expire-and-rotate)
------------------------------------------
Append-only JSON-lines log with a keccak hash chain. Revocation is recorded
as a status transition appended as a NEW line — never a deletion or edit.
The honest UX copy (§5.4 hard requirement) is enforced structurally:
"You can limit and expire disclosures; you cannot recall one already
accepted." Verification status derives lazily from (status, expiry_ts, now).
At-rest encryption is delegated to the enclosing encrypted wallet DB (§6.2);
callers MUST place the registry path inside that volume — documented here,
not faked in code.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Iterable, Optional

from prism.crypto.edwards import BASE, Point, decode, encode
from prism.crypto.field import L
from prism.crypto.hashing import keccak_256
from prism.crypto.pedersen import commit

# ---------------------------------------------------------------------------
# Constants — domain tags are consensus-adjacent: changing them invalidates
# previously issued scoped keys and packages. Pinned deliberately.
# ---------------------------------------------------------------------------

SCOPE_KEY_DOMAIN = b"PRISM_SCOPED_VIEWKEY_V1"
PACKAGE_BIND_PREFIX = b"PRISM_DISCLOSURE_PKG_V1"
REGISTRY_GENESIS_HASH = keccak_256(b"PRISM_DISCLOSURE_REGISTRY_GENESIS_v1")

DEFAULT_VALIDITY_DAYS = 90                 # §7.6 step 4 default
VERIFIER_VERSION = "prsm-verifier-v0"

STATUS_ACTIVE = "active"
STATUS_REVOKED = "revoked"
STATUS_EXPIRED = "expired"                 # derived, never stored

STATEMENT_INCOME_ATTRIBUTION = "income_attribution"
STATEMENT_PAYMENT_EXISTENCE = "payment_existence"
SUPPORTED_STATEMENTS = (STATEMENT_INCOME_ATTRIBUTION,
                        STATEMENT_PAYMENT_EXISTENCE)


class DisclosureError(ValueError):
    """Malformed disclosure package, scoped key, or registry operation."""


def _now() -> int:
    return int(time.time())


# ---------------------------------------------------------------------------
# Scoped view keys (§5.2 row 4)
# ---------------------------------------------------------------------------

def derive_scope_multiplier(root_key: bytes, scope_id: str) -> int:
    """c = H(SCOPE_KEY_DOMAIN ‖ root_key ‖ scope_id) mod L, never zero."""
    if len(root_key) != 32:
        raise DisclosureError("root_key must be 32 bytes")
    if not scope_id:
        raise DisclosureError("scope_id required")
    c = int.from_bytes(
        keccak_256(SCOPE_KEY_DOMAIN + root_key + scope_id.encode("utf-8")),
        "little") % L
    return c or 1


@dataclass(frozen=True)
class ScopedViewKey:
    """V(s,t): re-blinded scan secret s = c·a, public spend anchor B, window."""
    scope_id: str
    scoped_secret: int           # s = c·a mod L — shared with chosen auditor
    spend_pub: Point             # B — long-term spend pubkey (public anyway)
    window_start_ts: int
    window_end_ts: int

    def to_public_dict(self) -> dict:
        return {
            "scope_id": self.scope_id,
            "scoped_secret_hex": format(self.scoped_secret, "064x"),
            "spend_pub_hex": encode(self.spend_pub).hex(),
            "window": [self.window_start_ts, self.window_end_ts],
        }

    @classmethod
    def from_public_dict(cls, d: dict) -> "ScopedViewKey":
        try:
            return cls(
                scope_id=str(d["scope_id"]),
                scoped_secret=int(d["scoped_secret_hex"], 16) % L,
                spend_pub=decode(bytes.fromhex(d["spend_pub_hex"]),
                                 require_canonical=True),
                window_start_ts=int(d["window"][0]),
                window_end_ts=int(d["window"][1]),
            )
        except (KeyError, IndexError, TypeError, ValueError) as e:
            raise DisclosureError(f"malformed scoped view key: {e}") from e


def make_scoped_view_key(view_secret_a: int, root_key: bytes, scope_id: str,
                         spend_pub: Point, window_start_ts: int,
                         window_end_ts: int) -> ScopedViewKey:
    """Wallet side: mint V(s,t) = (c·a, B, [start, end])."""
    if not (0 <= window_start_ts < window_end_ts):
        raise DisclosureError("window must satisfy 0 <= start < end")
    c = derive_scope_multiplier(root_key, scope_id)
    return ScopedViewKey(scope_id=scope_id,
                         scoped_secret=(c * view_secret_a) % L,
                         spend_pub=spend_pub,
                         window_start_ts=window_start_ts,
                         window_end_ts=window_end_ts)


def scoped_owns_output(sv: ScopedViewKey, k: int, stealth_P: Point) -> bool:
    """Auditor-side ownership test for one claimed output.

    True iff k·s·G + B == P, where s is the scoped secret and k the link
    scalar shipped in the claim (wallet sets k = Hs(e)/c so that k·s = Hs(e),
    reproducing the standard stealth identity Hs(e)·G + B == P).
    """
    if not (0 <= k < L):
        return False
    T = BASE.mul(k * sv.scoped_secret % L)
    return T.add(sv.spend_pub).encode() == stealth_P.encode()


# ---------------------------------------------------------------------------
# Public chain view (minimal interface for §5.5 off-chain verification)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ChainTxRecord:
    """Public fields of a RingCT tx as they appear on chain (§6.1)."""
    txid: bytes
    height: int
    block_ts: int                    # header timestamp of inclusion block
    outputs: tuple                   # per output: (P_enc 32B, C_enc 32B)
    key_images: tuple = ()           # input images (bytes), double-spend gate


class ChainIndex:
    """Read-only public chain view for the verifier.

    Production backs this with the node DB; tests inject records directly.
    v0 needs only existence, timestamps, public output fields, and images.
    """

    def __init__(self, records: Iterable[ChainTxRecord] = ()):
        self._by_txid: dict[bytes, ChainTxRecord] = {}
        for rec in records:
            self.add(rec)

    def add(self, rec: ChainTxRecord) -> None:
        self._by_txid[rec.txid] = rec

    def get(self, txid: bytes) -> Optional[ChainTxRecord]:
        return self._by_txid.get(txid)


# ---------------------------------------------------------------------------
# Disclosure package (§6.1 anchored-disclosure shape, off-chain transport)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OutputClaim:
    """One claimed received output: location + value opening + link scalar."""
    txid_hex: str
    output_index: int
    amount_shard: int                # v, revealed to this auditor
    blinding_hex: str                # r, opens the on-chain commitment
    k_hex: str                       # k = Hs(e)/c mod L


@dataclass(frozen=True)
class DisclosurePackage:
    """Everything the auditor receives out-of-band (§7.6 step 4)."""
    disclosure_id: str               # uuid4; matches registry entry
    statement_type: str
    scoped_key: dict                 # ScopedViewKey.to_public_dict()
    claims: tuple                    # tuple[OutputClaim, ...]
    total_amount_shard: int
    period: tuple                    # (t1, t2) UTC, ⊆ scoped window
    verifier_nonce_hex: str
    issued_ts: int
    expiry_ts: int
    version: str = VERIFIER_VERSION

    def canonical_bytes(self) -> bytes:
        """Deterministic serialisation bound into hash/expiry checks."""
        d = {
            "disclosure_id": self.disclosure_id,
            "statement_type": self.statement_type,
            "scoped_key": self.scoped_key,
            "claims": [dict(c.__dict__) for c in self.claims],
            "total_amount_shard": self.total_amount_shard,
            "period": list(self.period),
            "verifier_nonce_hex": self.verifier_nonce_hex,
            "issued_ts": self.issued_ts,
            "expiry_ts": self.expiry_ts,
            "version": self.version,
        }
        return PACKAGE_BIND_PREFIX + json.dumps(
            d, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def disclosure_hash(self) -> bytes:
        return keccak_256(self.canonical_bytes())

    def to_json(self) -> str:
        d = {
            "disclosure_id": self.disclosure_id,
            "statement_type": self.statement_type,
            "scoped_key": self.scoped_key,
            "claims": [dict(c.__dict__) for c in self.claims],
            "total_amount_shard": self.total_amount_shard,
            "period": list(self.period),
            "verifier_nonce_hex": self.verifier_nonce_hex,
            "issued_ts": self.issued_ts,
            "expiry_ts": self.expiry_ts,
            "version": self.version,
        }
        return json.dumps(d, sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_json(cls, blob: str | bytes) -> "DisclosurePackage":
        try:
            d = json.loads(blob)
            claims = tuple(OutputClaim(**c) for c in d["claims"])
            return cls(
                disclosure_id=d["disclosure_id"],
                statement_type=d["statement_type"],
                scoped_key=d["scoped_key"],
                claims=claims,
                total_amount_shard=int(d["total_amount_shard"]),
                period=(int(d["period"][0]), int(d["period"][1])),
                verifier_nonce_hex=d["verifier_nonce_hex"],
                issued_ts=int(d["issued_ts"]),
                expiry_ts=int(d["expiry_ts"]),
                version=d.get("version", VERIFIER_VERSION),
            )
        except (KeyError, TypeError, ValueError) as e:
            raise DisclosureError(f"malformed package: {e}") from e


# ---------------------------------------------------------------------------
# Prover side: build a package from owned outputs (§7.6 steps 2–3)
# ---------------------------------------------------------------------------

def build_disclosure_package(*, view_secret_a: int, root_key: bytes,
                             spend_pub: Point, scope_id: str,
                             statement_type: str, owned: list[dict],
                             period: tuple[int, int],
                             verifier_nonce: bytes,
                             expiry_days: int = DEFAULT_VALIDITY_DAYS,
                             now_ts: Optional[int] = None
                             ) -> DisclosurePackage:
    """Mint V(s,t) and enumerate `owned` outputs into a package.

    Each element of `owned` (wallet-side Owned-Output record, §6.2):
        txid (bytes), output_index, amount_shard, blinding (int),
        e_shared (bytes — keccak(r·A) from the scan), 
    The wallet knows a and computes Hs(e) itself; it publishes
    k = Hs(e)·c⁻¹ mod L so the auditor's test is linear in s = c·a.
    """
    if statement_type not in SUPPORTED_STATEMENTS:
        raise DisclosureError(f"unsupported statement {statement_type!r}")
    if not owned:
        raise DisclosureError("cannot disclose an empty set")
    now = _now() if now_ts is None else now_ts
    sv = make_scoped_view_key(view_secret_a, root_key, scope_id,
                              spend_pub, period[0], period[1])
    c = derive_scope_multiplier(root_key, scope_id)
    c_inv = pow(c, L - 2, L)
    claims = []
    total = 0
    for o in owned:
        hs_e = int.from_bytes(o["e_shared"], "little") % L
        k = hs_e * c_inv % L
        claims.append(OutputClaim(
            txid_hex=o["txid"].hex(),
            output_index=int(o["output_index"]),
            amount_shard=int(o["amount_shard"]),
            blinding_hex=format(int(o["blinding"]), "064x"),
            k_hex=format(k, "064x"),
        ))
        total += int(o["amount_shard"])
    return DisclosurePackage(
        disclosure_id=str(uuid.uuid4()),
        statement_type=statement_type,
        scoped_key=sv.to_public_dict(),
        claims=tuple(claims),
        total_amount_shard=total,
        period=period,
        verifier_nonce_hex=verifier_nonce.hex(),
        issued_ts=now,
        expiry_ts=now + expiry_days * 86400,
    )


# ---------------------------------------------------------------------------
# Verifier v0 (§5.5 default path: off-chain, against public chain state)
# ---------------------------------------------------------------------------

@dataclass
class VerificationReport:
    ok: bool
    disclosure_id: str
    revealed_total_shard: int
    revealed_outputs: int
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    verifier_version: str = VERIFIER_VERSION


def verify_package(pkg: DisclosurePackage, *, chain: ChainIndex,
                   expected_nonce: bytes, now_ts: Optional[int] = None,
                   registry_status: Optional[str] = None
                   ) -> VerificationReport:
    """Verify one disclosure package. Never raises on bad data — reports.

    `registry_status`: issuer-side knowledge (from the user's own registry).
    Expiry is enforced cryptographically-by-timestamp; revocation is honoured
    cooperatively here because an accepted disclosure cannot technically be
    recalled (§5.4, D3) — the report says so plainly.
    """
    errs: list[str] = []
    warns: list[str] = []
    now = _now() if now_ts is None else now_ts

    # 0. version + statement support
    if pkg.version != VERIFIER_VERSION:
        errs.append(f"unsupported verifier version {pkg.version!r}")
    if pkg.statement_type not in SUPPORTED_STATEMENTS:
        errs.append(f"unsupported statement {pkg.statement_type!r}")

    # 1. expiry (§5.4 rule 1: verifiers reject expired proofs)
    if now >= pkg.expiry_ts:
        errs.append("disclosure expired")
    if pkg.issued_ts > now:
        errs.append("disclosure issued in the future")

    # 2. verifier-nonce binding (§5.4: bound to verifier-nonce)
    try:
        nonce = bytes.fromhex(pkg.verifier_nonce_hex)
    except ValueError:
        nonce = b"\x00"
        errs.append("verifier nonce not hex")
    if nonce != expected_nonce:
        errs.append("verifier nonce mismatch (package bound to another auditor)")

    # 3. window sanity
    try:
        sv = ScopedViewKey.from_public_dict(pkg.scoped_key)
    except DisclosureError as e:
        return VerificationReport(False, pkg.disclosure_id, 0, 0,
                                  errors=[str(e)], warnings=warns)
    if pkg.period[0] < sv.window_start_ts or pkg.period[1] > sv.window_end_ts:
        errs.append("claim period exceeds scoped key window")
    if not pkg.claims:
        errs.append("empty claim set")

    # 4. per-claim checks against public chain state
    seen_images: set[bytes] = set()
    dup_images: set[bytes] = set()
    revealed_total = 0
    ok_claims = 0
    for c in pkg.claims:
        try:
            txid = bytes.fromhex(c.txid_hex)
            k = int(c.k_hex, 16)
            r_blind = int(c.blinding_hex, 16)
        except (ValueError, TypeError) as e:
            errs.append(f"claim parse failed: {e}")
            continue
        rec = chain.get(txid)
        if rec is None:
            errs.append(f"tx {c.txid_hex[:16]}... not found on chain")
            continue
        if not (pkg.period[0] <= rec.block_ts <= pkg.period[1]):
            errs.append(f"tx {c.txid_hex[:16]}... outside claim period")
            continue
        if c.output_index >= len(rec.outputs):
            errs.append(f"output index {c.output_index} out of range "
                        f"for tx {c.txid_hex[:16]}...")
            continue
        P_enc, C_enc = rec.outputs[c.output_index]
        try:
            P = decode(P_enc, require_canonical=True)
            C = decode(C_enc, require_canonical=True)
        except ValueError:
            errs.append(f"tx {c.txid_hex[:16]}... output bytes undecodable")
            continue

        # 4a. commitment opening binds revealed value to chain: C == v·H + r·G
        if commit(c.amount_shard, r_blind).encode() != C.encode():
            errs.append(f"commitment opening fails for "
                        f"{c.txid_hex[:16]}...[{c.output_index}]")
            continue

        # 4b. recipient ownership via the scoped key
        if not scoped_owns_output(sv, k, P):
            errs.append(f"scoped key does not own "
                        f"{c.txid_hex[:16]}...[{c.output_index}]")
            continue

        # 4c. key-image hygiene across claimed txs (double-spend evidence)
        for img in rec.key_images:
            if img in seen_images:
                dup_images.add(img)
            seen_images.add(img)
        revealed_total += c.amount_shard
        ok_claims += 1

    if dup_images:
        errs.append(f"double-spend evidence: {len(dup_images)} duplicate key "
                    f"image(s) among claimed transactions")

    # 5. total consistency (Σ over *verified* claims vs claimed total)
    if revealed_total != pkg.total_amount_shard or ok_claims != len(pkg.claims):
        errs.append(f"claimed total {pkg.total_amount_shard} does not match "
                    f"verified revealed total {revealed_total} "
                    f"({ok_claims}/{len(pkg.claims)} claims verified)")

    # 6. issuer-side revocation (cooperative, per D3)
    if registry_status == STATUS_REVOKED:
        errs.append("disclosure revoked by issuer per registry")

    warns.append("expiry limits reuse; an already-accepted disclosure cannot "
                 "be recalled")

    return VerificationReport(
        ok=not errs,
        disclosure_id=pkg.disclosure_id,
        revealed_total_shard=revealed_total,
        revealed_outputs=ok_claims,
        errors=errs,
        warnings=warns,
    )


# ---------------------------------------------------------------------------
# Disclosure Registry (§5.3: logs every generated proof; §7.6 step 5)
# ---------------------------------------------------------------------------

class RegistryTamperError(RuntimeError):
    """Hash chain broken: entries modified, deleted, or reordered on disk."""


@dataclass
class RegistryEntry:
    """One append-only event. Status changes are new events, same id."""
    disclosure_id: str
    statement_type: str
    scope_id: str
    verifier_label: str              # who it was shared to (plain language)
    period: tuple
    total_amount_shard: int
    issued_ts: int
    expiry_ts: int
    disclosure_hash_hex: str         # binds the exact artifact revealed
    status: str = STATUS_ACTIVE
    entry_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def body(self) -> dict:
        return {
            "entry_id": self.entry_id,
            "disclosure_id": self.disclosure_id,
            "statement_type": self.statement_type,
            "scope_id": self.scope_id,
            "verifier_label": self.verifier_label,
            "period": list(self.period),
            "total_amount_shard": self.total_amount_shard,
            "issued_ts": self.issued_ts,
            "expiry_ts": self.expiry_ts,
            "disclosure_hash_hex": self.disclosure_hash_hex,
            "status": self.status,
        }


class DisclosureRegistry:
    """Append-only, hash-chained local log (§6.2 wallet-side structures).

    One JSON line per event; each line stores prev-hash chained with the
    entry body (keccak). The file is write-append only through this API;
    integrity is provable after the fact via :meth:`verify_chain`.
    """

    def __init__(self, path: str):
        self.path = path
        if not os.path.exists(path):
            open(path, "a", encoding="utf-8").close()

    @staticmethod
    def _line_hash(prev: bytes, body: dict) -> bytes:
        blob = json.dumps(body, sort_keys=True,
                          separators=(",", ":")).encode("utf-8")
        return keccak_256(prev + blob)

    def _iter_raw(self):
        with open(self.path, "r", encoding="utf-8") as f:
            for n, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    yield n, obj["prev_hash_hex"], obj["body"]
                except (KeyError, ValueError) as e:
                    raise RegistryTamperError(f"line {n}: unparseable ({e})") from e

    def _head(self) -> bytes:
        prev = REGISTRY_GENESIS_HASH
        for _, prev_hex, body in self._iter_raw():
            if bytes.fromhex(prev_hex) != prev:
                raise RegistryTamperError("hash chain discontinuity")
            prev = self._line_hash(prev, body)
        return prev

    def verify_chain(self) -> int:
        """Recompute the full chain; returns event count, raises on tamper."""
        prev = REGISTRY_GENESIS_HASH
        count = 0
        for n, prev_hex, body in self._iter_raw():
            if bytes.fromhex(prev_hex) != prev:
                raise RegistryTamperError(
                    f"line {n}: prev_hash does not match recomputed chain")
            prev = self._line_hash(prev, body)
            count += 1
        return count

    # -- operations ---------------------------------------------------------

    def record(self, pkg: DisclosurePackage, *,
               verifier_label: str) -> RegistryEntry:
        """Log a freshly generated disclosure (§7.6 step 5)."""
        e = RegistryEntry(
            disclosure_id=pkg.disclosure_id,
            statement_type=pkg.statement_type,
            scope_id=pkg.scoped_key["scope_id"],
            verifier_label=verifier_label,
            period=tuple(pkg.period),
            total_amount_shard=pkg.total_amount_shard,
            issued_ts=pkg.issued_ts,
            expiry_ts=pkg.expiry_ts,
            disclosure_hash_hex=pkg.disclosure_hash().hex(),
        )
        self._append(e.body())
        return e

    def revoke(self, disclosure_id: str, *,
               now_ts: Optional[int] = None) -> bool:
        """Mark revoked via a status-transition append (history untouched).

        Returns False if unknown or already terminal. Honest semantics: this
        stops *future cooperative* verifiers; it cannot un-reveal anything
        (§5.4 hard-requirement copy).
        """
        latest = self.get(disclosure_id)
        if latest is None or latest["status"] != STATUS_ACTIVE:
            return False
        now = _now() if now_ts is None else now_ts
        body = dict(latest)
        body["entry_id"] = str(uuid.uuid4())
        body["status"] = STATUS_REVOKED
        body["revoked_ts"] = now
        self._append(body)
        return True

    def get(self, disclosure_id: str) -> Optional[dict]:
        """Latest state of one disclosure (fold over append-only events)."""
        best = None
        for _, _, body in self._iter_raw():
            if body["disclosure_id"] == disclosure_id:
                best = body
        return best

    def effective_status(self, disclosure_id: str,
                         now_ts: Optional[int] = None) -> Optional[str]:
        """active | revoked | expired | None — expiry computed, not stored."""
        e = self.get(disclosure_id)
        if e is None:
            return None
        if e["status"] == STATUS_REVOKED:
            return STATUS_REVOKED
        now = _now() if now_ts is None else now_ts
        if now >= e["expiry_ts"]:
            return STATUS_EXPIRED
        return STATUS_ACTIVE

    def active_disclosures(self, now_ts: Optional[int] = None) -> list:
        """Dashboard feed (§7.6 step 5: 'Active disclosures')."""
        now = _now() if now_ts is None else now_ts
        latest: dict[str, dict] = {}
        for _, _, body in self._iter_raw():
            latest[body["disclosure_id"]] = body
        return sorted(
            (e for e in latest.values()
             if e["status"] == STATUS_ACTIVE and now < e["expiry_ts"]),
            key=lambda e: e["issued_ts"])

    def _append(self, body: dict) -> None:
        head = self._head()
        line = {"prev_hash_hex": head.hex(), "body": body}
        blob = json.dumps(line, sort_keys=True, separators=(",", ":"))
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(blob + "\n")
