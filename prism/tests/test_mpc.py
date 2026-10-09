"""MPC custody stack tests — sharing (Shamir+Feldman), FROST threshold
EdDSA, GG20 threshold ECDSA, and the social-recovery state machine.

House rules (mirrors crypto/ and zk/ test suites): deterministic seeds for
reproducibility, adversarial/tamper cases alongside happy paths, and every
protocol-level invariant from spec §7.3 / threat model E9 gets a named test.
"""
from __future__ import annotations

import random

import pytest

from prism.crypto.edwards import BASE as EDWARDS_BASE
from prism.crypto.edwards import IDENTITY_POINT, Point
from prism.crypto.field import L

from prism.mpc import frost, gg20, recovery, secp256k1, sharing
from prism.mpc.secp256k1 import BASE as SECP_BASE
from prism.mpc.sharing import (ED25519_SCALARS, Share, combine_commitments,
                               eval_poly, lagrange_coefficients, make_shares,
                               poly_commitments, random_poly, reconstruct,
                               verify_share)

_F = ED25519_SCALARS
T0 = 1_800_000_000          # fixed "now" for all recovery tests


def _rng(seed: int) -> random.Random:
    return random.Random(seed)


def _blinds(n: int, seed: int) -> dict[int, int]:
    r = _rng(seed)
    return {i: r.randrange(1, L) for i in range(1, n + 1)}


# ===========================================================================
# sharing.py — Shamir secret sharing + Feldman VSS
# ===========================================================================

class TestSharing:
    def test_reconstruct_matches_secret(self):
        r = _rng(1)
        secret = r.randrange(1, L)
        coeffs = random_poly(secret, 3, _F, rng=r)
        shares = make_shares(coeffs, 5, _F)
        for subset in ([0, 1, 2], [0, 2, 4], [1, 3, 4]):
            picked = [shares[i] for i in subset]
            assert reconstruct(picked, _F) == secret

    def test_fewer_than_threshold_gives_nothing_useful(self):
        secret = 12345
        coeffs = random_poly(secret, 3, _F, rng=_rng(2))
        shares = make_shares(coeffs, 5, _F)
        wrong = reconstruct(shares[:2], _F)   # degree-2 poly from 2 points
        assert wrong != secret

    def test_eval_poly_horner(self):
        # f(x) = 5 + 0x + 1x^2  => f(3) = 14
        assert eval_poly([5, 0, 1], 3, _F) == 14

    def test_lagrange_known_values(self):
        lam = lagrange_coefficients([1, 2, 3], _F)
        # λ for x=0 on {1,2,3}: 3, -3, 1
        assert lam[1] == 3
        assert lam[2] == _F.reduce(-3)
        assert lam[3] == 1

    def test_lagrange_rejects_bad_sets(self):
        with pytest.raises(ValueError):
            lagrange_coefficients([], _F)
        with pytest.raises(ValueError):
            lagrange_coefficients([1, 1, 2], _F)

    def test_feldman_verify_accepts_valid_rejects_tampered(self):
        coeffs = random_poly(987, 3, _F, rng=_rng(3))
        commits = poly_commitments(coeffs, EDWARDS_BASE)
        shares = make_shares(coeffs, 5, _F)
        for sh in shares:
            assert verify_share(sh, commits, EDWARDS_BASE, _F)
        tampered = Share(3, shares[2].value + 1)
        assert not verify_share(tampered, commits, EDWARDS_BASE, _F)

    def test_combine_commitments_is_linear(self):
        c1 = random_poly(11, 3, _F, rng=_rng(4))
        c2 = random_poly(22, 3, _F, rng=_rng(5))
        j = combine_commitments([poly_commitments(c1, EDWARDS_BASE),
                                 poly_commitments(c2, EDWARDS_BASE)])
        assert j[0] == EDWARDS_BASE.mul(33)
        with pytest.raises(ValueError):
            combine_commitments([])
        with pytest.raises(ValueError):
            combine_commitments([poly_commitments(c1, EDWARDS_BASE),
                                 poly_commitments([1, 2], EDWARDS_BASE)])


# ===========================================================================
# frost.py — threshold EdDSA (shipped spend path)
# ===========================================================================

class TestFrost:
    def test_keygen_structure(self):
        cer = frost.run_keygen(_blinds(5, 10), n=5, t=3)
        assert len(cer.shares) == 5
        joint = combine_commitments(
            [cer.commitments[i] for i in sorted(cer.commitments)])
        assert joint[0] == cer.public_key
        for idx, val in cer.joint_poly_at.items():
            assert cer.shares[idx].value == val

    @pytest.mark.parametrize("seed", [11, 12])
    def test_sign_and_verify_roundtrip(self, seed):
        cer = frost.run_keygen(_blinds(5, seed), n=5, t=3)
        msg = b"send 50 PRSM to alex"
        sig = frost.sign_threshold(cer, [1, 3, 5], msg, rng=_rng(seed))
        assert frost.verify_signature(sig, msg, cer.public_key)
        # any other quorum of size >= 2 also verifies...
        sig2 = frost.sign_threshold(cer, [2, 4], msg, rng=_rng(seed + 1))
        assert frost.verify_signature(sig2, msg, cer.public_key)
        # ...and both are valid standalone EdDSA-style signatures
        assert len(sig) == len(sig2) == 64

    def test_wrong_message_or_key_fails_verification(self):
        cer = frost.run_keygen(_blinds(5, 13), n=5, t=3)
        sig = frost.sign_threshold(cer, [1, 2], b"a", rng=_rng(14))
        assert not frost.verify_signature(sig, b"b", cer.public_key)
        other = frost.run_keygen(_blinds(5, 15), n=5, t=3)
        assert not frost.verify_signature(sig, b"a", other.public_key)

    def test_tampered_signature_rejected(self):
        cer = frost.run_keygen(_blinds(5, 16), n=5, t=3)
        sig = bytearray(frost.sign_threshold(cer, [1, 2, 3], b"m",
                                             rng=_rng(17)))
        sig[40] ^= 0x01
        assert not frost.verify_signature(bytes(sig), b"m", cer.public_key)

    def test_keygen_rejects_malformed_ceremonies(self):
        blinds = _blinds(5, 18)
        with pytest.raises(ValueError):
            frost.run_keygen(blinds, n=5, t=0)
        with pytest.raises(ValueError):
            frost.run_keygen(blinds, n=5, t=6)
        with pytest.raises(ValueError):
            frost.run_keygen(blinds, n=5, t=1)      # degenerate t=1 banned
        with pytest.raises(ValueError):
            frost.run_keygen({1: 1, 2: 2}, n=5, t=3)  # missing signers

    def test_sign_requires_known_signers(self):
        cer = frost.run_keygen(_blinds(5, 19), n=5, t=3)
        with pytest.raises(ValueError):
            frost.sign_threshold(cer, [1, 9], b"m", rng=_rng(20))
        with pytest.raises(ValueError):
            frost.sign_threshold(cer, [1], b"m")     # single signer


# ===========================================================================
# secp256k1.py — group sanity
# ===========================================================================

class TestSecp256k1:
    def test_base_order(self):
        assert SECP_BASE.mul(secp256k1.N).is_identity
        assert SECP_BASE.mul(1) == SECP_BASE

    def test_compressed_codec(self):
        p = SECP_BASE.mul(123456789)
        assert secp256k1.ECPoint.decode(p.encode_compressed()) == p

    def test_point_arithmetic(self):
        a = SECP_BASE.mul(7)
        b = SECP_BASE.mul(9)
        assert a.add(b) == SECP_BASE.mul(16)
        assert a.negate().add(a).is_identity


# ===========================================================================
# gg20.py — threshold ECDSA (bridge-signer path)
# ===========================================================================

class TestGG20:
    def _keys(self, seed=30):
        blinds = {i: _rng(seed).randrange(1, secp256k1.N)
                  for i in range(1, 6)}
        return gg20.gg20_keygen(blinds, n=5, t=3)

    def test_keygen_pubkey_matches_blind_sum(self):
        blinds = {i: _rng(31).randrange(1, secp256k1.N)
                  for i in range(1, 6)}
        keys = gg20.gg20_keygen(blinds, n=5, t=3)
        x = sum(blinds.values()) % secp256k1.N
        assert keys.public_key == SECP_BASE.mul(x)

    def test_gg20_delta_identity(self):
        """Lagrange-adjusted shares must sum to the joint secret — this is
        the invariant gg20_sign's MtA rounds rely on (Σ x_adj == x, hence
        z == kk·x). Verified directly here and end-to-end via ECDSA checks
        in test_sign_verifies_with_standard_ecdsa."""
        keys = self._keys()
        lam = lagrange_coefficients([1, 2, 3], gg20._SC)
        x_adj = {i: lam[i] * keys.shares[i].value % secp256k1.N
                 for i in (1, 2, 3)}
        assert sum(x_adj.values()) % secp256k1.N == reconstruct(
            [keys.shares[i] for i in (1, 2, 3)], gg20._SC)
        secret = reconstruct([keys.shares[i] for i in (1, 2, 3)], gg20._SC)
        assert SECP_BASE.mul(secret) == keys.public_key

    @pytest.mark.parametrize("quorum", [[1, 2, 3], [2, 4, 5], [1, 3, 5]])
    def test_sign_verifies_with_standard_ecdsa(self, quorum):
        keys = self._keys()
        h = 0xCAFEBABEDEADBEEF % secp256k1.N
        sig = gg20.gg20_sign(keys, quorum, h, rng=_rng(40 + quorum[0]))
        assert gg20.ecdsa_verify(sig, h, keys.public_key)
        assert 1 <= sig.s <= secp256k1.N // 2      # low-s (BIP 62)

    def test_wrong_hash_fails(self):
        keys = self._keys()
        sig = gg20.gg20_sign(keys, [1, 2, 3], 5, rng=_rng(41))
        assert not gg20.ecdsa_verify(sig, 6, keys.public_key)

    def test_quorum_below_threshold_rejected(self):
        keys = self._keys()
        with pytest.raises(ValueError):
            gg20.gg20_sign(keys, [1, 2], 7, rng=_rng(42))
        with pytest.raises(ValueError):
            gg20.gg20_sign(keys, [1, 2, 9], 7, rng=_rng(43))

    def test_der_encoding_shape(self):
        keys = self._keys()
        sig = gg20.gg20_sign(keys, [1, 2, 3], 9, rng=_rng(44))
        der = sig.der()
        assert der[0] == 0x30
        assert len(der) <= 72                       # BTC relay limit

    def test_keygen_validation(self):
        blinds = {i: 3 for i in range(1, 6)}
        with pytest.raises(ValueError):
            gg20.gg20_keygen(blinds, n=5, t=1)
        with pytest.raises(ValueError):
            gg20.gg20_keygen({1: 1}, n=5, t=3)


# ===========================================================================
# recovery.py — social recovery state machine (§7.3 / E9)
# ===========================================================================

def _setup(seed=50, n=5, t=3):
    cer = frost.run_keygen(_blinds(n, seed), n=n, t=t)
    contacts = {i: f"leg-{i}" for i in range(1, n + 1)}
    st = recovery.create_recovery_state(
        cer.public_key, cer.joint_commitments(), t, contacts,
        device_legs=[1, 2])
    return cer, st


class TestRecoveryInitiation:
    def test_happy_path_pending_window(self):
        cer, st = _setup()
        pend = st.initiate_recovery(T0, [2, 3, 4],
                                    recovery.derive_stale_share(
                                        cer.shares, [5]))
        assert st.phase is recovery.Phase.PENDING
        assert pend.activates_at == T0 + 72 * recovery.HOURS
        assert st.window_remaining(T0) == 72 * 3600

    def test_garbage_stale_share_cannot_initiate(self):
        cer, st = _setup(seed=51)
        bad = {5: cer.shares[5].value + 1}
        with pytest.raises(ValueError, match="Feldman"):
            st.initiate_recovery(T0, [2, 3, 4], bad)
        assert st.phase is recovery.Phase.ACTIVE

    def test_no_live_leg_refused(self):
        # full-quorum-loss recovery can't offer cancellation guarantees
        cer, st = _setup(seed=52)
        with pytest.raises(ValueError, match="out of scope"):
            st.initiate_recovery(T0, [2, 3, 4], {})

    def test_wrong_collector_count(self):
        cer, st = _setup(seed=53)
        stale = recovery.derive_stale_share(cer.shares, [5])
        with pytest.raises(ValueError):
            st.initiate_recovery(T0, [2, 3], stale)          # too few
        with pytest.raises(ValueError):
            st.initiate_recovery(T0, [2, 3, 4, 5], stale)    # too many
        with pytest.raises(ValueError):
            st.initiate_recovery(T0, [2, 2, 4], stale)       # dupes

    def test_double_pending_blocked(self):
        cer, st = _setup(seed=54)
        stale = recovery.derive_stale_share(cer.shares, [5])
        st.initiate_recovery(T0, [2, 3, 4], stale)
        with pytest.raises(RuntimeError):
            st.initiate_recovery(T0 + 10, [1, 3, 4], stale)

    def test_device_leg_policy_knob(self):
        cer = frost.run_keygen(_blinds(5, 55), n=5, t=3)
        policy = recovery.RecoveryPolicy(require_device_leg=True)
        st = recovery.create_recovery_state(
            cer.public_key, cer.joint_commitments(), 3,
            {i: f"leg-{i}" for i in range(1, 6)}, policy=policy,
            device_legs=[1])
        stale = recovery.derive_stale_share(cer.shares, [5])
        with pytest.raises(ValueError, match="device"):
            st.initiate_recovery(T0, [2, 3, 4], stale)   # no device leg
        st.initiate_recovery(T0, [1, 3, 4], stale)       # includes leg 1


class TestRecoveryCancellation:
    def test_stale_share_veto_works(self):
        cer, st = _setup(seed=56)
        stale = recovery.derive_stale_share(cer.shares, [5])
        st.initiate_recovery(T0, [2, 3, 4], stale)
        st.cancel_recovery(T0 + 3600, 5, cer.shares[5].value)
        assert st.phase is recovery.Phase.CANCELLED
        assert st.pending is None

    def test_unregistered_share_cannot_veto(self):
        cer, st = _setup(seed=57)
        stale = recovery.derive_stale_share(cer.shares, [5])
        st.initiate_recovery(T0, [2, 3, 4], stale)
        with pytest.raises(PermissionError):
            st.cancel_recovery(T0 + 10, 1, cer.shares[1].value)
        assert st.phase is recovery.Phase.PENDING

    def test_cancel_without_pending(self):
        cer, st = _setup(seed=58)
        with pytest.raises(RuntimeError):
            st.cancel_recovery(T0, 1, cer.shares[1].value)


class TestRecoveryFinalization:
    def test_full_flow_reshare_preserves_pubkey(self):
        cer, st = _setup(seed=59)
        stale = recovery.derive_stale_share(cer.shares, [5])
        st.initiate_recovery(T0, [2, 3, 4], stale)
        for i in (2, 3, 4):
            st.submit_collector_share(T0 + 60, i, cer.shares[i].value)

        with pytest.raises(RuntimeError, match="timelock"):
            st.finalize_recovery(T0 + 71 * 3600)

        new_shares = st.finalize_recovery(T0 + 72 * 3600)
        assert st.phase is recovery.Phase.ACTIVE
        assert st.generation == 1
        assert st.public_key == cer.public_key           # address unchanged
        assert sorted(new_shares) == [1, 2, 3, 4, 5]     # everyone refreshed

        # old shares are now dead against the new commitments
        for i in range(1, 6):
            assert not verify_share(Share(i, cer.shares[i].value),
                                    st.commitments, EDWARDS_BASE, _F)
        # new shares are live and still reconstruct the SAME secret
        for i, v in new_shares.items():
            assert verify_share(Share(i, v), st.commitments,
                                EDWARDS_BASE, _F)
        sec = reconstruct([Share(i, new_shares[i]) for i in (1, 4, 5)], _F)
        assert EDWARDS_BASE.mul(sec) == cer.public_key

    def test_incomplete_collectors_blocked(self):
        cer, st = _setup(seed=60)
        stale = recovery.derive_stale_share(cer.shares, [5])
        st.initiate_recovery(T0, [2, 3, 4], stale)
        st.submit_collector_share(T0 + 60, 2, cer.shares[2].value)
        st.submit_collector_share(T0 + 60, 3, cer.shares[3].value)
        with pytest.raises(RuntimeError, match="never delivered"):
            st.finalize_recovery(T0 + 73 * 3600)
        assert st.phase is recovery.Phase.PENDING       # stays pending

    def test_cheating_collector_rejected(self):
        cer, st = _setup(seed=61)
        stale = recovery.derive_stale_share(cer.shares, [5])
        st.initiate_recovery(T0, [2, 3, 4], stale)
        st.submit_collector_share(T0 + 60, 2, cer.shares[2].value)
        st.submit_collector_share(T0 + 60, 3, cer.shares[3].value)
        st.submit_collector_share(T0 + 60, 4, cer.shares[4].value + 7)
        with pytest.raises(ValueError, match="invalid share"):
            st.finalize_recovery(T0 + 73 * 3600)

    def test_noncollector_submission_rejected(self):
        cer, st = _setup(seed=62)
        stale = recovery.derive_stale_share(cer.shares, [5])
        st.initiate_recovery(T0, [2, 3, 4], stale)
        with pytest.raises(ValueError, match="not a designated collector"):
            st.submit_collector_share(T0 + 60, 1, cer.shares[1].value)

    def test_cooldown_blocks_immediate_second_recovery(self):
        cer, st = _setup(seed=63)
        stale = recovery.derive_stale_share(cer.shares, [5])
        st.initiate_recovery(T0, [2, 3, 4], stale)
        for i in (2, 3, 4):
            st.submit_collector_share(T0 + 60, i, cer.shares[i].value)
        new_shares = st.finalize_recovery(T0 + 72 * 3600)

        later = T0 + 72 * 3600 + 10
        with pytest.raises(RuntimeError, match="cooldown"):
            st.initiate_recovery(later, [1, 2, 3],
                                 {4: new_shares[4]})
        assert st.remaining_cooldown(later) > 0
        # after 30 days it clears
        past = T0 + 72 * 3600 + 31 * recovery.DAYS
        assert st.remaining_cooldown(past) == 0
        st.initiate_recovery(past, [1, 2, 3], {4: new_shares[4]})
        assert st.phase is recovery.Phase.PENDING


class TestScheduledRefresh:
    def test_refresh_rotates_shares_without_cooldown(self):
        cer, st = _setup(seed=64)
        sec = reconstruct([cer.shares[i] for i in (1, 2, 3)], _F)
        blinds = {1: 7, 2: 11, 3: _F.sub(sec, 18)}
        ns = st.scheduled_refresh(T0, sec, blinds)
        assert st.generation == 1
        assert st.last_recovery_unix is None            # no cooldown clock
        for i, v in ns.items():
            assert verify_share(Share(i, v), st.commitments,
                                EDWARDS_BASE, _F)
        assert EDWARDS_BASE.mul(reconstruct(
            [Share(i, ns[i]) for i in (2, 4, 5)], _F)) == cer.public_key

    def test_refresh_rejects_bad_participants(self):
        cer, st = _setup(seed=65)
        sec = reconstruct([cer.shares[i] for i in (1, 2, 3)], _F)
        with pytest.raises(ValueError):
            st.scheduled_refresh(T0, sec, {1: 1, 2: 2})   # below threshold
        with pytest.raises(ValueError):
            st.scheduled_refresh(T0, sec, {1: 1, 2: 2, 9: 3})

    def test_refresh_requires_correct_secret(self):
        cer, st = _setup(seed=66)
        with pytest.raises(ValueError, match="pubkey"):
            st.scheduled_refresh(T0, 42, {1: 1, 2: 2, 3: 39})

    def test_reshare_blinds_must_sum_to_secret(self):
        cer, st = _setup(seed=67)
        with pytest.raises(ValueError, match="sum"):
            st.reshare_with_secret(T0,
                                   reconstruct([cer.shares[i]
                                                for i in (1, 2, 3)], _F),
                                   {1: 5, 2: 5, 3: 5})


class TestRecoveryPolicyValidation:
    def test_policy_checks(self):
        with pytest.raises(ValueError):
            recovery.RecoveryPolicy(quorum=1).validate()
        with pytest.raises(ValueError):
            recovery.RecoveryPolicy(timelock_hours=0).validate()
        with pytest.raises(ValueError):
            recovery.RecoveryPolicy(cooldown_days=0).validate()

    def test_create_state_threshold_mismatch(self):
        cer = frost.run_keygen(_blinds(5, 68), n=5, t=3)
        with pytest.raises(ValueError, match="policy quorum"):
            recovery.create_recovery_state(
                cer.public_key, cer.joint_commitments(), 3,
                {i: "x" for i in range(1, 6)},
                policy=recovery.RecoveryPolicy(quorum=4))
        with pytest.raises(ValueError, match="commitment vector"):
            recovery.create_recovery_state(
                cer.public_key, cer.joint_commitments()[:2], 3,
                {i: "x" for i in range(1, 6)})
