---
name: verify
description: Validate a proposed or completed change with an independent falsification mindset.
---

# Verify

Use this skill when you need to confirm whether a result is actually correct.

## Verification Mindset

- Assume the first implementation may be wrong.
- Look for the cheapest decisive check first.
- Prefer primary evidence over explanation.

## Verification Order

1. Re-read the changed code or inspected artifact
2. Check the exact path that should now work
3. Run the smallest targeted test or command that can fail decisively
4. Report what was verified and what remains unverified

## What To Look For

- Behavioral regressions
- Broken assumptions at boundaries
- Missing edge cases
- Mismatch between code and docs/config
- Claims that are not backed by runtime evidence
- Future-task plans that lack evidence, acceptance criteria, or clear value over repeated low-signal work

## Subagent Use

If you need an independent pass, prefer `spawn(..., mode="verify")` and ask for:

- concrete findings first
- exact file or command evidence
- item-by-item scores when reviewing a plan or TODO list
- residual risks only after findings
