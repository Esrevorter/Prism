# Qwen Code Rules & Operational Constraints

# Qwen3-Coder Client Scaling & Anti-Burst Rules

> **Provenance / stress-test log (Oct 10, 2026):** The original pacing values (4 s inter-request delay, 8 s post-heavy delay, 10 s resume warm-up, 10 s error pause) were repeatedly stress-tested against the provider and consistently stalled with `429 Throttling.BurstRate`. The values below are the empirically safe replacements. Do not lower them without a fresh stress test.

## Crucial Execution Pacing
- **Smooth Traffic Scaling:** To prevent upstream system instability errors, you must actively scale your client logic and request velocity smoothly over time.
- **Mandatory Inter-Request Delay:** You must inject an artificial sleep/delay of at least **10 seconds** between completing one tool call (or request) and initiating the next. A 4 s gap was stress-tested against the `Throttling.BurstRate` threshold and failed to keep the session alive; 10 s is the empirically safe value. This delay is measured from the moment the previous response is fully received — never start counting it while a prior request is still in flight.
- **Extended Delay After Heavy Turns:** After any turn that produced a large tool result (long file views, full test-suite runs, big diffs, multi-file output), extend the inter-request delay to at least **20 seconds** to let the upstream connection settle before the next request.
- **Strict Anti-Burst Buffering:** Never fire rapid, back-to-back API actions or parallel verification requests. Treat every step as an isolated, sequential block.
- **Uniform Scheduling:** Distribute your file reading, diagnostics, and workspace edits evenly over time instead of causing instantaneous request peaks.
- **One Tool Call Per Turn:** Emit at most one tool call per assistant turn; do not chain multiple calls in a single burst.

## Concurrency & Request Limits
- **Strict Sequential Execution:** You must wait for the current request or tool execution to complete fully before initiating any additional requests.
- **No Parallel Tool Calls:** Do not attempt to run multiple file reads, terminal executions, or API calls simultaneously. Under no circumstances may two requests be in-flight at once, even briefly — always await the full response before sending the next.
- **Rate-Limiting Compliance:** Cap concurrent in-flight requests per provider at exactly 1 (hard limit, zero tolerance for overlap). If a task requires multiple steps, break them down and execute them one by one.
- **Session Resume Discipline:** When reconnecting after a dropped session or error, wait at least **30 seconds** before issuing the first new request (the old 10 s warm-up still triggered burst-rate throttling in stress tests), and start with the smallest possible operation (e.g., a short status check) rather than a heavy read or command. Ramp up gradually: first 3 requests spaced ≥ 20 s apart, then settle to the normal inter-request delay.

## Error Recovery
- If you encounter a connection error or a "Too many concurrent requests" message, pause all operations for **30 seconds** (upgraded from 10 s; the 10 s pause proved insufficient in stress tests) before retrying, and double the subsequent inter-request delay for the rest of the session.
- If retries continue to fail, escalate the backoff exponentially: 30 s → 60 s → 120 s, reducing the request payload on each attempt. Never retry faster than the exponential schedule — premature retries re-trigger the throttle and extend the total downtime.
- Reduce token payload sizes by omitting unneeded code context if rate limits continue to trigger. Prefer targeted `sed -n`/line-range views over full-file cat, and `pytest -k` subsets over full suite runs when diagnosing repeated failures.
- If you hit the `429 Throttling.BurstRate` error ("Request rate increased too quickly"), you must pause all execution entirely for **30 seconds** (upgraded from 10 s; shorter pauses were re-throttled in stress tests) before attempting an exponential backoff retry, and resume at half the previous request velocity (i.e., double the normal inter-request delay) for the remainder of the session.

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

