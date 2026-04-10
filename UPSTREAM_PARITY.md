# Upstream Parity

This file is the living parity ledger for the local `hahobot` fork.

It is inspired by the `PARITY.md` style used in `soongenwong/claudecode`: keep one explicit
document for "already matched", "intentionally different", and "still worth checking" instead of
burying that state across commit messages and chat logs.

## Scope

Primary upstream tracked here:

- `HKUDS/nanobot`

Related inspiration that is intentionally **not** treated as a parity target:

- `shenmintao/NanoMate`
- Hermes Agent docs
- `soongenwong/claudecode`

Those projects influence local direction, but this file is mainly for deciding whether a change
from upstream `nanobot` still needs to be ported, re-audited, or deliberately ignored.

## Status Legend

- `synced`: behavior already exists locally, possibly with extra local wiring or tests
- `local_extension`: local-only capability; upstream parity is not the main goal
- `intentional_divergence`: local behavior deliberately differs from upstream
- `watchlist`: area should be re-checked during the next upstream sync

## Current Snapshot

| Area | Status | Local State |
| --- | --- | --- |
| Tool/runtime policy | `synced` | Runtime tool enable/disable checks are centralized, hot reload can add/remove tool families, and doctor output reuses the same policy layer. |
| Hook lifecycle semantics | `synced` | Hook fan-out supports `reraise` semantics and keeps compatibility behavior for legacy hooks. |
| OpenAI direct reasoning routing | `synced` | Direct OpenAI GPT-5/o-series requests prefer Responses API and fall back to Chat Completions only for compatibility errors. |
| Tool hint formatting | `synced` | Exec hints handle quoted paths, path abbreviation, and duplicate collapse in one formatter. |
| Telegram / Discord streaming | `synced` | Telegram uses configurable `channels.telegram.streamEditInterval`; Discord keeps edit-based streaming enabled by default. |
| Legacy rename compatibility | `synced` | `nanobot` CLI/module/import compatibility stays live, and default config fallback is preserved. |
| Config fallback behavior | `intentional_divergence` | When no config path is passed, hahobot checks `~/.hahobot/config.json` first, then copies `~/.nanobot/config.json` into the hahobot location instead of migrating in place. |
| Persona / companion workflow | `local_extension` | `PROFILE.md`, `INSIGHTS.md`, `STYLE.md`, `LORE.md`, companion commands, SillyTavern imports, voice overrides, and scene generation are local-first features. |
| Memory architecture | `local_extension` | Dream maintenance, archive sidecars, Mem0 backend/shadow-write, and structured profile/insight hygiene go beyond upstream nanobot. |
| Gateway/admin/runtime ops | `local_extension` | Admin UI, `/status`, Star-Office push, companion doctor, runtime doctor, and session inspection are local operational surfaces. |
| Extension model | `local_extension` | Skills, MCP, built-in companion helpers, and `ExternalHookBridge` are the main extension surfaces; there is no separate plugin framework today. |
| Future upstream channel/provider churn | `watchlist` | Re-audit `channels/`, `providers/`, `agent/hook.py`, `config/schema.py`, and runtime doctor whenever upstream lands new runtime toggles or transport behavior. |

## Already Synced From Upstream nanobot

These are the upstream-facing items that are already present locally and should normally be treated
as "do not re-port unless upstream changes again":

- Telegram streamed reply throttling is config-backed through `channels.telegram.streamEditInterval`.
- Discord streamed final replies use the same edit-then-finalize model and keep related UX knobs
  together.
- Hook composition supports explicit `reraise` semantics while preserving compatibility behavior.
- Direct OpenAI reasoning requests use Responses-first routing with compatibility fallback.
- Runtime tool hints format shell commands more robustly, including quoted paths and repeated calls.
- Version resolution prefers `importlib.metadata` and falls back to `pyproject.toml` in source trees.
- Workspace/runtime behavior keeps the rename compatibility layer alive for `nanobot` entrypoints and
  imports.

## Intentional Local Differences

These are local choices. When upstream behaves differently, that is not automatically a bug:

- Hahobot is not a thin mirror of `nanobot`; it is a workspace-first local runtime with companion
  and persona workflows layered on top.
- The rename transition is conservative: legacy config is copied into the hahobot path instead of
  moved, and existing legacy default workspaces are preserved.
- The local project keeps `PROFILE.md` and `INSIGHTS.md` as separate memory layers rather than
  treating all long-term user/context data as one flat store.
- Admin, gateway status, Star-Office integration, companion doctor, and local session inspection
  are first-class local ops features even when upstream does not have equivalents.
- Extension priority is currently `skills + MCP + hook bridge`; do not add a separate plugin
  framework unless there is a concrete gap those surfaces cannot cover cleanly.

## Watchlist For Next Upstream Sync

- Re-check upstream `channels/*` when new transport defaults, streaming semantics, or multi-instance
  behavior changes land.
- Re-check upstream `providers/*` when request routing, retry behavior, or error surfaces change.
- Re-check upstream `agent/hook.py`, `agent/runner.py`, and related tests whenever lifecycle
  contracts move.
- Re-check upstream `config/schema.py` only when behavior changes; avoid meaningless field-order
  churn.
- Re-check docs/admin/AGENTS together whenever an upstream config toggle becomes user-visible in the
  local runtime.

## Update Checklist

When syncing from upstream `nanobot`, update this file in the same patch:

1. Identify whether the upstream change is `synced`, `local_extension`, `intentional_divergence`,
   or `watchlist`.
2. Record the concrete local surface that now owns the behavior: code path, command, config field,
   or doc section.
3. If local behavior intentionally differs, write the reason here instead of leaving the difference
   implicit.
4. If a change affects user-visible behavior or contributor workflow, update `README.md`,
   `README_ZH.md`, and `AGENTS.md` together.

