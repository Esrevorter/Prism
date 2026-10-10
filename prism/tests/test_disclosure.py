"""Tests for the Disclosure Registry + off-chain Verifier v0 (spec §5.2–§5.5, §7.6).

Covers: scoped view-key derivation/rotation, honest-path verification, every
rejection gate (expiry, nonce, unknown tx, out-of-window, bad opening, wrong
recipient, duplicate key images, tampered total), registry append-only
semantics with hash-chain tamper detection, and package JSON round-trips.
"""
from __future__ import annotations

import json
import os
import time

import pytest

from prism.crypto.edwards import decode, encode
from prism.crypto.field import L
from prism.crypto.hashing import keccak_256
from prism.crypto.pedersen import commit
from prism.crypto.stealth import (compute_shared_secret, derive_stealth_address,
                                  generate_tx_key_pair, keypair_from_seed)
from prism.wallet.disclosure import (
    DEFAULT_VALIDITY_DAYS, STATEMENT_INCOME_ATTRIBUTION,
    STATEMENT_PAYMENT_EXISTENCE, ChainIndex, ChainTxRecord, DisclosureError,
    DisclosurePackage, DisclosureRegistry, OutputClaim, RegistryTamperError,
    STATUS_ACTIVE, STATUS_EXPIRED, STATUS_REVOKED, ScopedViewKey,
    build_disclosure_package, derive_scope_multiplier, make_scoped_view_key,
    scoped_owns_output, verify_package)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

VIEW_SECRET_A = int.from_bytes(keccak_256(b"test view secret"), "little") % L or 7
SPEND_SECRET_B = int.from_bytes(keccak_256(b"test spend secret"), "little") % L or 11
ROOT_KEY = keccak_256(b"test wallet root key")

VIEW_PUB_A = None  # filled below (needs edwards BASE)
SPEND_PUB_B = None


def setup_module(module=None):
    global VIEW_PUB_A, SPEND_PUB_B
    from prism.crypto.stealth import public_key
    VIEW_PUB_A = public_key(VIEW_SECRET_A)
    SPEND_PUB_B = public_key(SPEND_SECRET_B)


def make_chain_output(txid: bytes, amount: int, blinding: int):
    """Simulate a real stealth output for tx with ephemeral key r.

    Returns (record_patch dict fields, e_shared, P_enc, C_enc).
    """
    r, R = generate_tx_key_pair()
    e = compute_shared_secret(r, VIEW_PUB_A)
    P = derive_stealth_address(e, SPEND_PUB_B)
    C = commit(amount, blinding)
    return {"r": r, "R": R, "e_shared": e,
            "P_enc": encode(P), "C_enc": encode(C)}


@pytest.fixture
def scenario():
    """Two owned outputs in two txs on a small fake chain."""
    setup_module()
    now = 1_760_000_000
    period = (now - 86400 * 30, now + 86400)
    amounts = [5_000_000_000, 3_250_000_000]
    blinds = [int.from_bytes(keccak_256(b"b0"), "little") % L,
              int.from_bytes(keccak_256(b"b1"), "little") % L]
    txids = [keccak_256(b"tx-a"), keccak_256(b"tx-b")]
    images = [keccak_256(b"img-1"), keccak_256(b"img-2")]

    owned, records = [], []
    for i, (amt, bl, txid) in enumerate(zip(amounts, blinds, txids)):
        sim = make_chain_output(txid, amt, bl)
        rec = ChainTxRecord(
            txid=txid, height=1000 + i, block_ts=now - 86400 * (10 - i),
            outputs=((sim["P_enc"], sim["C_enc"]),
                     (encode(decode(sim["P_enc"])), encode(commit(7, 9)))),
            key_images=(images[i],))
        records.append(rec)
        owned.append({"txid": txid, "output_index": 0, "amount_shard": amt,
                      "blinding": bl, "e_shared": sim["e_shared"],
                      "tx_pub_R": sim["R"]})

    chain = ChainIndex(records)
    nonce = keccak_256(b"accountant-nonce")[:16]
    pkg = build_disclosure_package(
        view_secret_a=VIEW_SECRET_A, root_key=ROOT_KEY, view_pub=VIEW_PUB_A,
        scope_id="tax-2026", statement_type=STATEMENT_INCOME_ATTRIBUTION,
        owned=owned, period=period, verifier_nonce=nonce,
        now_ts=now)
    return {"chain": chain, "pkg": pkg, "nonce": nonce, "now": now,
            "period": period, "total": sum(amounts), "owned": owned,
            "records": records, "txids": txids}


# ---------------------------------------------------------------------------
# Scoped view keys
# ---------------------------------------------------------------------------

class TestScopedViewKeys:
    def test_derivation_deterministic_and_in_range(self):
        c1 = derive_scope_multiplier(ROOT_KEY, "scope-x")
        c2 = derive_scope_multiplier(ROOT_KEY, "scope-x")
        assert c1 == c2 and 0 < c1 < L

    def test_rotation_yields_unrelated_multiplier(self):
        c1 = derive_scope_multiplier(ROOT_KEY, "scope-1")
        c2 = derive_scope_multiplier(ROOT_KEY, "scope-2")
        assert c1 != c2

    def test_root_key_length_enforced(self):
        with pytest.raises(DisclosureError):
            derive_scope_multiplier(b"short", "x")

    def test_window_validation(self):
        with pytest.raises(DisclosureError):
            make_scoped_view_key(VIEW_SECRET_A, ROOT_KEY, "s",
                                 SPEND_PUB_B, 100, 100)   # start==end invalid

    def test_public_dict_roundtrip(self, scenario):
        sv = ScopedViewKey.from_public_dict(scenario["pkg"].scoped_key)
        assert sv.to_public_dict() == scenario["pkg"].scoped_key

    def test_malformed_scoped_key_rejected(self):
        with pytest.raises(DisclosureError):
            ScopedViewKey.from_public_dict({"scope_id": "x"})

    def test_scoped_key_cannot_spend(self):
        """Structural guarantee: the package exposes no spend capability —
        it carries s=c·a and B only; b never appears anywhere."""
        pkg_json = json.dumps(json.loads(self._pkg_with_outputs()))
        assert str(SPEND_SECRET_B) not in pkg_json
        assert format(SPEND_SECRET_B, "064x") not in pkg_json

    @staticmethod
    def _pkg_with_outputs():
        setup_module()
        now = 1_760_000_000
        sim = make_chain_output(keccak_256(b"t"), 1000, 42)
        owned = [{"txid": keccak_256(b"t"), "output_index": 0,
                  "amount_shard": 1000, "blinding": 42,
                  "e_shared": sim["e_shared"], "tx_pub_R": sim["R"]}]
        pkg = build_disclosure_package(
            view_secret_a=VIEW_SECRET_A, root_key=ROOT_KEY,
            view_pub=VIEW_PUB_A, scope_id="s",
            statement_type=STATEMENT_PAYMENT_EXISTENCE, owned=owned,
            period=(now - 10, now + 10), verifier_nonce=b"\x01" * 16,
            now_ts=now)
        return pkg.to_json()

    def test_ownership_test_honest_path(self, scenario):
        sv = ScopedViewKey.from_public_dict(scenario["pkg"].scoped_key)
        for claim, rec in zip(scenario["pkg"].claims, scenario["records"]):
            W = decode(bytes.fromhex(claim.w_hex), require_canonical=True)
            P, _C = rec.outputs[claim.output_index]
            assert scoped_owns_output(sv, W, decode(P))

    def test_ownership_fails_for_foreign_output(self, scenario):
        """A random (k, P) pair cannot pass the linear test."""
        sv = ScopedViewKey.from_public_dict(scenario["pkg"].scoped_key)
        other_b = keccak_256(b"someone else") 
        from prism.crypto.stealth import public_key as pk
        foreign_P = derive_stealth_address(b"\x22" * 32, pk(int.from_bytes(other_b, 'little') % L))
        assert not scoped_owns_output(sv, 12345, foreign_P)


# ---------------------------------------------------------------------------
# Verifier v0 — honest path
# ---------------------------------------------------------------------------

class TestVerifierHonestPath:
    def test_valid_package_verifies(self, scenario):
        rep = verify_package(scenario["pkg"], chain=scenario["chain"],
                             expected_nonce=scenario["nonce"],
                             now_ts=scenario["now"])
        assert rep.ok, rep.errors
        assert rep.revealed_total_shard == scenario["total"]
        assert rep.revealed_outputs == 2

    def test_report_carries_honest_limitations_copy(self, scenario):
        rep = verify_package(scenario["pkg"], chain=scenario["chain"],
                             expected_nonce=scenario["nonce"],
                             now_ts=scenario["now"])
        assert any("cannot be recalled" in w for w in rep.warnings)

    def test_expiry_boundary_is_half_open(self, scenario):
        """Exactly at expiry_ts → reject; one second before → accept."""
        base = scenario
        late = verify_package(base["pkg"], chain=base["chain"],
                              expected_nonce=base["nonce"],
                              now_ts=base["pkg"].expiry_ts)
        assert not late.ok and any("expired" in e for e in late.errors)
        early = verify_package(base["pkg"], chain=base["chain"],
                               expected_nonce=base["nonce"],
                               now_ts=base["pkg"].expiry_ts - 1)
        assert early.ok


# ---------------------------------------------------------------------------
# Verifier v0 — every rejection gate
# ---------------------------------------------------------------------------

class TestVerifierRejections:
    def _verify(self, scenario, **kw):
        opts = {"chain": scenario["chain"], "expected_nonce": scenario["nonce"],
                "now_ts": scenario["now"]}
        opts.update(kw)
        return verify_package(scenario["pkg"], **opts)

    def test_wrong_verifier_nonce_rejected(self, scenario):
        rep = self._verify(scenario, expected_nonce=b"other-auditor!!\x00")
        assert not rep.ok and any("nonce mismatch" in e for e in rep.errors)

    def test_unknown_txid_rejected(self, scenario):
        pkg2 = _swap_claim(scenario["pkg"], txid_hex="ab" * 32)
        rep = verify_package(pkg2, chain=scenario["chain"],
                             expected_nonce=scenario["nonce"],
                             now_ts=scenario["now"])
        assert not rep.ok and any("not found on chain" in e for e in rep.errors)

    def test_out_of_period_tx_rejected(self, scenario):
        rec = scenario["chain"].get(scenario["txids"][0])
        moved = ChainTxRecord(rec.txid, rec.height, rec.block_ts - 10**7,
                              rec.outputs, rec.key_images)
        chain = ChainIndex([moved] + [scenario["chain"].get(t)
                                      for t in scenario["txids"][1:]])
        rep = self._verify(scenario, chain=chain)
        assert not rep.ok and any("outside claim period" in e for e in rep.errors)

    def test_bad_commitment_opening_rejected(self, scenario):
        pkg2 = _swap_claim(scenario["pkg"], index=0, blinding_hex=format(
            (int(scenario["pkg"].claims[0].blinding_hex, 16) + 1) % L, "064x"))
        rep = verify_package(pkg2, chain=scenario["chain"],
                             expected_nonce=scenario["nonce"],
                             now_ts=scenario["now"])
        assert not rep.ok and any("commitment opening fails" in e for e in rep.errors)

    def test_wrong_W_fails_ownership(self, scenario):
        bad = bytes((b ^ 0x01) for b in bytes.fromhex(
            scenario["pkg"].claims[0].w_hex))
        pkg2 = _swap_claim(scenario["pkg"], index=0, w_hex=bad.hex())
        rep = verify_package(pkg2, chain=scenario["chain"],
                             expected_nonce=scenario["nonce"],
                             now_ts=scenario["now"])
        assert not rep.ok and any("does not own" in e for e in rep.errors)

    def test_output_index_out_of_range(self, scenario):
        pkg2 = _swap_claim(scenario["pkg"], index=0, output_index=99)
        rep = verify_package(pkg2, chain=scenario["chain"],
                             expected_nonce=scenario["nonce"],
                             now_ts=scenario["now"])
        assert not rep.ok and any("out of range" in e for e in rep.errors)

    def test_duplicate_key_images_flag_double_spend(self, scenario):
        """Second tx claims to also spend img-1 → double-spend evidence."""
        r2 = scenario["chain"].get(scenario["txids"][1])
        tainted = ChainTxRecord(r2.txid, r2.height, r2.block_ts, r2.outputs,
                                (r2.key_images[0], scenario["records"][0].key_images[0]))
        chain = ChainIndex([scenario["chain"].get(scenario["txids"][0]), tainted])
        rep = self._verify(scenario, chain=chain)
        assert not rep.ok and any("double-spend evidence" in e for e in rep.errors)

    def test_tampered_total_rejected(self, scenario):
        d = json.loads(scenario["pkg"].to_json())
        d["total_amount_shard"] += 1
        pkg2 = DisclosurePackage.from_json(json.dumps(d))
        rep = verify_package(pkg2, chain=scenario["chain"],
                             expected_nonce=scenario["nonce"],
                             now_ts=scenario["now"])
        assert not rep.ok and any("does not match" in e for e in rep.errors)

    def test_revoked_registry_status_honoured(self, scenario):
        rep = self._verify(scenario, registry_status=STATUS_REVOKED)
        assert not rep.ok and any("revoked" in e for e in rep.errors)

    def test_future_issue_rejected(self, scenario):
        rep = self._verify(scenario, now_ts=scenario["pkg"].issued_ts - 1)
        assert not rep.ok and any("future" in e for e in rep.errors)

    def test_unsupported_statement_rejected(self, scenario):
        d = json.loads(scenario["pkg"].to_json())
        d["statement_type"] = "balance_solvency"     # Phase-2 circuit only
        pkg2 = DisclosurePackage.from_json(json.dumps(d))
        rep = verify_package(pkg2, chain=scenario["chain"],
                             expected_nonce=scenario["nonce"],
                             now_ts=scenario["now"])
        assert not rep.ok and any("unsupported statement" in e for e in rep.errors)

    def test_unsupported_version_rejected(self, scenario):
        d = json.loads(scenario["pkg"].to_json())
        d["version"] = "prsm-verifier-v9"
        pkg2 = DisclosurePackage.from_json(json.dumps(d))
        rep = verify_package(pkg2, chain=scenario["chain"],
                             expected_nonce=scenario["nonce"],
                             now_ts=scenario["now"])
        assert not rep.ok and any("version" in e for e in rep.errors)

    def test_period_wider_than_window_rejected(self, scenario):
        d = json.loads(scenario["pkg"].to_json())
        d["period"] = [d["period"][0] - 1, d["period"][1]]
        pkg2 = DisclosurePackage.from_json(json.dumps(d))
        rep = verify_package(pkg2, chain=scenario["chain"],
                             expected_nonce=scenario["nonce"],
                             now_ts=scenario["now"])
        assert not rep.ok and any("exceeds scoped key window" in e
                                  for e in rep.errors)


def _swap_claim(pkg: DisclosurePackage, *, txid_hex=None, index=0, **fields):
    """Return pkg with claim[index] replaced (frozen dataclasses → rebuild)."""
    claims = list(pkg.claims)
    old = claims[index].__dict__.copy()
    if txid_hex is not None:
        old["txid_hex"] = txid_hex
    old.update(fields)
    claims[index] = OutputClaim(**old)
    d = json.loads(pkg.to_json())
    d["claims"] = [dict(c.__dict__) for c in claims]
    return DisclosurePackage.from_json(json.dumps(d))


# ---------------------------------------------------------------------------
# Package serialisation
# ---------------------------------------------------------------------------

class TestPackageSerialisation:
    def test_json_roundtrip_stable(self, scenario):
        pkg2 = DisclosurePackage.from_json(scenario["pkg"].to_json())
        assert pkg2.canonical_bytes() == scenario["pkg"].canonical_bytes()
        assert pkg2.disclosure_hash() == scenario["pkg"].disclosure_hash()

    def test_hash_changes_with_any_field(self, scenario):
        h0 = scenario["pkg"].disclosure_hash()
        tampered = _swap_claim(scenario["pkg"], index=0,
                               amount_shard=scenario["pkg"].claims[0].amount_shard + 1)
        # total mismatch alone would fail verify; here just check binding
        assert tampered.disclosure_hash() != h0

    def test_malformed_json_raises_disclosure_error(self):
        with pytest.raises(DisclosureError):
            DisclosurePackage.from_json("{not json")
        with pytest.raises(DisclosureError):
            DisclosurePackage.from_json("{}")


# ---------------------------------------------------------------------------
# Prover-side guards
# ---------------------------------------------------------------------------

class TestProverGuards:
    def test_empty_owned_set_refused(self):
        setup_module()
        with pytest.raises(DisclosureError):
            build_disclosure_package(
                view_secret_a=VIEW_SECRET_A, root_key=ROOT_KEY,
                view_pub=VIEW_PUB_A, scope_id="s",
                statement_type=STATEMENT_INCOME_ATTRIBUTION, owned=[],
                period=(0, 10), verifier_nonce=b"\x00" * 16)

    def test_unsupported_statement_refused(self):
        setup_module()
        with pytest.raises(DisclosureError):
            build_disclosure_package(
                view_secret_a=VIEW_SECRET_A, root_key=ROOT_KEY,
                view_pub=VIEW_PUB_A, scope_id="s",
                statement_type="source_provenance",
                owned=[{"txid": b"\x01" * 32, "output_index": 0,
                        "amount_shard": 1, "blinding": 1,
                        "e_shared": b"\x02" * 32,
                        "tx_pub_R": SPEND_PUB_B}],
                period=(0, 10), verifier_nonce=b"\x00" * 16)

    def test_default_validity_is_90_days(self, scenario):
        p = scenario["pkg"]
        assert p.expiry_ts - p.issued_ts == DEFAULT_VALIDITY_DAYS * 86400


# ---------------------------------------------------------------------------
# Disclosure Registry
# ---------------------------------------------------------------------------

class TestRegistry:
    @pytest.fixture
    def reg(self, tmp_path, scenario):
        r = DisclosureRegistry(str(tmp_path / "registry.jsonl"))
        r.record(scenario["pkg"], verifier_label="Alex (accountant)")
        return r

    def test_record_then_get(self, reg, scenario):
        e = reg.get(scenario["pkg"].disclosure_id)
        assert e is not None
        assert e["status"] == STATUS_ACTIVE
        assert e["verifier_label"] == "Alex (accountant)"
        assert e["disclosure_hash_hex"] == scenario["pkg"].disclosure_hash().hex()

    def test_chain_verifies_after_ops(self, reg, scenario):
        assert reg.verify_chain() == 1
        reg.revoke(scenario["pkg"].disclosure_id)
        assert reg.verify_chain() == 2      # status transition appended

    def test_revoke_is_append_only_transition(self, reg, scenario):
        did = scenario["pkg"].disclosure_id
        with open(reg.path, "r", encoding="utf-8") as f:
            before = f.read()
        assert reg.revoke(did) is True
        with open(reg.path, "r", encoding="utf-8") as f:
            after = f.read()
        assert after.startswith(before)     # original line untouched
        assert reg.effective_status(did) == STATUS_REVOKED

    def test_double_revoke_returns_false(self, reg, scenario):
        did = scenario["pkg"].disclosure_id
        assert reg.revoke(did) is True
        assert reg.revoke(did) is False

    def test_revoke_unknown_returns_false(self, reg):
        assert reg.revoke("no-such-id") is False

    def test_expiry_status_derived_not_stored(self, reg, scenario):
        did = scenario["pkg"].disclosure_id
        assert reg.effective_status(did) == STATUS_ACTIVE
        later = scenario["pkg"].expiry_ts
        assert reg.effective_status(did, now_ts=later) == STATUS_EXPIRED

    def test_active_dashboard_filters(self, reg, tmp_path, scenario):
        # second disclosure, then revoke first
        p2 = DisclosurePackage.from_json(scenario["pkg"].to_json())
        object.__setattr__(p2, "disclosure_id", "second-id")
        reg.record(p2, verifier_label="Tax office")
        did1 = scenario["pkg"].disclosure_id
        actives = {e["disclosure_id"] for e in reg.active_disclosures()}
        assert actives == {did1, "second-id"}
        reg.revoke(did1)
        actives = {e["disclosure_id"] for e in reg.active_disclosures()}
        assert actives == {"second-id"}

    def test_tampered_body_detected(self, reg, scenario):
        with open(reg.path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        obj = json.loads(lines[0])
        obj["body"]["total_amount_shard"] += 1
        lines[0] = json.dumps(obj, sort_keys=True, separators=(",", ":")) + "\n"
        with open(reg.path, "w", encoding="utf-8") as f:
            f.writelines(lines)
        with pytest.raises(RegistryTamperError):
            reg.verify_chain()

    def test_deleted_line_detected(self, reg, tmp_path, scenario):
        p2 = DisclosurePackage.from_json(scenario["pkg"].to_json())
        object.__setattr__(p2, "disclosure_id", "third-id")
        reg.record(p2, verifier_label="x")
        with open(reg.path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        assert len(lines) == 2
        with open(reg.path, "w", encoding="utf-8") as f:
            f.writelines(lines[1:])          # drop first event
        with pytest.raises(RegistryTamperError):
            reg.verify_chain()

    def test_reordered_lines_detected(self, reg, tmp_path, scenario):
        p2 = DisclosurePackage.from_json(scenario["pkg"].to_json())
        object.__setattr__(p2, "disclosure_id", "fourth-id")
        reg.record(p2, verifier_label="x")
        with open(reg.path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        with open(reg.path, "w", encoding="utf-8") as f:
            f.writelines(reversed(lines))
        with pytest.raises(RegistryTamperError):
            reg.verify_chain()

    def test_corrupt_line_detected(self, reg):
        with open(reg.path, "a", encoding="utf-8") as f:
            f.write("garbage-not-json\n")
        with pytest.raises(RegistryTamperError):
            reg.verify_chain()

    def test_fresh_registry_empty(self, tmp_path):
        r = DisclosureRegistry(str(tmp_path / "new.jsonl"))
        assert r.verify_chain() == 0
        assert r.active_disclosures() == []
        assert os.path.exists(r.path)

    def test_end_to_end_tax_season_flow(self, tmp_path, scenario):
        """§7.6 happy path: build → record → share → verify → dashboard →
        revoke → cooperating verifier rejects; registry history intact."""
        reg = DisclosureRegistry(str(tmp_path / "wallet.jsonl"))
        pkg = scenario["pkg"]
        reg.record(pkg, verifier_label="Alex (accountant)")
        rep = verify_package(pkg, chain=scenario["chain"],
                             expected_nonce=scenario["nonce"],
                             now_ts=scenario["now"],
                             registry_status=reg.effective_status(pkg.disclosure_id))
        assert rep.ok
        assert reg.active_disclosures(now_ts=scenario["now"])
        reg.revoke(pkg.disclosure_id, now_ts=scenario["now"])
        rep2 = verify_package(pkg, chain=scenario["chain"],
                              expected_nonce=scenario["nonce"],
                              now_ts=scenario["now"],
                              registry_status=reg.effective_status(pkg.disclosure_id))
        assert not rep2.ok and any("revoked" in e for e in rep2.errors)
        assert reg.verify_chain() == 2       # full history preserved
