# Qwen Code Rules & Operational Constraints

## Concurrency & Request Limits
- **Strict Sequential Execution:** You must wait for the current request or tool execution to complete fully before initiating any additional requests.
- **No Parallel Tool Calls:** Do not attempt to run multiple file reads, terminal executions, or API calls simultaneously.
- **Rate-Limiting Compliance:** Cap concurrent in-flight requests per provider at 1. If a task requires multiple steps, break them down and execute them one by one.

## Error Recovery
- If you encounter a connection error or a "Too many concurrent requests" message, pause all operations for 5 seconds before retrying.
- Reduce token payload sizes by omitting unneeded code context if rate limits continue to trigger.
