# Internal governance files

These three files bind **AI-assisted development sessions** (and any human acting as
release steward) on the Prism project. They are intentionally kept out of the root and
out of user-facing documentation because they describe *how we work*, not *what Prism is*:

| File | Purpose |
|---|---|
| [`rules.md`](rules.md) | Tool-calling & execution rules: batching, pacing, verifiable status reporting (§4). |
| [`ethics.md`](ethics.md) | Financial-ethics coding & reporting standard: fail-closed money handling, proof-or-UNVERIFIED claims, positive+negative test pairing. |
| [`qwen.md`](qwen.md) | Agent operating constraints: reference map, anti-burst pacing, loop cycle, stop conditions. |

Precedence notes (chain, Oct 13 v1.0 — single authoritative statement; all other files
point here):
1. `spec.md` — product truth.
2. **Integrity contract** (`rules.md` §4 + `ethics.md` §3) — overrides EVERYTHING else,
   including qwen.md and rules.md's own execution limits. No claim may be made without
   observed in-session evidence; "simulated" results are fabrication.
3. `rules.md` §§1–3,5–7 — execution/tool-calling limits.
4. `qwen.md` — pacing NUMBERS win over rules.md numbers (Oct 11 stress tests), but only
   for pacing; qwen.md never outranks the integrity contract.
- New AI sessions bootstrap via **`AGENTS.md`** (repo root, single entry point) which
  defines the load order for everything above plus `docs/HANDOFF.md`. The precedence
  chain below is authoritative; AGENTS.md restates it and must stay in sync.
- House rule against drift: governance files must not restate project status (versions,
  test counts, token capabilities, path names) as facts — those live in HANDOFF.md only,
  and any session quoting them must re-verify first (rules.md §4 "No Carrying Over").
