---
name: llm-wiki
description: Use the current workspace like a local wiki for architecture terms, config semantics, and repo-backed explanations.
---

# LLM Wiki

Treat the current workspace as a local wiki.

Use this skill when the user is asking:

- what a project term, command, config field, or subsystem means
- where a behavior is implemented or documented
- how a feature is supposed to work in this repository
- whether docs, tests, and runtime code agree

Do not invent missing wiki content. If the repository does not answer the question, say that
clearly and fall back to normal code inspection or web research only when the task actually needs
external sources.

## Search flow

Prefer this sequence:

1. Start narrow with `glob` or directory listing to find likely docs and code roots.
2. Use `grep` to locate the exact term, symbol, command, or config path before reading whole files.
3. Read the most authoritative local sources for the question.
4. Cross-check docs against code and tests before answering.

## Good local sources

Use the repository itself as the wiki index:

- `README.md`, `README_ZH.md`, and `AGENTS.md` for user-facing behavior and contributor rules
- `UPSTREAM_PARITY.md` for sync state, intentional divergences, and local extensions
- `hahobot/config/` for config schema and defaults
- `hahobot/agent/`, `hahobot/channels/`, `hahobot/providers/`, and `hahobot/gateway/` for runtime truth
- `hahobot/locales/` for user-visible wording
- `tests/` for behavioral expectations and regression coverage

If available, archived history or project memory tools can help with prior rationale, but current
repository state stays authoritative.

## Answering rules

- Prefer repo-backed explanations over generic theory.
- Cite the concrete file or config path that supports the answer.
- Call out mismatches between docs, code, and tests instead of smoothing them over.
- When multiple layers exist, explain precedence explicitly: runtime code first, then tests, then docs.
- Keep answers concise and practical; this is a wiki lookup flow, not an essay contest.
