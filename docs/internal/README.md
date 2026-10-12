# Internal governance files

These three files bind **AI-assisted development sessions** (and any human acting as
release steward) on the Prism project. They are intentionally kept out of the root and
out of user-facing documentation because they describe *how we work*, not *what Prism is*:

| File | Purpose |
|---|---|
| [`rules.md`](rules.md) | Tool-calling & execution rules: batching, pacing, verifiable status reporting (§4). |
| [`ethics.md`](ethics.md) | Financial-ethics coding & reporting standard: fail-closed money handling, proof-or-UNVERIFIED claims, positive+negative test pairing. |
| [`qwen.md`](qwen.md) | Agent operating constraints: reference map, anti-burst pacing, loop cycle, stop conditions. |

Precedence notes:
- If `qwen.md` and `rules.md` disagree on pacing numbers, **qwen.md wins**.
- `rules.md` §4 + `ethics.md` §3 together form the binding verifiable-status-reporting contract.
- All three complement [`spec.md`](../../spec.md); the spec remains the single source of truth for product requirements.

New AI sessions should load these files (plus `docs/HANDOFF.md`) before doing substantive work.
