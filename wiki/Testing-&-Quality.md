# Testing & Quality

*How we know the reference implementation is right — property tests, fuzz harnesses, acceptance suites, and the release gates that block bad builds. Code: `prism/tests/`.*

---

## Philosophy

Consensus and cryptography fail in the weird corners, so testing effort concentrates there: every primitive gets **completeness** (honest things work), **soundness** (dishonest things get rejected), and **malleability** (no two meanings per encoding) coverage; every parser gets a fuzzer; every release gets the whole suite green or doesn't happen. Tests are documentation with teeth — when behavior changes, the test diff tells the story.

## The suite inventory (`prism/tests/`)

| File | Area | What it proves |
|---|---|---|
| `test_crypto.py` | Primitives | Field/curve laws, Pedersen homomorphism, stealth round-trips, CLSAG verify/reject, range-proof completeness/soundness |
| `test_bulletproof_extra.py`, `test_bulletproof_plus.py` | Range proofs | Bit-vector consistency, challenge binding, tamper rejection, wire-format exactness (11×32B) |
| `test_clsag_extra.py` | Ring sigs | Linkability (double-spend detectable), aggregation across multi-input txs, negative cases |
| `test_chain.py`, `test_block_codec.py` | Chain rules | Emission curve monotonicity/cap convergence, difficulty retarget bounds, header codec round-trips, node accept/reject matrix |
| `test_dandelion.py` | Relay policy | Stem/fluff state machine invariants |
| `test_mpc_recovery_acceptance.py`, `test_sharing_refresh.py`, `test_gg20_candidate.py`, `test_frost_candidate.py`, `test_secp256k1_curve.py` | Keys | Threshold correctness, refresh revokes stale shares, ceremony abort/resume safety, recovery timelock + cancellation flows |
| `test_zk_circuits_v1.py`, `test_income_circuit.py`, `test_plonk_verifier.py`, `test_statement_malleability.py` | Disclosures | Each circuit's honest prove→verify, statement-encoding malleability attacks rejected |
| `test_intent_compiler.py`, `test_nl_grammar.py`, `test_agent_perms.py`, `test_fl_dp_accounting.py`, `test_simulator_explain.py` | Wallet/AI | Grammar-validated compile-or-fallback, grant enforcement outside the model, DP noise accounting, simulator determinism |
| `test_l2_channels.py` | Channels | Open/transfer/close/penalty state machine |
| `test_release_engineering.py` | Packaging | Version sync, CLI entry points, frozen-binary expectations |
| `fuzz_blocks.py`, `test_fuzz_parsing.py` | **Fuzzers** | Random/mutated inputs through parsers & serializers — round-trip exactness, no crashes, canonical-form enforcement |

## Running locally

```bash
python -m pytest prism/tests -q                       # everything
python -m pytest prism/tests/test_crypto.py -v        # one area
python prism/tests/fuzz_blocks.py --iterations 100000 # parse fuzzer, extended run
```

Zero setup beyond Python ≥3.10 — stdlib-only code, pytest as sole dev extra. Property tests record their random seeds in failure output; any red run reproduces exactly with the printed seed.

### What a typical property test looks like

```python
# sketch from test_crypto.py lineage
for _ in range(N):
    v = random.randrange(0, 2**64)      # legal values pass
    C, opening = commit(v)
    assert bulletproof_verify(C, bulletproof_prove(C, opening))
bad = tamper(proof)                      # flip bytes → must reject
assert not bulletproof_verify(C, bad)
```

Millisecond-cheap assertions × thousands of randomized instances catch the off-by-one universe that hand-written examples miss. Phase-1 acceptance raises stakes: the RingCT spend-to-self loop must clear ≥10⁶ iterations ([Roadmap](Roadmap.md)).

## Fuzzing discipline

- **Targets:** block/header parsing, transaction serialization, MPC protocol messages, ZK statement encodings — anything crossing a trust boundary.
- **Method:** generation-based mutation with grammar awareness (e.g., flipping `version.vote` to bool/coerced variants must *fail* parse, not silently succeed — the canonical-domain precedent).
- **CI posture:** fast smoke fuzz on every push; extended campaigns nightly and pre-release; findings get regression tests alongside fixes.

## CI & release gates

Pipeline (`.github/workflows/release.yml`):

1. **Every push/PR:** full pytest suite on Linux/macOS/Windows runners (Python 3.10–3.12 matrix). Red = no merge to protected paths without maintainer override + filed issue.
2. **Tagged releases:** suite re-run → sdist/wheel build → PyInstaller binaries per OS → **smoke-test each frozen binary** (`--selftest` exercises crypto + chain validation inside the packaged interpreter) → checksums → prerelease publish. Test failures hard-block artifacts.
3. **Pre-mainnet additions (planned):** continuous fuzzing service, cross-client vector exchange with Rust/C ports, formal-audit remediation tracking.

Quality metrics we publish over time: property-test iteration counts, fuzz campaign durations, mutation scores on consensus modules — numbers, not vibes ([spec §13 security gates](../spec.md)).

## Review standards by blast radius

| Path | Requirement |
|---|---|
| `chain/`, `crypto/`, `mpc/`, `zk/` | 2 approvals + spec cross-check + tests proving completeness/soundness/malleability for touched code |
| `wallet/`, `ai/`, `l2/` | 1 approval + tests; privacy-sensitive copy follows honest-language rules |
| docs/wiki | 1 approval; claims must trace to spec sections or code |

Security findings bypass public review: private disclosure per [Security Model](Security-Model.md) §Bug bounty.

---

**Next:** [Release Process](Release-Process.md) · [Cryptography Deep Dive](Cryptography-Deep-Dive.md) · [Contributing](Contributing.md)
