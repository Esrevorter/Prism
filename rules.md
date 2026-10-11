# Tool Calling and Execution Rules

## 1. Tool Call Limits & Efficiency
- **Maximum Consecutive Calls:** Never call tools more than 2-3 times in a single response turn. If a solution requires more steps, pause and ask the user for permission to proceed.
- **Batching:** If you need to perform multiple actions (e.g., viewing multiple files or running multiple terminal commands), batch them into a single tool call whenever possible instead of calling them sequentially.

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
