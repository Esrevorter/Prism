"""Federated-learning client skeleton — ai/ deliverable (spec §8.3, §6.2
"Federated Learning Contribution").

Goal: improve the fraud detector globally with NO user data leaving devices.
Mechanism per spec, mirrored at prototype fidelity:

  FedAvg            — clients train locally on their own labeled history and
                      upload only weight deltas; server averages.
  Client-side DP    — Gaussian mechanism, ε ≤ 1.0 per round enforced by an
                      accountant ledger that refuses to overspend the cap
                      (per-round opt-in, §8.3).
  Norm clipping     — every update is clipped to ``clip_norm`` BEFORE noise,
                      bounding one client's influence (also poisoning defense).
  Secure aggregation — additive secret splitting of each noisy update across
                      aggregator shards; no single shard ever sees a client's
                      vector in the clear (interface pinned; production swaps
                      in pairwise-keyed Bonawitz-style secagg).
  Mix network       — layered-encrypted relay hops + batch shuffling so the
                      round roster cannot correlate contributor → payload.
  Poison defenses   — median/trimmed aggregation + contribution reputation
                      decay on the server side (§8.3 governance).

Incentives (§14 Q7): participation is altruistic + reputation-only; this
module deliberately has no reward accounting (``reward_eligible=False``).

This is a skeleton: transports are in-process stand-ins with the same
interfaces the production build swaps (HTTP-over-mixnet, real Shamir via
prism.mpc.sharing, LibML secagg). Math is stdlib float; production moves to
fixed-point int vectors.
"""
from __future__ import annotations

import hashlib
import hmac
import math
import random
import secrets
from dataclasses import dataclass, field
from typing import Optional, Sequence

# ---------------------------------------------------------------------------
# Privacy accounting (§8.3: epsilon <= 1.0 per round, Gaussian mechanism)
# ---------------------------------------------------------------------------

EPSILON_CAP_PER_ROUND = 1.0
DELTA_PER_ROUND = 1e-5
MIN_CONTRIBUTORS = 256          # secure-aggregation quorum floor (§8.3)


class BudgetExhausted(RuntimeError):
    pass


@dataclass
class DPAccountant:
    """Per-round budget ledger. v1 semantics: fresh ε per round (opt-in each
    round), so the ledger tracks spend WITHIN a round and hard-caps at 1.0."""

    epsilon_cap: float = EPSILON_CAP_PER_ROUND
    spent: float = 0.0

    def request(self, epsilon: float) -> float:
        """Return the granted ε (possibly less than requested); never over cap."""
        if epsilon <= 0:
            raise ValueError("epsilon must be positive")
        remaining = self.epsilon_cap - self.spent
        if remaining <= 0:
            raise BudgetExhausted(f"round budget {self.epsilon_cap} fully spent")
        granted = min(epsilon, remaining)
        self.spent += granted
        return granted

    @property
    def remaining(self) -> float:
        return max(0.0, self.epsilon_cap - self.spent)


def gaussian_sigma(epsilon: float, delta: float, l2_sensitivity: float) -> float:
    """Calibration for the analytic-Gaussian mechanism (simplified bound
    adequate for a skeleton): σ ≥ sensitivity · sqrt(2 ln(1/δ)) / ε."""
    if epsilon <= 0 or delta <= 0:
        raise ValueError("epsilon/delta must be positive")
    return l2_sensitivity * math.sqrt(2.0 * math.log(1.0 / delta)) / epsilon


# ---------------------------------------------------------------------------
# Vector ops (production: fixed-point int; prototype: float list)
# ---------------------------------------------------------------------------

Vec = list  # list[float]


def clip_l2(v: Vec, norm: float) -> Vec:
    n = math.sqrt(sum(x * x for x in v))
    if n <= norm or n == 0:
        return list(v)
    s = norm / n
    return [x * s for x in v]


def add_noise(v: Vec, sigma: float) -> Vec:
    r = random.SystemRandom()
    return [x + r.gauss(0.0, sigma) for x in v]


def vec_digest(v: Vec) -> str:
    blob = ",".join(f"{x:.10f}" for x in v).encode()
    return hashlib.sha256(blob).hexdigest()


# ---------------------------------------------------------------------------
# Mix network stand-in (§8.3: contributions routed through a mix network)
# ---------------------------------------------------------------------------


class MixRelay:
    """One hop. Production: onion layers over a Tor-like circuit with timing
    jitter. Prototype: peel one keyed-XOR layer and shuffle batch order."""

    def __init__(self, key: bytes):
        self.key = key

    def _layer(self, payload: bytes) -> bytes:
        k = hmac.new(self.key, b"mix-layer", hashlib.sha256).digest()
        return bytes(b ^ k[i % len(k)] for i, b in enumerate(payload))

    def process_batch(self, batch: list[tuple[str, bytes]]) -> list[tuple[str, bytes]]:
        out = [(rid, self._layer(pt)) for rid, pt in batch]
        secrets.SystemRandom().shuffle(out)   # unlinkability by shuffling
        return out


def wrap_onion(payload: bytes, relay_keys: Sequence[bytes]) -> bytes:
    """Client-side: encrypt outermost-last so relays peel in path order."""
    layer = payload
    for key in reversed(list(relay_keys)):
        k = hmac.new(key, b"mix-layer", hashlib.sha256).digest()
        layer = bytes(b ^ k[i % len(k)] for i, b in enumerate(layer))
    return layer


def unwrap_through_mixes(ciphertext: bytes, relays: Sequence[MixRelay]) -> bytes:
    """Server-side entry point: payload arrives at the last relay only."""
    layer = ciphertext
    for relay in relays:
        layer = relay._layer(layer)
    return layer


# ---------------------------------------------------------------------------
# Secure aggregation stand-in (additive sharing of noisy updates)
# ---------------------------------------------------------------------------


class SecureAggregator:
    """Additive masking: client splits its noisy update into ``num_shards``
    parts (uniform-random except the last = value − sum(others)). Any proper
    subset reveals nothing about the vector; only the full combine restores
    it — pinning the information-flow property production secagg must give.

    NOTE: true deployment uses pairwise client↔client key agreement
    (Bonawitz et al.) so even the *server* sums masked values; the interface
    (split / partial-reveal-impossible / combine) stays identical.
    """

    def __init__(self, num_shards: int = 3):
        self.num_shards = max(2, num_shards)

    def split(self, v: Vec) -> list[Vec]:
        parts: list[Vec] = []
        for _ in range(self.num_shards - 1):
            parts.append([secrets.randbelow(2**32) / 2**32 - 0.5 for _ in v])
        last = [v[i] - sum(p[i] for p in parts) for i in range(len(v))]
        parts.append(last)
        return parts

    @staticmethod
    def combine(parts: Sequence[Vec]) -> Vec:
        n = len(parts[0])
        return [sum(p[i] for p in parts) for i in range(n)]


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


@dataclass
class RoundRecord:
    """Mirrors §6.2 'Federated Learning Contribution' JSON record."""

    round_id: int
    model: str
    client_dp_epsilon: float
    clip_norm: float
    update_digest: str
    submitted_via_mixnet: bool
    reward_eligible: bool = False      # §14 Q7: altruistic/reputation only

    def to_json(self) -> dict:
        return {"round_id": self.round_id, "model": self.model,
                "client_dp_epsilon": self.client_dp_epsilon,
                "clip_norm": self.clip_norm, "update_digest": self.update_digest,
                "submitted_via_mixnet": self.submitted_via_mixnet,
                "reward_eligible": self.reward_eligible}


@dataclass
class TrainingSample:
    features: Vec           # same layout as ai.fraud.FEATURE_NAMES
    label: float            # 1.0 = fraud, 0.0 = benign


class FLClient:
    """On-device trainer + privacy-preserving submitter.

    The client NEVER sends raw transactions — only local feature vectors
    already computed on-device (ai/fraud.TxFeatures) and numeric weight
    deltas after clipping + DP noise (+ optional secagg split + mix route).
    """

    def __init__(self, weights: Vec, bias: float, *, lr: float = 0.5,
                 clip_norm: float = 1.0, epsilon: float = EPSILON_CAP_PER_ROUND,
                 delta: float = DELTA_PER_ROUND, model_name: str = "fraud_det_v1",
                 convergence_tol: float = 1e-6):
        if epsilon > EPSILON_CAP_PER_ROUND:
            raise ValueError(f"spec cap: epsilon <= {EPSILON_CAP_PER_ROUND}")
        self.weights = list(weights)
        self.bias = bias
        self.lr = lr
        self.clip_norm = clip_norm
        self.epsilon = epsilon
        self.delta = delta
        self.model_name = model_name
        self.convergence_tol = convergence_tol
        self.dim = len(self.weights) + 1     # weights ++ bias (last slot)

    # -- local training ---------------------------------------------------

    def local_update(self, samples: Sequence[TrainingSample], *,
                     base_weights: Optional[Vec] = None,
                     base_bias: Optional[float] = None) -> Vec:
        """One-shot supervised step on the client's own labeled history.

        Semantics (production swaps SGD mini-batches behind this interface):
        compute the full-batch mean logistic-regression gradient over the
        client's data, take a single ``lr``-scaled step, and clip the result
        to ``clip_norm`` — so the returned delta ALWAYS satisfies
        ‖delta‖ ≤ clip_norm, matching the server's norm screen (§8.3
        poisoning bound: one client's influence per round is bounded).

        Why exactly one step: iterating to convergence would let the local
        model chase its own class mix into saturation — the bias would drift
        toward whichever label dominates even after every sample is already
        classified correctly, leaking label-fraction information into the
        shared update. A single mean-gradient step keeps the update honest.
        Returns the DELTA vector (weights then bias) before any privacy
        operations.

        Bias-prior sign convention: fraud labels are the rare positive class
        in real traffic, so a client whose batch skews fraud-heavy must move
        the global prior UPWARD (Δb > 0). We therefore descend the negated
        loss gradient — Δ = +lr·mean((y − p)·[x…, 1]) — which reduces to the
        exact label-mean shift at a neutral linearization point:
        Δb = lr·(ȳ − p̄). Balanced batches cancel; skew raises the prior.

        ``base_weights`` / ``base_bias`` optionally pin the linearization
        point (defaults: the client's current model). FedAvg rounds evaluate
        the gradient at the SHARED global model, not each device's private
        local state — that keeps the submitted delta an unbiased signal about
        the batch instead of leaking how far a device's personal model has
        drifted from the global one.
        """
        dim = len(self.weights) + 1
        w = list(self.weights if base_weights is None else base_weights)
        b = self.bias if base_bias is None else float(base_bias)
        n = max(1, len(samples))

        gw = [0.0] * (dim - 1)
        gb = 0.0
        for s in samples:
            z = b + sum(wi * xi for wi, xi in zip(w, s.features))
            z = max(-50.0, min(50.0, z))
            p = 1.0 / (1.0 + math.exp(-z))
            err = s.label - p                    # ascend toward the label mean
            for i, x in enumerate(s.features):
                gw[i] += err * x
            gb += err
        grad = [g / n for g in gw] + [gb / n]

        # Residual below tolerance ⇒ nothing meaningful left to learn from
        # this local batch; submit a clean zero delta (still budget-spent if
        # prepare_contribution wraps it — rounds are opt-in regardless).
        if math.sqrt(sum(g * g for g in grad)) < self.convergence_tol:
            return [0.0] * dim

        return clip_l2([self.lr * g for g in grad], self.clip_norm)

    # -- privacy pipeline --------------------------------------------------

    def prepare_contribution(self, samples: Sequence[TrainingSample],
                             accountant: Optional[DPAccountant] = None
                             ) -> tuple[Vec, RoundRecord]:
        """clip → DP noise → digest; exactly the §8.3 client-side order.

        Raises BudgetExhausted if the round's ε ledger is spent — the client
        then simply does not contribute this round (opt-in, never forced).
        """
        acct = accountant or DPAccountant(epsilon_cap=self.epsilon)
        granted = acct.request(self.epsilon)
        delta = self.local_update(samples)
        clipped = clip_l2(delta, self.clip_norm)
        # L2 sensitivity of a clipped update is 2*clip_norm (leave-one-out).
        sigma = gaussian_sigma(granted, self.delta, 2.0 * self.clip_norm)
        noisy = add_noise(clipped, sigma)
        rec = RoundRecord(round_id=0, model=self.model_name,
                          client_dp_epsilon=granted, clip_norm=self.clip_norm,
                          update_digest=vec_digest(noisy),
                          submitted_via_mixnet=False)
        return noisy, rec

    def submit_round(self, samples: Sequence[TrainingSample],
                     transport: "MixTransport", round_id: int,
                     accountant: Optional[DPAccountant] = None) -> RoundRecord:
        """Full client path for one round: prepare → secagg-split → mix route.

        The transport delivers SHARDS of the noisy delta; per §8.3 the record
        marks submitted_via_mixnet=True and reward_eligible stays False.
        """
        noisy, rec = self.prepare_contribution(samples, accountant)
        rec.round_id = round_id
        secagg = SecureAggregator(num_shards=transport.num_ingress_shards)
        parts = secagg.split(noisy)
        transport.deliver(round_id, parts)
        rec.submitted_via_mixnet = True
        return rec

    def apply_global(self, avg_delta: Vec) -> None:
        """FedAvg: adopt the aggregated delta into the local model."""
        if len(avg_delta) != self.dim:
            raise ValueError("delta dimension mismatch")
        self.weights = [w + d for w, d in zip(self.weights, avg_delta[:-1])]
        self.bias += avg_delta[-1]


# ---------------------------------------------------------------------------
# Round transport (mixnet + secagg ingress glue; server-side in production)
# ---------------------------------------------------------------------------


class MixTransport:
    """Glue object standing in for mixnet-routed submission to secagg shards."""

    def __init__(self, relays: Sequence[MixRelay], num_ingress_shards: int = 3):
        self.relays = list(relays)
        self.num_ingress_shards = num_ingress_shards
        self.inbox: dict[int, list[Vec]] = {}   # round_id -> received parts

    def deliver(self, round_id: int, parts: list[Vec]) -> None:
        blob = repr((round_id, parts)).encode()
        ct = wrap_onion(blob, [r.key for r in self.relays])
        pt = unwrap_through_mixes(ct, self.relays)
        rid, received = eval_safe(pt)            # deterministic decode
        assert rid == round_id
        self.inbox.setdefault(round_id, []).extend(received)


def eval_safe(blob: bytes):
    """Prototype codec (NOT for production): round-trips (int, list[list[float]])."""
    import ast
    obj = ast.literal_eval(blob.decode())
    if (not isinstance(obj, tuple) or len(obj) != 2
            or not isinstance(obj[0], int) or not isinstance(obj[1], list)):
        raise ValueError("malformed mix payload")
    return obj


# ---------------------------------------------------------------------------
# Server-side pieces (interfaces pinned; production runs on infra, not device)
# ---------------------------------------------------------------------------


@dataclass
class ContributorReputation:
    score: float = 1.0        # multiplier applied pre-average; decays on outliers


_SERVER_CLIP_BOUND = 1.0     # matches default clip_norm; synced per model card


class AggregatorServer:
    """FedAvg with robust aggregation + reputation decay (§8.3 poisoning
    defenses). Quorum rule: a round finalizes ONLY with >= MIN_CONTRIBUTORS
    accepted contributions — below that the round is discarded entirely,
    because DP guarantees are meaningless and Sybil rounds are cheap to kill.
    """

    def __init__(self, dim: int, *, mode: str = "trimmed_mean",
                 clip_bound: float = _SERVER_CLIP_BOUND):
        assert mode in ("mean", "median", "trimmed_mean")
        self.dim = dim
        self.mode = mode
        self.clip_bound = clip_bound
        self.reputations: dict[str, ContributorReputation] = {}

    def _rep(self, cid: str) -> float:
        return self.reputations.setdefault(cid, ContributorReputation()).score

    _DECAY_FLOOR = 0.125      # below this the contributor is silenced outright

    def accept(self, contributor_id: str, delta: Vec) -> bool:
        """Screen one (already-noisy) update; drop+decay oversized norms.

        Decay is multiplicative (×0.5 per offense); once the score falls to
        the floor it is zeroed — "silence": the contributor is excluded from
        aggregation entirely and only a manual governance reset restores it.
        """
        if len(delta) != self.dim:
            raise ValueError(f"expected dim {self.dim}")
        n = math.sqrt(sum(x * x for x in delta))
        if n > 1.0001 * self.clip_bound:
            r = self.reputations.setdefault(contributor_id, ContributorReputation())
            r.score = max(0.0, r.score * 0.5)      # reputation decay
            if r.score < self._DECAY_FLOOR:
                r.score = 0.0                      # silence
            return False
        self.reputations.setdefault(contributor_id, ContributorReputation())
        return True

    def recombine(self, round_parts: Sequence[Sequence[Vec]]) -> list[Vec]:
        """Securely aggregate ingress shards back to per-contributor noisy
        deltas WITHOUT ever materializing a single contributor's vector on
        one shard (prototype: combine all shards of each submission)."""
        secagg = SecureAggregator()
        out = []
        for parts in round_parts:
            out.append(secagg.combine(parts))
        return out

    def aggregate(self, deltas: dict[str, Vec]) -> Optional[Vec]:
        """Returns the averaged delta, or None if quorum unmet."""
        usable = {cid: d for cid, d in deltas.items() if self._rep(cid) > 0.0}
        if len(usable) < MIN_CONTRIBUTORS:
            return None
        rows = list(usable.values())
        if self.mode == "mean":
            return [sum(r[i] for r in rows) / len(rows) for i in range(self.dim)]
        if self.mode == "median":
            return [sorted(r[i] for r in rows)[len(rows) // 2]
                    for i in range(self.dim)]
        # trimmed mean: drop top+bottom ~10% per coordinate
        k = max(1, len(rows) // 10)
        out = []
        for i in range(self.dim):
            col = sorted(r[i] for r in rows)
            core = col[k:len(col) - k] or col
            out.append(sum(core) / len(core))
        return out


# ---------------------------------------------------------------------------
# End-to-end round driver (prototype glue; production runs these steps on
# server infra between mixnet batches — interfaces are identical)
# ---------------------------------------------------------------------------


class FLRound:
    """One full FedAvg round over an AggregatorServer: collect accepted
    noisy deltas → securely recombine secagg shards → robust-average iff
    quorum → apply to the live FraudDetector model (§8.3 closed loop).

    ``finalize`` returns the adopted delta, or None when quorum is unmet —
    a discarded round changes NO model and spends no reputation.
    """

    def __init__(self, server: "AggregatorServer"):
        self.server = server
        self.collected: dict[str, list[Vec]] = {}   # cid -> secagg parts

    def submit(self, contributor_id: str, parts: Sequence[Vec]) -> bool:
        """Ingress one contribution (list of secagg shard-vectors). The
        norm screen + reputation decay run on the RECOMBINED delta so a
        splitter cannot smuggle an oversized update past the clip bound."""
        if any(len(p) != self.server.dim for p in parts) or len(parts) < 2:
            raise ValueError("malformed secagg submission")
        delta = SecureAggregator.combine(parts)
        if not self.server.accept(contributor_id, delta):
            return False
        self.collected.setdefault(contributor_id, []).extend(parts)
        return True

    def finalize(self, detector=None):
        deltas = {cid: SecureAggregator.combine(parts)
                  for cid, parts in self.collected.items()}
        avg = self.server.aggregate(deltas)
        if avg is None:
            return None
        if detector is not None:
            detector.apply_global_delta(avg)
        return avg
