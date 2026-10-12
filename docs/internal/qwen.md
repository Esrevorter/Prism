# QWEN SYSTEM OPERATING CONSTRAINTS
You must strictly cross-reference and follow the constraints provided across our system files before outputting any answer or code block.

## Reference Map
- **General Architecture & Rules:** See @docs/internal/rules.md
- **Safety & Behavioral Guardrails:** See @docs/internal/ethics.md
- **Technical & Feature Requirements:** See @spec.md

## Execution Enforcement
1. Before generating any response, verify that your claims are grounded entirely by the parameters laid out in @spec.md.
2. If a library, function, or fact is requested that contradicts or is absent from @spec.md, do not fabricate a plausible-sounding alternative. State explicitly that it is missing or unsupported.

# Qwen Code Rules & Operational Constraints

# Qwen3-Coder Client Scaling & Anti-Burst Rules

> **Provenance / stress-test log (Oct 10, 2026):** The original pacing values (4 s inter-request delay, 8 s post-heavy delay, 10 s resume warm-up, 10 s error pause) were repeatedly stress-tested against the provider and consistently stalled with `429 Throttling.BurstRate`. The values below were the empirically safe replacements.
>
> **Stress-test log (Oct 11, 2026):** The 10 s / 20 s pair ALSO failed under sustained long-session load: despite full local compliance (verified sleeps between every tool call), the provider returned "Request rate increased too quickly" during heavy multi-turn work. Root cause analysis: burst-rate throttling responds to *sustained request cadence over minutes*, not just gaps between adjacent calls; a 10 s steady-state cadence (~6 req/min indefinitely) re-triggers it. Values raised accordingly below. Do not lower them without a fresh stress test.

## Crucial Execution Pacing
- **Smooth Traffic Scaling:** To prevent upstream system instability errors, you must actively scale your client logic and request velocity smoothly over time.
- **Mandatory Inter-Request Delay:** You must inject an artificial sleep/delay of at least **20 seconds** between completing one tool call (or request) and initiating the next. Both 4 s and 10 s gaps were stress-tested and failed to keep long sessions alive; 20 s is the current empirically safe value. This delay is measured from the moment the previous response is fully received — never start counting it while a prior request is still in flight.
- **Extended Delay After Heavy Turns:** After any turn that produced a large tool result (long file views, full test-suite runs, big diffs, multi-file output), extend the inter-request delay to at least **30 seconds** to let the upstream connection settle before the next request.
- **Sustained-Cadence Breather Rule:** Every **8th consecutive tool call**, insert a **60-second breather** pause regardless of individual gaps — burst-rate counters integrate over rolling windows, so periodic long pauses are required to keep windowed velocity below the threshold. Prefer fewer, batched operations (e.g., one command chaining `git add && git commit && git push`) over many granular calls.
- **Strict Anti-Burst Buffering:** Never fire rapid, back-to-back API actions or parallel verification requests. Treat every step as an isolated, sequential block.
- **Uniform Scheduling:** Distribute your file reading, diagnostics, and workspace edits evenly over time instead of causing instantaneous request peaks.
- **One Tool Call Per Turn:** Emit at most one tool call per assistant turn; do not chain multiple calls in a single burst.

## Concurrency & Request Limits
- **Strict Sequential Execution:** You must wait for the current request or tool execution to complete fully before initiating any additional requests.
- **No Parallel Tool Calls:** Do not attempt to run multiple file reads, terminal executions, or API calls simultaneously. Under no circumstances may two requests be in-flight at once, even briefly — always await the full response before sending the next.
- **Rate-Limiting Compliance:** Cap concurrent in-flight requests per provider at exactly 1 (hard limit, zero tolerance for overlap). If a task requires multiple steps, break them down and execute them one by one.
- **Session Resume Discipline:** When reconnecting after a dropped session or error (including provider-side stalls like "Request rate increased too quickly"), wait at least **60 seconds** before issuing the first new request (30 s warm-ups were re-throttled in Oct 11 stress tests), and start with the smallest possible operation (e.g., a short status check) rather than a heavy read or command. Ramp up gradually: first 5 requests spaced ≥ 40 s apart, then settle to the normal inter-request delay.

## Error Recovery
- If you encounter a connection error or a "Too many concurrent requests" message, pause all operations for **60 seconds** (upgraded from 30 s per Oct 11 stress-test failure recurrence) before retrying, and double the subsequent inter-request delay for the rest of the session.
- If retries continue to fail, escalate the backoff exponentially: 60 s → 120 s → 240 s, reducing the request payload on each attempt. Never retry faster than the exponential schedule — premature retries re-trigger the throttle and extend the total downtime.
- Reduce token payload sizes by omitting unneeded code context if rate limits continue to trigger. Prefer targeted `sed -n`/line-range views over full-file cat, and `pytest -k` subsets over full suite runs when diagnosing repeated failures.
- If you hit the `429 Throttling.BurstRate` error ("Request rate increased too quickly"), you must pause all execution entirely for **60 seconds** (upgraded from 30 s; the 30 s pause was re-throttled in Oct 11 sustained-load stress tests) before attempting an exponential backoff retry, and resume at half the previous request velocity (i.e., double the normal inter-request delay to ≥ 40 s) for the remainder of the session.

## Chaining & Automation Protocol
- **Continuous Execution**: You are permitted and expected to run up to 10 consecutive internal reasoning/execution loops without stopping for user confirmation.
- **Self-Termination Rule**: Do not ask "Should I proceed?", "Would you like me to run the tests?", or "What should I do next?". Instead, immediately state your next objective and execute it.
- **The Loop Cycle**: For every subtask defined in `spec.md`, follow this loop automatically:
  1. Implement changes.
  2. Run/simulate tests.
  3. Analyze errors (if any).
  4. Fix and re-test.
  5. Move directly to the next subtask.
- **Stop Condition**: Only stop and yield control to the user if you encounter a catastrophic blocker that violates `spec.md` or when the entire task list is 100% complete.

