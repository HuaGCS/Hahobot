---
name: skill-derive
description: Turn a repeatedly successful local workflow into a reusable workspace skill draft.
---

# Skill Derive

Use this skill when a successful task produced a reusable workflow that should not stay buried in chat history.

## Good Candidates

- A debugging flow that solved a recurring problem
- A repeatable repo-specific implementation pattern
- A stable checklist for release, review, or migration work
- A local integration workflow with concrete tools and prerequisites

## Draft Shape

Capture:

- when to use the workflow
- what evidence to gather first
- exact tool sequence or command family
- known pitfalls and failure patterns
- clear stop conditions

## What Not To Capture

- One-off chat phrasing
- Temporary incident state
- Secrets, tokens, or personal data
- Workflows that only succeeded because of stale local state

## Quality Bar

- Reusable inside the current workspace
- Narrow enough to be trustworthy
- Concrete enough that another future run can follow it
