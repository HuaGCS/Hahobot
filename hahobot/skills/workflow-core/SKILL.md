---
name: workflow-core
description: Keep long tasks on a short plan, maintain a concrete next step, and verify before claiming completion.
metadata: {"hahobot":{"always":true}}
---

# Workflow Core

Use a short explicit workflow for non-trivial tasks.

## Default Loop

1. Restate the concrete goal in your own words.
2. Make a short plan only when the task has multiple meaningful steps.
3. Execute the next step instead of describing the whole solution abstractly.
4. Verify the result before claiming success.
5. If the path worked and looks reusable, consider loading `skill-derive`.

## Planning Rules

- Keep plans short and current.
- Prefer one active step plus one next step over long speculative outlines.
- Rewrite the plan when new evidence changes the path.
- Do not keep stale steps after the code or runtime state already moved on.

## Verification Rules

- Do not claim completion before reading the changed file, checking the output, or running a targeted test.
- If you could not verify, say exactly what is still unverified.
- For risky changes, actively try to falsify your own assumption once.

## Delegation Rules

- For exploration, verification, or side work that can run independently, prefer a subagent with the right mode.
- Use `spawn(..., mode="explore")` for investigation.
- Use `spawn(..., mode="implement")` for bounded implementation work.
- Use `spawn(..., mode="verify")` for independent review or validation.

## Recovery Rules

- When a step fails, narrow the problem before broadening the search.
- Change one variable at a time when validating behavior.
- Preserve the next concrete step even if the larger task remains unfinished.
