---
name: plan
description: Build and maintain short execution plans for multi-step engineering tasks.
---

# Plan

Use this skill when the task is large enough that a short explicit plan improves correctness or coordination.

## When To Use

- The task has multiple files or subsystems
- You need to coordinate implementation and validation
- You need to delegate work to subagents
- The user asked for a design or staged rollout

## Plan Shape

Keep the plan compact:

- Goal
- Current step
- Next step
- Optional follow-up steps only if they are already justified

## Good Plans

- Concrete and executable
- Ordered by dependency
- Easy to invalidate and update
- Reviewed independently before execution when they generate future tasks or autonomous TODOs

## Bad Plans

- Long checklists with no active step
- Purely descriptive restatements of the request
- Steps that assume outcomes you have not verified yet
- Plans that use self-review as permission to execute unvalidated future work

## Review Gate

- For plans that create or reorder future tasks/TODOs, use an independent verification pass before executing those tasks.
- Prefer `spawn(..., mode="verify")` and ask for item-by-item scoring, weak assumptions, missing acceptance criteria, and low-value/repetitive work.
- If independent verification cannot run, leave the plan as a draft and do not execute follow-up tasks in the same turn.

## During Execution

- Update the plan after decisive new evidence
- Remove stale steps instead of crossing them out verbally forever
- If the task collapses to one clear action, stop planning and do the action
