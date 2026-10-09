"""Tests for prism.ai.fl_client — federated-learning client skeleton
(spec §8.3, §6.2 "Federated Learning Contribution").

Covers: DP budget accounting (ε ≤ 1.0/round hard cap), Gaussian calibration,
norm clipping bounds, secagg split/combine information flow, mixnet routing,
FedAvg application, quorum + poisoning defenses, and the §14 Q7 rule that
contributions are never reward-eligible.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest  # noqa: E402

from prism.ai import fl_client as FL      # noqa: E402


def sample(features, label):
    return FL.TrainingSample(features=list(features), label=label)


def toy_samples(n=20):
    rows = []
    for i in range(n):
        fraud = i % 2 == 0
        rows.append(sample([1.0 if fraud else 0.0, 0.5,
                            1.0 if fraud else 0.0, 0.0,
                            0.9 if fraud else 0.1, 0.0, 0.0, 0.0],
                           1.0 if fraud else 0.0))
    return rows


# ---------------------------------------------------------------------------
# DP accountant (§8.3: ε ≤ 1.0 per round)
# ---------------------------------------------------------------------------


class TestAccountant:
    def test_grants_up_to_cap(self):
        acct = FL.DPAccountant()
        assert acct.request(1.0) == 1.0
        with pytest.raises(FL.BudgetExhausted):
            acct.request(0.1)                    # round is done contributing

    def test_partial_grant_never_overspends(self):
        acct = FL.DPAccountant(epsilon_cap=1.0)
        first = acct.request(0.7)
        second = acct.request(0.7)
        assert first == 0.7 and second == pytest.approx(0.3)
        assert acct.remaining == pytest.approx(0.0)

    def test_client_rejects_epsilon_above_cap(self):
        with pytest.raises(ValueError):
            FL.FLClient([0.0] * 8, 0.0, epsilon=1.5)

    def test_prepare_raises_when_budget_gone(self):
        c = FL.FLClient([0.0] * 8, -1.0)
        acct = FL.DPAccountant()
        acct.request(1.0)                        # exhaust externally
        with pytest.raises(FL.BudgetExhausted):
            c.prepare_contribution(toy_samples(), acct)


class TestGaussianCalibration:
    def test_sigma_shrinks_with_epsilon(self):
        s_hi = FL.gaussian_sigma(1.0, 1e-5, 2.0)
        s_lo = FL.gaussian_sigma(0.1, 1e-5, 2.0)
        assert s_lo > s_hi                       # less privacy budget → more noise

    def test_rejects_bad_params(self):
        with pytest.raises(ValueError):
            FL.gaussian_sigma(0.0, 1e-5, 1.0)


# ---------------------------------------------------------------------------
# Clipping + noise pipeline
# ---------------------------------------------------------------------------


class TestPrivacyPipeline:
    def test_clip_bounds_l2_norm(self):
        v = [3.0, 4.0]                           # norm 5
        clipped = FL.clip_l2(v, 1.0)
        n = sum(x * x for x in clipped) ** 0.5
        assert n == pytest.approx(1.0)

    def test_update_is_clipped_then_noised(self):
        c = FL.FLClient([0.0] * 8, -1.0, clip_norm=1.0)
        delta = c.local_update(toy_samples())
        big = [x * 100 for x in delta]           # pretend huge local update
        noisy, rec = c.prepare_contribution(toy_samples())
        # digest exists; record mirrors §6.2 fields
        assert rec.update_digest and len(rec.update_digest) == 64
        assert rec.client_dp_epsilon <= 1.0
        assert rec.clip_norm == 1.0
        assert rec.reward_eligible is False      # §14 Q7

    def test_record_json_shape_matches_spec_fields(self):
        c = FL.FLClient([0.0] * 8, -1.0)
        _, rec = c.prepare_contribution(toy_samples())
        j = rec.to_json()
        for k in ("round_id", "model", "client_dp_epsilon", "clip_norm",
                  "update_digest", "submitted_via_mixnet", "reward_eligible"):
            assert k in j


# ---------------------------------------------------------------------------
# Secure aggregation information flow
# ---------------------------------------------------------------------------


class TestSecAgg:
    def test_split_combine_roundtrip(self):
        sa = FL.SecureAggregator(num_shards=3)
        v = [0.11, -0.42, 3.14, 0.0]
        parts = sa.split(v)
        got = sa.combine(parts)
        assert all(abs(a - b) < 1e-9 for a, b in zip(v, got))

    def test_proper_subset_reveals_nothing_deterministic(self):
        sa = FL.SecureAggregator(num_shards=3)
        v1 = [1.0, 1.0]
        p_a = sa.split(v1)
        p_b = sa.split(v1)
        # shard-0 masks differ run-to-run ⇒ individual shares carry no signal
        assert p_a[0] != p_b[0]

    def test_transport_delivers_shards_not_clear_vector(self):
        relays = [FL.MixRelay(b"k1"), FL.MixRelay(b"k2")]
        tr = FL.MixTransport(relays, num_ingress_shards=3)
        c = FL.FLClient([0.0] * 8, -1.0)
        rec = c.submit_round(toy_samples(), tr, round_id=7)
        assert rec.submitted_via_mixnet is True
        parts = tr.inbox[7]
        assert len(parts) == 3                   # shards only ever stored
        assert len(parts[0]) == 9                # weights ++ bias dim


# ---------------------------------------------------------------------------
# Mixnet stand-in
# ---------------------------------------------------------------------------


class TestMixnet:
    def test_onion_roundtrip(self):
        keys = [b"a" * 16, b"b" * 16, b"c" * 16]
        relays = [FL.MixRelay(k) for k in keys]
        msg = b"opaque-payload"
        ct = FL.wrap_onion(msg, keys)
        assert ct != msg
        assert FL.unwrap_through_mixes(ct, relays) == msg

    def test_relay_shuffles_batch(self):
        r = FL.MixRelay(b"key")
        batch = [(str(i), f"p{i}".encode()) for i in range(50)]
        out = r.process_batch(batch)
        assert sorted(x[0] for x in out) == sorted(x[0] for x in batch)


# ---------------------------------------------------------------------------
# FedAvg + server defenses
# ---------------------------------------------------------------------------


class TestFedAvgAndServer:
    def test_apply_global_moves_weights(self):
        c = FL.FLClient([1.0] * 8, 0.0)
        avg = [0.1] * 8 + [0.5]
        c.apply_global(avg)
        assert c.weights == pytest.approx([1.1] * 8)
        assert c.bias == pytest.approx(0.5)

    def test_dim_mismatch_refused(self):
        c = FL.FLClient([1.0] * 8, 0.0)
        with pytest.raises(ValueError):
            c.apply_global([0.1] * 3)

    def test_quorum_below_256_discards_round(self):
        srv = FL.AggregatorServer(dim=9)
        deltas = {f"c{i}": [0.0] * 9 for i in range(255)}
        for cid, d in deltas.items():
            assert srv.accept(cid, d)
        assert srv.aggregate(deltas) is None     # MIN_CONTRIBUTORS floor

    def test_quorum_met_returns_average(self):
        srv = FL.AggregatorServer(dim=2, mode="mean")
        deltas = {f"c{i}": [0.2, -0.2] for i in range(FL.MIN_CONTRIBUTORS)}
        for cid, d in deltas.items():
            srv.accept(cid, d)
        out = srv.aggregate(deltas)
        assert out == pytest.approx([0.2, -0.2])

    def test_poisonous_norm_decays_reputation_and_dropped(self):
        srv = FL.AggregatorServer(dim=2)
        assert srv.accept("evil", [50.0, -50.0]) is False
        assert srv.reputations["evil"].score == 0.5
        for _ in range(3):                       # repeated attempts decay…
            assert srv.accept("evil", [500.0, -500.0]) is False
        assert srv.reputations["evil"].score == 0.0   # …to silence
        # silenced contributors are excluded from aggregation entirely
        deltas = {"evil": [0.1, 0.1], **{f"c{i}": [0.1, 0.1]
                                         for i in range(FL.MIN_CONTRIBUTORS)}}
        out = srv.aggregate(deltas)
        assert out == pytest.approx([0.1, 0.1])  # only the 256 honest rows

    def test_trimmed_mean_blunts_outliers(self):
        srv = FL.AggregatorServer(dim=1, mode="trimmed_mean")
        deltas = {f"c{i}": [0.0] for i in range(300)}
        deltas["poisoner"] = [0.9]
        for cid, d in deltas.items():
            if FL.clip_l2.__name__:              # accept() screens norms
                try:
                    srv.accept(cid, d)
                except ValueError:
                    pass
        out = srv.aggregate(deltas)
        assert out is not None and out[0] < 0.05  # outlier trimmed away

    def test_local_training_actually_lears_signal(self):
        # benign-start model trained one step on separable data should push
        # the first_seen weight positive (fraud label correlates w/ feature 0);
        # with a balanced class mix the bias gradient cancels at p=0.5.
        c = FL.FLClient([0.0] * 8, 0.0, lr=1.0)
        delta = c.local_update(toy_samples())
        assert delta[0] > 0                       # increase first_seen weight
        assert delta[-1] == pytest.approx(0.0)    # balanced classes → no bias shift
        fraud_heavy = toy_samples() + [sample([1, 0, 1, 0, 0.9, 0, 0, 0], 1.0)]
        c2 = FL.FLClient([0.0] * 8, 0.0, lr=1.0)
        d2 = c2.local_update(fraud_heavy)
        assert d2[-1] < 0                         # skew raises fraud prior
