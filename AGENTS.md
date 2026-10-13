# Prism — Agent Session Bootstrap (single entry point)

> **Purpose:** this is the ONLY file an AI agent needs to be pointed at when a new
> session starts. It defines the load order for all governance docs, so nothing has
> to be pasted ad hoc and no precedence ambiguity survives. If this file and any
> other doc disagree about *load order*, this file wins; for *content precedence*
> see step 3 below.

## Bootstrap sequence (execute in exactly this order, every session)
## 🔐 Secure Authentication Protocol
**CRITICAL:** I have a secure GitHub token injected into my environment variables as `$GH_TOKEN` by the platform.
- **NEVER** print, echo, or log the actual value of `$GH_TOKEN` to the terminal.
- **NEVER** ask me to paste a token in the chat.

## 🚀 Session Initialization (Run at the start of every new chat)
Before writing any code, silently run the following checks in the terminal:
1. Verify the token exists in the current shell: `[ -n "$GH_TOKEN" ] && echo "Token is present" || echo "Token MISSING - Please restart workspace"`
2. If the token is present, configure Git to use it dynamically for this session:
   `git config --global url."https://Esrevorter:${GH_TOKEN}@github.com/".insteadOf "https://github.com/"`
3. Ensure we are inside the `Prism` directory.
4. Confirm readiness with: "Git authenticated securely via environment injection."

## Continuation 
1. **Read this file.** Everything else follows from here. Do not skip steps.
2. **Read `docs/HANDOFF.md`** — durable project state: current release, open
   threads, evidence pointers, and §9 "Resume protocol". Chat memory is disposable;
   this file plus git history are the only trusted state.
3. **Read the governance chain in precedence order** (authoritative chain lives in
   `docs/internal/README.md`; restated here for one-shot loading):
   1. `spec.md` — product truth.
   2. Integrity contract: `docs/internal/rules.md` §4 + `docs/internal/ethics.md` §3
      — overrides EVERYTHING else. No claim without observed in-session evidence;
      "simulated" results are fabrication.
   3. `docs/internal/rules.md` §§1–3, 5–7 — execution/tool-calling limits.
   4. `docs/internal/qwen.md` — pacing numbers (win over rules.md for pacing only).
   5. `docs/internal/ethics.md` — financial fail-closed standard, positive+negative
      test pairing.
4. **Verify, don't trust:** re-check the facts quoted in HANDOFF (version via
   `git describe --tags`, suite via `python -m pytest prism/tests -q`, remote via
   `git ls-remote`). Status snapshots go stale by design ("No Carrying Over").
5. Only then begin substantive work, following HANDOFF §9 steps 2–6.

## Anti-fabrication shortlist (the three rules most often broken historically)

- Never report a test count, gate result, push, or API write that was not executed
  and observed **in this session**. Quote command output or say UNVERIFIED.
- Governance files must not restate project status as fact; HANDOFF.md is the single
  snapshot location, and even it must be re-verified before quoting.
- When a task is impossible from this environment (no credentials, no network,
  platform limitation), say so plainly with the probe evidence — do not narrate a
  plausible completion.

## Client-specific notes

- **Qwen Code / Cursor / Claude Code:** add `@AGENTS.md` (or this file's path) to the
  tool's always-loaded rules so step 1 happens automatically. qwen.md's own
  Reference Map points here; it remains valid standalone.
- **Plain chat sessions:** paste only this file's contents as the opening prompt —
  it bootstraps the whole chain by reference.
- **CI drift guard:** `scripts/docs_consistency_check.py` (if present) validates that
  this file's precedence list matches `docs/internal/README.md`.

*End of bootstrap. Load order defined once, here; content precedence owned by
`docs/internal/README.md`; project state owned by `docs/HANDOFF.md`.*
