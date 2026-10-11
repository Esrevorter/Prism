# Tool Calling and Execution Rules

> **Binding companion files (re-read before every substantive task):** `qwen.md`
> (anti-burst pacing — one tool call per turn, sequential only, ≥20 s inter-request;
> Oct 11 addendum: if this file and `qwen.md` ever disagree on pacing numbers,
> `qwen.md` wins),
> `ethics.md` (financial-ethics coding & reporting standard — fail-closed money handling,
> proof-or-UNVERIFIED status claims). rules.md §4 and ethics.md §3 together form the
> integrity contract: no status message may be sent without a self-audit against observed
> tool output from the current session.

## 1. Tool Call Limits & Efficiency
- **Maximum Consecutive Calls:** Never call tools more than 2-3 times in a single response turn. If a solution requires more steps, pause and ask the user for permission to proceed. *(Exception for tidying/cleanup sessions: see §5 — do NOT stop to ask; batching is mandatory there.)*
- **Batching:** If you need to perform multiple actions (e.g., viewing multiple files or running multiple terminal commands), batch them into a single tool call whenever possible instead of calling them sequentially. Batch aggressively: chained commands (`cmd1 && cmd2 && cmd3`, multi-file `grep`/`sed -n`) count as ONE request toward the qwen.md pacing/breather budget.
- **Parallel-Call Prohibition (overrides any client default):** Even if the agent client supports emitting multiple independent tool calls in one block, never do so against Qwen3-Coder — two simultaneously dispatched requests trip the provider's "Too many concurrent requests" limit. Emit exactly one tool call, await its full result, then proceed per qwen.md pacing.

## 2. Strict Stopping Criteria
- **Loop Prevention:** If a tool returns the exact same error or result twice, DO NOT call it a third time. Stop immediately, explain the issue to the user, and ask for manual intervention.
- **Diminishing Returns:** If the first tool call provides 80% of the required context, do not make subsequent calls for minor details. Rely on your internal knowledge base to fill in non-critical gaps.

## 3. Mandatory Pre-Conditions
- **Think Before You Call:** Before invoking any tool, explicitly state in your internal thoughts (or a short text response) *why* the tool is necessary and *what* specific piece of missing information you expect to gain.
- **No Redundant Calls:** Never call a tool to search for information that has already been provided in the conversation history or the initial prompt.

## 4. Verifiable Status Reporting — Absolute Integrity Rule (MANDATORY)
*Grounded in the project's core principle: users rely on honest, plain-language status because they cannot audit every internal step. A fabricated status report voids that trust and directly violates the neuro-inclusive / honest-UX mandate in spec.md. This section overrides any convenience, brevity, or optimism pressure.*

- **Reality-Only Claims:** Every statement about work being done, passing, committed, pushed, merged, or verified MUST be backed by an actual tool result observed in the current session (e.g., exit code + output of the command run, `git log`/`git status` showing the commit, a fetch confirming the remote tip). No exceptions.
- **Never Predict Results as Facts:** Do not describe a test outcome, benchmark number, commit hash, push result, or PR state before the command that produces it has actually completed and its output has been read. If a job was launched but its result was not observed, report it as "launched, result unknown" — never as passed/failed.
- **No Carrying Over Assumed State:** Previous turns' summaries are NOT evidence of current state. Before asserting anything about files, commits, branches, remotes, or test results, re-inspect the actual repository/environment state with a tool call. Context may have been truncated; sessions may have stalled mid-operation.
- **Distinguish Intent from Achievement:** Phrases like "next steps include…" must be clearly separated from "completed:". A plan is never reported as progress. Partial completion must be labeled PARTIAL with an explicit list of what is done (each item tied to its evidence) and what is not done.
- **Fail Loud, Not Gracefully:** If a verification step fails, times out, or cannot be run, say so plainly with the exact error/output. Do not soften, omit, or substitute an assumed value. Never claim "should pass" or "expected green" as a substitute for observed green.
- **Evidence Citations in Status Reports:** When reporting completion of a substantive task, include the concrete artifact of proof inline (test count + duration line, commit hash from `git log`, remote tip from a fresh fetch, benchmark summary line). If the artifact does not exist yet, the task is not complete.
- **Self-Audit Before Sending:** Before any status/summary response, scan each factual claim and confirm a corresponding observed tool output exists in this session. Any claim that fails this check must be removed or restated as unverified.
- **Zero-Tolerance Retroactive Correction:** If a hallucinated or unverified claim is discovered (by self or user), immediately correct the record with verified facts, re-run the missing verifications, and note the correction prominently rather than quietly editing the narrative.

## 5. Tidying / Cleanup Sessions — Low-Concurrency Protocol
*Added Oct 11, 2026 after repeated provider errors "Oops! There was an issue connecting to Qwen3-Coder. Too many concurrent requests." during tidying tasks. Root cause: tidy-up workflows naturally fan out into many small reads/status checks (git status, ls, per-file views, linters), which either overlap in-flight or sustain a request cadence above the burst-rate threshold.*

- **Inventory First, One Pass:** Start every tidying task with exactly ONE batched inventory command (e.g., `git status --short && git diff --stat && ls -la`) instead of probing files individually. Plan the entire cleanup from that single output; do not re-inspect unless the plan changes.
- **Chain Everything:** Combine related operations into chained shell commands (`git add && git commit`, `sed -i` across multiple listed files in one call). Each chain counts as one request; target ≤ 5 total tool calls for a typical tidy-up.
- **Never Fan Out:** Do not issue parallel/per-block multi-tool calls even when the client allows it (see §1 Parallel-Call Prohibition). Tidy-up steps are strictly sequential with full qwen.md pacing between them (≥20 s steady state, ≥30 s after heavy output, 60 s breather every 8th call).
- **Skip Optional Verification Steps:** During tidying, suppress nice-to-have checks (redundant `git log`, repeat `ls`, full test suites) unless spec.md/ethics.md §4 requires evidence for a status claim. When evidence IS required, prefer one targeted check over several broad ones.
- **On "Too many concurrent requests":** Stop immediately, do NOT auto-retry inline. Wait the full 60 s (then 120 s → 240 s exponential per qwen.md Error Recovery), resume with the smallest possible status check, and continue at doubled inter-request delay (≥40 s) for the rest of the session. Report the stall plainly to the user rather than hammering retries — premature retries extend the throttle window.
