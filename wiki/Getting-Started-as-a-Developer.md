# Getting Started as a Developer

*Set up the codebase, run the tests, and find your way around the contribution workflow. For design rationale read [Architecture](https://github.com/Esrevorter/Prism/wiki/Architecture) and [`spec.md`](https://github.com/Esrevorter/Prism/wiki/spec).*

---

## Prerequisites

- **Python ≥ 3.10** (that's it — the core has zero third-party runtime dependencies; `pytest` is the only dev extra).
- Git. Optional: PyInstaller if you want to build local binaries.

## Clone & run

```bash
git clone https://github.com/Esrevorter/Prism.git
cd prism

# Run the test suite directly from the repo layout:
python -m pytest prism/tests -q

# Or install the package + CLI into a venv:
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .                                     # provides the `prism` command
prism params | head -20
prism mine --blocks 3
```

The tests import packages relative to `prism/` (each subpackage — `chain`, `crypto`, `auth`, … — is imported top-level); editable installs put them on the path via `[tool.setuptools.packages.find] where=["prism"]`.

## Repository layout

```text
prism/
├── crypto/     Ed25519 field/group, hashing, Pedersen, stealth, CLSAG, Bulletproofs+
├── chain/      params · emission · difficulty · pow · block (canonical codec)
│               denylist · node (ChainState validation) · cli (`prism` command)
├── auth/       keystore.py (sealed key, phrase restore) · captcha.py (presence gates)
├── mpc/        LEGACY (retired by A2, unwired): secp256k1 · Shamir · GG20 · FROST · recovery
├── zk/         BN scalar field · PLONK-style IOP · disclosure circuits
├── wallet/     intent schema/compiler · agent grants & audit log
├── ai/         fraud scoring · deterministic simulator · FL client
├── l2/         payment channels (Phase 3 scaffold)
└── tests/      property tests, fuzz harnesses, acceptance suites (pytest)
packaging/      PyInstaller spec + release build script
.github/        CI: test gate + tagged release pipeline
spec.md         THE source of truth — decisions D1–D5 are binding
```

## Reading order for new contributors

1. [`spec.md` §4–§6](https://github.com/Esrevorter/Prism/wiki/spec) — money rules, privacy model, data formats.
2. `prism/crypto/field.py → edwards.py → pedersen.py` — the arithmetic floor everything stands on.
3. `prism/chain/block.py → node.py` — canonical encoding + validation rules (where most chain-split bugs would live).
4. Pick your layer: RingCT (`clsag.py`, `bulletproof.py`), custody (`auth/`), disclosures (`zk/`), UX logic (`wallet/`, `ai/`), shipped wallet (`gui/`, `rpc/`).

Each module's docstring states its construction and paper-lineage; tests double as executable documentation (`prism/tests/test_crypto.py` shows every primitive's intended behavior including negative cases).

## Coding conventions

- **Stdlib only** in anything under `prism/` that touches consensus or crypto. Dev/test tooling may use pytest.
- Pure functions for consensus logic (`emission_at(height)` takes height, returns shards — no clocks, no globals).
- Canonical encodings everywhere: if two objects can serialize identically while differing semantically, that's a bug to fix at the type level (see the `version_vote ∈ {0,1}` precedent).
- Every new cryptographic routine ships with completeness + soundness + malleability tests before merge.
- Docstrings cite the construction (paper/section) — reviewers must not have to reverse-engineer intent.

## Testing expectations

```bash
python -m pytest prism/tests -q                    # full suite
python -m pytest prism/tests/test_chain.py -q      # one area
python -m pytest prism/tests/test_fuzz_parsing.py  # parse/serialize round-trip fuzzers
```

CI runs the suite on every push and **blocks releases on failures** ([Testing & Quality](https://github.com/Esrevorter/Prism/wiki/Testing-&-Quality)). Property-based tests use randomized inputs with fixed seeds recorded in failure output so any flake reproduces exactly.

## Build a local binary (optional)

```bash
pip install pyinstaller
./packaging/build_release.sh        # smoke-tests the frozen binary, zips it with SHA256SUMS
```

Windows `.exe`s are produced by CI on native runners — see [Release Process](https://github.com/Esrevorter/Prism/wiki/Release-Process).

## Contribution workflow

1. Branch from `main`: `feat/<area>/<summary>` or `fix/<area>/<issue>`.
2. Keep commits scoped; consensus-affecting changes require a spec reference (§ number or decision ID) in the PR description.
3. Tests accompany behavior changes (non-negotiable for crypto/chain paths).
4. One approval minimum for tooling/docs; **two approvals + spec cross-check for `chain/`, `crypto/`, `auth/`, `zk/`**.
5. New user-facing copy touching privacy/recovery claims must match the honest-language rules ([AI Layer](https://github.com/Esrevorter/Prism/wiki/AI-Layer-Explained), [Security Model](https://github.com/Esrevorter/Prism/wiki/Security-Model)) — e.g., never "revoked disclosure," always "expired disclosure."

Good first issues carry the `good-first-issue` label; the current frontier is the RingCT transaction layer wiring, daemon networking for Refraction testnet, and L2 channel logic ([Roadmap](https://github.com/Esrevorter/Prism/wiki/Roadmap)).

## Release engineering (maintainers)

Tag → CI builds sdist/wheel + per-OS frozen binaries (windows-latest x64, ubuntu-22.04, macOS arm64) → publishes GitHub prerelease with checksums:

```bash
git tag -a v0.1.0 -m "Phase 1 reference implementation" && git push origin v0.1.0
```

Details and signing plans: [Release Process](https://github.com/Esrevorter/Prism/wiki/Release-Process).

---

**Next:** [Architecture Overview](https://github.com/Esrevorter/Prism/wiki/Architecture) · [Cryptography Deep Dive](https://github.com/Esrevorter/Prism/wiki/Cryptography-Deep-Dive) · [Contributing](https://github.com/Esrevorter/Prism/wiki/Contributing)
