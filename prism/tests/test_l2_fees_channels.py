"""Phase 3 L2 + fee-policy tests — spec §10.1/§10.2, E3 (payment channels,
watchtower beta) and fee predictability hardening (priority cap enforcement,
block-packing policy).

Run: python -m pytest prism/tests/test_l2_fees_channels.py -q
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from prism.chain.params import BASE_FEE_SHARDS, PRIORITY_FEE_MAX_MULTIPLE
from prism.l2.fees import (BASE_FEE_CLEAR_BLOCKS_MAX, FeePolicyError, TxView,
                           is_standard_fee, pack_block, priority_density,
                           priority_multiple, quote, validate_fee)
from prism.l2.channels import (Channel, ChannelError, KeyPair, PenaltyBlob,
                               SignedState, State, Watchtower, channel_pay,
                               make_state, open_channel, penalty_outcome,
                               seal_blob, unseal_blob)
from prism.crypto.edwards import Point


# ================================================================ fees ====

class TestFeeLattice(unittest.TestCase):
    def test_base_fee_is_fixed_constant(self):
        self.assertEqual(BASE_FEE_SHARDS, 10_000)          # 0.0001 PRSM
        self.assertTrue(is_standard_fee(BASE_FEE_SHARDS))

    def test_all_multiples_up_to_cap_accepted(self):
        for n in range(1, PRIORITY_FEE_MAX_MULTIPLE + 1):
            fee = n * BASE_FEE_SHARDS
            self.assertEqual(validate_fee(fee), fee)
            self.assertEqual(priority_multiple(fee), n)

    def test_above_cap_rejected_nonstandard(self):
        with self.assertRaises(FeePolicyError):
            validate_fee((PRIORITY_FEE_MAX_MULTIPLE + 1) * BASE_FEE_SHARDS)

    def test_off_lattice_fee_rejected(self):
        for bad in (9_999, 10_500, 1, 0, -10_000):
            with self.assertRaises(FeePolicyError):
                validate_fee(bad)
            self.assertFalse(is_standard_fee(bad))

    def test_quotes_deterministic_and_bounded(self):
        seen = {t: quote(t).total_shard for t in
                ("base_only", "priority_low", "priority_max")}
        self.assertEqual(seen["base_only"], BASE_FEE_SHARDS)
        self.assertEqual(seen["priority_low"], 2 * BASE_FEE_SHARDS)
        self.assertEqual(seen["priority_max"], PRIORITY_FEE_MAX_MULTIPLE
                         * BASE_FEE_SHARDS)
        # repeat calls identical (no estimation jitter — principle 5)
        self.assertEqual(quote("priority_low").total_shard,
                         quote("priority_low").total_shard)
        with self.assertRaises(FeePolicyError):
            quote("priority_highest_ever")

    def test_quote_multiple_never_exceeds_cap(self):
        for t in ("base_only", "priority_low", "priority_max"):
            self.assertLessEqual(quote(t).multiple, PRIORITY_FEE_MAX_MULTIPLE)


class TestBlockPacking(unittest.TestCase):
    def _txs(self):
        return [
            TxView("base1", 100, BASE_FEE_SHARDS),
            TxView("base2", 100, BASE_FEE_SHARDS),
            TxView("prio5", 100, 5 * BASE_FEE_SHARDS),
            TxView("prio10", 100, 10 * BASE_FEE_SHARDS),
        ]

    def test_packs_by_priority_density(self):
        inc, deferred = pack_block(self._txs(), block_size_bytes=250)
        ids = [t.tx_id for t in inc]
        self.assertEqual(ids[:2], ["prio10", "prio5"])   # density order
        self.assertEqual(len(inc), 2)                    # 2x100 fits in 250
        self.assertEqual({t.tx_id for t in deferred}, {"base1", "base2"})

    def test_deterministic_tiebreak(self):
        a, _ = pack_block(self._txs(), block_size_bytes=400)
        b, _ = pack_block(list(reversed(self._txs())), block_size_bytes=400)
        self.assertEqual([t.tx_id for t in a], [t.tx_id for t in b])

    def test_nonstandard_fee_never_packed(self):
        smuggle = TxView("evil", 50, 12 * BASE_FEE_SHARDS)
        inc, deferred = pack_block([smuggle], block_size_bytes=10_000)
        self.assertEqual(inc, [])
        self.assertIn(smuggle, deferred)

    def test_base_fee_clears_within_two_blocks_normal_load(self):
        """§10.1 acceptance: at normal load (one burst of prio txs then a
        base-fee tx), the base tx lands within BASE_FEE_CLEAR_BLOCKS_MAX."""
        burst = [TxView(f"p{i}", 100, 10 * BASE_FEE_SHARDS) for i in range(8)]
        base = TxView("me", 100, BASE_FEE_SHARDS)
        blk_size = 500                       # 5 txs per block
        _, deferred = pack_block(burst, block_size_bytes=blk_size)
        inc2, def2 = pack_block(deferred + [base], block_size_bytes=blk_size)
        blocks_to_clear = 2 if base in inc2 else None
        self.assertIsNotNone(blocks_to_clear, "base tx starved past block 2")
        self.assertLessEqual(blocks_to_clear, BASE_FEE_CLEAR_BLOCKS_MAX)

    def test_density_positive_validation(self):
        with self.assertRaises(ValueError):
            priority_density(TxView("z", 0, BASE_FEE_SHARDS))
        with self.assertRaises(ValueError):
            pack_block([], block_size_bytes=0)


# ============================================================ channels ====

class TestChannelLifecycle(unittest.TestCase):
    def setUp(self):
        self.a, self.b = KeyPair.generate(), KeyPair.generate()

    def test_open_costs_one_base_fee_and_state_verifies(self):
        ch, states = open_channel(1_000_000, self.a, self.b)
        v0 = ch.history[-1]
        self.assertEqual(ch.state, State.OPEN)
        self.assertEqual(ch.open_fee_paid, BASE_FEE_SHARDS)
        self.assertTrue(v0.verify_against(self.a.public, self.b.public))

    def test_in_channel_payments_zero_fee_instant(self):
        ch, _ = open_channel(1_000_000, self.a, self.b)
        st = channel_pay(ch, self.a, self.b, 250_000, from_a=True)
        self.assertEqual((st.bal_a, st.bal_b), (250_000, 750_000))
        self.assertEqual(st.version, 2)
        self.assertTrue(st.verify_against(self.a.public, self.b.public))
        # no chain cost accrued for the payment itself
        self.assertEqual(ch.total_chain_cost_shard(), BASE_FEE_SHARDS)

    def test_lifetime_chain_cost_is_two_base_fees(self):
        """§10.2: 'Channel open/close cost = 2 base-fee transactions'."""
        ch, _ = open_channel(500_000, self.a, self.b)
        channel_pay(ch, self.a, self.b, 100_000, from_a=True)
        out = ch.cooperative_close()
        self.assertEqual(ch.total_chain_cost_shard(), 2 * BASE_FEE_SHARDS)
        self.assertEqual(out["settled_a"] + out["settled_b"], 500_000)

    def test_overdraft_and_bad_transitions_refused(self):
        ch, _ = open_channel(100_000, self.a, self.b)
        with self.assertRaises(ChannelError):
            ch.pay(100_001, from_a=True)
        with self.assertRaises(ChannelError):
            ch.pay(-5, from_a=True)
        ch.cooperative_close()
        with self.assertRaises(ChannelError):
            channel_pay(ch, self.a, self.b, 1, from_a=True)
        with self.assertRaises(ChannelError):
            ch.fund(1, 99_999)

    def test_half_signed_state_not_finalized(self):
        ch, _ = open_channel(100_000, self.a, self.b)
        ch.pay(10_000, from_a=True)
        half = ch.sign_for("a", self.a)
        self.assertFalse(half.sig_b)          # not yet jointly signed
        full = ch.sign_for("b", self.b)
        self.assertTrue(full.sig_a and full.sig_b)
        self.assertTrue(full.verify_against(self.a.public, self.b.public))


class TestCheatingCloseAndWatchtower(unittest.TestCase):
    def setUp(self):
        self.a, self.b = KeyPair.generate(), KeyPair.generate()
        self.secret = b"k" * 32

    def test_stale_broadcast_triggers_penalize_action(self):
        ch, _ = open_channel(1_000_000, self.a, self.b)
        latest = channel_pay(ch, self.a, self.b, 300_000, from_a=True)
        wt = Watchtower()
        wt.register(seal_blob(self.secret, latest))
        honest = wt.on_close_seen(_tag(self.secret, ch), latest.version)
        self.assertEqual(honest["action"], "none")
        cheat = wt.on_close_seen(_tag(self.secret, ch), 1)
        self.assertEqual(cheat["action"], "penalize")
        self.assertEqual(cheat["with_version"], latest.version)

    def test_penalty_sweeps_full_balance_to_victim(self):
        ch, _ = open_channel(1_000_000, self.a, self.b)
        truth = channel_pay(ch, self.a, self.b, 400_000, from_a=True)
        cheat = SignedState(ch.channel_id, 1, 500_000, 500_000, b"", b"")
        po = penalty_outcome(truth, cheat)
        self.assertEqual(po["winner"], "b")
        self.assertEqual(po["credit_b"], 1_000_000)
        self.assertEqual(po["credit_a"], 0)
        # mirrored case: B cheats
        cheat2 = SignedState(ch.channel_id, 1, 500_000, 600_000, b"", b"")
        # cheat2 claims more for B than truth? truth=(100k? ) compute:
        # after 400k A->B: bal_a=100_000, bal_b=900_000; cheat2.b=600k < 900k
        # so A is the one claiming more relative to truth -> winner a
        po2 = penalty_outcome(truth, cheat2)
        self.assertEqual(po2["winner"], "a")

    def test_evidence_pair_must_be_stale(self):
        s_new = SignedState("c", 5, 1, 1, b"", b"")
        s_old = SignedState("c", 4, 1, 1, b"", b"")
        with self.assertRaises(ChannelError):
            penalty_outcome(s_old, s_new)

    def test_blob_roundtrip_confidential_and_tamper_evident(self):
        ch, _ = open_channel(1_000_000, self.a, self.b)
        state = ch.history[-1]
        blob = seal_blob(self.secret, state)
        # tower-side view: ciphertext reveals nothing about balances
        self.assertNotIn(b'"a"', blob.ciphertext)
        rt = unseal_blob(self.secret, blob)
        self.assertEqual(rt.payload, state.payload)
        self.assertTrue(rt.verify_against(self.a.public, self.b.public))
        tampered = PenaltyBlob(blob.channel_tag, blob.version,
                               bytes([blob.ciphertext[0] ^ 1])
                               + blob.ciphertext[1:], blob.mac)
        with self.assertRaises(ChannelError):
            unseal_blob(self.secret, tampered)
        with self.assertRaises(ChannelError):
            unseal_blob(b"wrong-secret" * 3, blob)

    def test_wrong_secret_cannot_unseal(self):
        ch, _ = open_channel(10_000, self.a, self.b)
        blob = seal_blob(self.secret, ch.history[-1])
        with self.assertRaises(ChannelError):
            unseal_blob(b"n" * 32, blob)

    def test_unknown_channel_ignored(self):
        wt = Watchtower()
        self.assertEqual(wt.on_close_seen(b"\x00" * 16, 3)["action"], "ignore")


def _tag(secret: bytes, ch: Channel) -> bytes:
    # must mirror channels.seal_blob's index derivation exactly
    from prism.crypto.hashing import keccak_256
    import hashlib
    return hashlib.sha256(b"|".join([b"tag", secret,
                                     ch.channel_id.encode()]))[:16]


# --------------------------------------------------- schnorr sanity -------

class TestChannelSchnorr(unittest.TestCase):
    def test_signature_scheme_sound(self):
        kp = KeyPair.generate()
        msg = make_state("c1", 1, 5, 5)
        sig = kp.sign(msg)
        self.assertTrue(KeyPair.verify(kp.public, msg, sig))
        self.assertFalse(KeyPair.verify(kp.public, b"tampered", sig))
        other = KeyPair.generate()
        self.assertFalse(KeyPair.verify(other.public, msg, sig))


if __name__ == "__main__":
    unittest.main(verbosity=2)
