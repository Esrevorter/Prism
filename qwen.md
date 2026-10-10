# Qwen Code Rules & Operational Constraints

# Qwen3-Coder Client Scaling & Anti-Burst Rules

## Crucial Execution Pacing
- **Smooth Traffic Scaling:** To prevent upstream system instability errors, you must actively scale your client logic and request velocity smoothly over time.
- **Mandatory Inter-Request Delay:** You must inject an artificial sleep/delay of at least **2 seconds** between completing one tool call (or request) and initiating the next. 
- **Strict Anti-Burst Buffering:** Never fire rapid, back-to-back API actions or parallel verification requests. Treat every step as an isolated, sequential block.
- **Uniform Scheduling:** Distribute your file reading, diagnostics, and workspace edits evenly over time instead of causing instantaneous request peaks.

## Concurrency & Request Limits
- **Strict Sequential Execution:** You must wait for the current request or tool execution to complete fully before initiating any additional requests.
- **No Parallel Tool Calls:** Do not attempt to run multiple file reads, terminal executions, or API calls simultaneously.
- **Rate-Limiting Compliance:** Cap concurrent in-flight requests per provider at 1. If a task requires multiple steps, break them down and execute them one by one.

## Error Recovery
- If you encounter a connection error or a "Too many concurrent requests" message, pause all operations for 5 seconds before retrying.
- Reduce token payload sizes by omitting unneeded code context if rate limits continue to trigger.
- If you hit the `429 Throttling.BurstRate` error ("Request rate increased too quickly"), you must pause all execution entirely for **5 seconds** before attempting an exponential backoff retry.

