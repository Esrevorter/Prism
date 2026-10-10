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
