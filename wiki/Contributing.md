# Contributing

*From "I read the wiki" to "merged." Prism is MIT-licensed, pre-audit reference code — which makes careful contribution the highest-value thing an outsider can do right now.*

---

## First steps

1. Read [Prism in Plain English](Prism-in-Plain-English.md) (context), then [`spec.md`](../spec.md) §4–§6 (the rules). Where prose and spec disagree, **spec wins** — if you think the spec is wrong, that's an RFC conversation ([Governance](Governance.md)).
2. Set up per [Getting Started as a Developer](Getting-Started-as-a-Developer.md): clone → `python -m pytest prism/tests -q` → green on your machine.
3. Pick something: issues labeled `good-first-issue`, docs gaps (this wiki welcomes fixes), test coverage for any module, or the current frontier — RingCT tx-layer wiring, daemon netcode, L2 channel logic ([Roadmap](Roadmap.md)).

Questions? Open a discussion or tag `question` on an issue; ambiguity in our docs is a bug report about our docs.

## Workflow

```bash
git switch -c feat/crypto/ring-sampling   # branch: type/area/summary
# … small, focused commits; message style: "area: imperative summary"
git push origin feat/crypto/ring-sampling # then open a PR against main
```

PR requirements:

- **Tests accompany behavior changes.** Crypto/chain paths need completeness + soundness + malleability cases ([Testing & Quality](Testing-&-Quality.md)); "I'll add tests later" PRs don't merge.
- **Consensus-affecting diffs cite the spec** (§ number / decision ID) in the description; anything changing monetary or privacy rules additionally needs an accepted RFC.
- **CI green:** suite across OS/Python matrix, fuzz smoke passing.
- **Reviewers by blast radius:** `chain/ crypto/ mpc/ zk/` → two approvals + spec cross-check; `wallet/ ai/ l2/` → one; docs → one fast review.
- **Honest-language rule for user-facing copy:** never "revoked disclosure" (only *expired*), never "untraceable," never recovery guarantees beyond what cryptography provides ([FAQ](FAQ.md), [Security Model](Security-Model.md)). Marketing-adjacent wording gets held to the same bar as code here.

## House style (short version)

- Python ≥3.10, stdlib-only inside `prism/`; no new dependencies without an RFC-level justification (consensus code especially).
- Canonical encodings are sacred: if two semantically different objects could serialize identically, fix the type, not the symptom.
- Pure functions wherever possible (`emission_at(height)` takes height — full stop).
- Docstrings name the construction and its paper lineage; reviewers shouldn't reverse-engineer intent from code.
- Performance work lands *after* clarity: this is the reference implementation; speed belongs to the Rust/C ports (help wanted there too — test vectors shared).

## Contribution areas that matter most right now

| Area | State | How to help |
|---|---|---|
| RingCT transaction layer | primitives ✅, wiring 🚧 | tx build/sign/verify integration into `ChainState`, spend-loop property tests |
| Daemon & Refraction testnet | next | P2P sync harnesses, Dandelion++ simulation, node ops feedback |
| ZK circuits | reference-grade | constraint-level reviews, statement-encoding attack ideas, circuit test expansion |
| MPC protocols | logic ✅ | ceremony edge cases, abort/resume fuzzing |
| Wallet UX specs & copy | planning | accessibility review of flows, plain-language drafts under honest-language rules |
| Documentation | ongoing | translations, tutorials, wiki corrections (start [here](Home.md)) |

## Code of conduct & credit

Be technical, be kind, assume good faith; harassment or gatekeeping ends participation. Contributions are credited via commit history; significant design contributions get named in release notes. The dev-fund model ([Governance](Governance.md)) exists so sustained contributors can eventually be funded transparently — milestone proposals follow the RFC process.

## Security issues

Do **not** open public issues for vulnerabilities. Private report per [Security Model](Security-Model.md) §Bug bounty (ack ≤72 h, coordinated disclosure ≤90 days).

---

**Next:** [Architecture Overview](Architecture.md) · [Release Process](Release-Process.md) · [Wiki Home](Home.md)
