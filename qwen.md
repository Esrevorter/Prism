# Qwen Code Rules & Operational Constraints

# Qwen3-Coder Client Scaling & Anti-Burst Rules

## Crucial Execution Pacing
- **Smooth Traffic Scaling:** To prevent upstream system instability errors, you must actively scale your client logic and request velocity smoothly over time.
- **Mandatory Inter-Request Delay:** You must inject an artificial sleep/delay of at least **4 seconds** between completing one tool call (or request) and initiating the next. This delay is measured from the moment the previous response is fully received — never start counting it while a prior request is still in flight.
- **Extended Delay After Heavy Turns:** After any turn that produced a large tool result (long file views, full test-suite runs, big diffs, multi-file output), extend the inter-request delay to at least **8 seconds** to let the upstream connection settle before the next request.
- **Strict Anti-Burst Buffering:** Never fire rapid, back-to-back API actions or parallel verification requests. Treat every step as an isolated, sequential block.
- **Uniform Scheduling:** Distribute your file reading, diagnostics, and workspace edits evenly over time instead of causing instantaneous request peaks.
- **One Tool Call Per Turn:** Emit at most one tool call per assistant turn; do not chain multiple calls in a single burst.

## Concurrency & Request Limits
- **Strict Sequential Execution:** You must wait for the current request or tool execution to complete fully before initiating any additional requests.
- **No Parallel Tool Calls:** Do not attempt to run multiple file reads, terminal executions, or API calls simultaneously. Under no circumstances may two requests be in-flight at once, even briefly — always await the full response before sending the next.
- **Rate-Limiting Compliance:** Cap concurrent in-flight requests per provider at exactly 1 (hard limit, zero tolerance for overlap). If a task requires multiple steps, break them down and execute them one by one.
- **Session Resume Discipline:** When reconnecting after a dropped session or error, wait at least **10 seconds** before issuing the first new request, and start with the smallest possible operation (e.g., a short status check) rather than a heavy read or command.

## Error Recovery
- If you encounter a connection error or a "Too many concurrent requests" message, pause all operations for **10 seconds** (upgraded from 5 s) before retrying, and double the subsequent inter-request delay for the rest of the session.
- If retries continue to fail, escalate the backoff exponentially: 10 s → 20 s → 40 s, reducing the request payload on each attempt.
- Reduce token payload sizes by omitting unneeded code context if rate limits continue to trigger. Prefer targeted `sed -n`/line-range views over full-file cat, and `pytest -k` subsets over full suite runs when diagnosing repeated failures.
- If you hit the `429 Throttling.BurstRate` error ("Request rate increased too quickly"), you must pause all execution entirely for **10 seconds** (upgraded from 5 s) before attempting an exponential backoff retry, and resume at half the previous request velocity.

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

