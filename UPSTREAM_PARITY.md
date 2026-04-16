# Upstream Parity

This file is the living parity ledger for the local `hahobot` fork.

It is inspired by the `PARITY.md` style used in `soongenwong/claudecode`: keep one explicit
document for "already matched", "intentionally different", and "still worth checking" instead of
burying that state across commit messages and chat logs.

## Scope

Primary upstreams tracked here:

- `HKUDS/nanobot`
- `lsdefine/GenericAgent`

Related inspiration that is intentionally **not** treated as a parity target:

- `shenmintao/NanoMate`
- Hermes Agent docs
- `soongenwong/claudecode`

These upstreams are not tracked in the same way:

- `nanobot` remains the main behavior-parity target for runtime, channel, provider, and config
  sync decisions.
- `GenericAgent` is tracked as an architectural/workflow upstream: planning SOPs, layered memory
  semantics, skill accumulation, and lightweight autonomous loops are worth auditing, but local
  implementation is not expected to mirror file layout or minimal-tool philosophy one-to-one.

This file therefore records both:

- direct upstream parity work for `nanobot`
- explicit adoption / divergence decisions for `GenericAgent` where the ideas are relevant to
  hahobot's local runtime

## Status Legend

- `synced`: behavior already exists locally, possibly with extra local wiring or tests
- `local_extension`: local-only capability; upstream parity is not the main goal
- `intentional_divergence`: local behavior deliberately differs from upstream
- `watchlist`: area should be re-checked during the next upstream sync

## Current Snapshot

| Area | Status | Local State |
| --- | --- | --- |
| Tool/runtime policy | `synced` | Runtime tool enable/disable checks are centralized, hot reload can add/remove tool families, doctor output reuses the same policy layer, and shell env passthrough stays explicit through `tools.exec.allowedEnvKeys`. |
| Turn recovery / idle compact safety | `synced` | Session recovery now restores runtime checkpoints before the next request, persists plain-text user turns early so crashes do not lose the prompt, closes orphaned pending user turns, and skips proactive auto-compact while a session still has an in-flight agent task. |
| Hook lifecycle semantics | `synced` | Hook fan-out supports `reraise` semantics and keeps compatibility behavior for legacy hooks. |
| OpenAI direct reasoning routing | `synced` | Direct OpenAI GPT-5/o-series requests prefer Responses API and fall back to Chat Completions only for compatibility errors. |
| Tool hint formatting | `synced` | Exec hints handle quoted paths, path abbreviation, and duplicate collapse in one formatter. |
| Provider request sanitization | `synced` | Role alternation repair now recovers a trailing assistant message as `user` when otherwise only `system` content would remain, preventing empty/invalid provider requests after assistant-scoped injections. |
| Skill filtering / idle compact / MCP tool filtering | `synced` | `agents.defaults.disabledSkills`, `agents.defaults.idleCompactAfterMinutes` (plus `sessionTtlMinutes` alias), and `tools.mcpServers.<name>.enabledTools` are wired through local runtime, tests, and docs. |
| Cron state / scheduler behavior | `synced` | Cron preserves last-run status plus merged run history on disk, and the workspace scheduler periodically wakes to reload external `cron/jobs.json` edits via `gateway.cron.maxSleepMs`. |
| Telegram / Discord streaming | `synced` | Telegram uses configurable `channels.telegram.streamEditInterval`; Discord keeps edit-based streaming enabled by default, and the related runtime knobs are exposed in local schema/docs/admin surfaces. |
| Legacy rename compatibility | `synced` | `nanobot` CLI/module/import compatibility stays live, and default config fallback is preserved. |
| Config fallback behavior | `intentional_divergence` | When no config path is passed, hahobot checks `~/.hahobot/config.json` first, then copies `~/.nanobot/config.json` into the hahobot location instead of migrating in place. |
| Web search backend mix | `synced` | Built-in web search now supports Brave, SearXNG, and DuckDuckGo; DuckDuckGo runs as an exclusive tool so concurrent tool batches do not group multiple searches together. |
| GenericAgent-style SOP workflow | `watchlist` | GenericAgent's explicit planning / verification / memory-management SOPs are a good fit for hahobot's existing skills + context system, but they have not yet been formalized as first-class built-in workflow skills. |
| GenericAgent-style skill accumulation | `watchlist` | hahobot already has workspace/builtin skills plus `/skill search|install|list|update`, but it still lacks a local "derive successful execution into a reusable skill" loop. |
| GenericAgent layered memory semantics | `synced` | hahobot already separates conversation archive, `MEMORY.md`, `PROFILE.md`, and `INSIGHTS.md`, with Dream + archive sidecars providing a stronger implementation than GenericAgent's simpler layered-memory framing. |
| Hermes-inspired workspace wiki skill | `local_extension` | Built-in `llm-wiki` treats the repo itself as a local concept/config/architecture wiki, using docs + code + tests as the evidence chain without adding another runtime service. |
| Persona / companion workflow | `local_extension` | `PROFILE.md`, `INSIGHTS.md`, `STYLE.md`, `LORE.md`, companion commands, SillyTavern imports, voice overrides, and scene generation are local-first features. |
| Memory architecture | `local_extension` | Dream maintenance, archive sidecars, Mem0 backend/shadow-write, and structured profile/insight hygiene go beyond upstream nanobot. |
| Gateway/admin/runtime ops | `local_extension` | Admin UI, `/status`, Star-Office push, companion doctor, runtime doctor, session inspection, and gateway-backed `/session` / `/repo` / `/review` / `/compact` controls are local operational surfaces. |
| Extension model | `local_extension` | Skills, MCP, built-in companion helpers, and `ExternalHookBridge` are the main extension surfaces; there is no separate plugin framework today. |
| Future upstream channel/provider churn | `watchlist` | Re-audit `channels/`, `providers/`, `cron/`, `agent/hook.py`, `config/schema.py`, and runtime doctor whenever upstream lands new runtime toggles or transport behavior. |

## Already Synced From Upstream nanobot

These are the upstream-facing items that are already present locally and should normally be treated
as "do not re-port unless upstream changes again":

- Telegram streamed reply throttling is config-backed through `channels.telegram.streamEditInterval`.
- Discord streamed final replies use the same edit-then-finalize model and keep related UX knobs
  together.
- Cron job state keeps persisted last-run status plus bounded run-history records instead of
  dropping manual/external execution context on reload.
- Hook composition supports explicit `reraise` semantics while preserving compatibility behavior.
- Direct OpenAI reasoning requests use Responses-first routing with compatibility fallback.
- Runtime tool hints format shell commands more robustly, including quoted paths and repeated calls.
- Shell exec passthrough remains explicit through `tools.exec.allowedEnvKeys`, and local admin/docs
  surfaces expose that knob instead of hiding it in raw JSON only.
- Interrupted turns are recovered before the next request: runtime checkpoints are replayed,
  plain-text user prompts are persisted up front, and orphaned early-persisted user turns are
  closed with an interruption placeholder instead of leaving illegal session tails.
- Proactive auto-compact skips sessions that still have an active agent task, so long-running
  turns are not archived out from under themselves.
- Provider request sanitation recovers a trailing assistant message as a user message when that is
  the only non-system content left after alternation repair.
- Web search supports Brave, SearXNG, and DuckDuckGo, and DuckDuckGo searches are serialized at the
  tool-runner layer when concurrent tool execution is enabled.
- `agents.defaults.disabledSkills` excludes selected skills from main-agent and subagent summaries.
- Idle session auto compact is available through `agents.defaults.idleCompactAfterMinutes`, with
  the legacy `sessionTtlMinutes` alias still accepted on load.
- MCP server configs support `enabledTools` so one server can register all, none, or only a named
  subset of wrapped/raw MCP tools.
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
- `GenericAgent` is treated as an ideas upstream, not a strict structural upstream: hahobot keeps
  richer runtime surfaces such as multi-channel delivery, admin/status pages, review/doctor
  commands, MCP wiring, and hot-reloadable tool/runtime policy instead of converging on a
  deliberately minimal single-loop architecture.
- Admin, gateway status, Star-Office integration, companion doctor, and local session inspection
  are first-class local ops features even when upstream does not have equivalents.
- Hermes-style dashboard/webui is intentionally not mirrored as a second UI stack; equivalent local
  operational surfaces stay in the existing gateway admin and `/status` endpoints, and related
  page layouts are folded into the local Jinja-admin shell instead of shipping a separate SPA.
- Gateway chat surfaces intentionally expose local `/session`, `/repo`, `/review`, and `/compact`
  controls even though upstream parity is tracked primarily at the runtime/tool layer.
- Extension priority is currently `skills + MCP + hook bridge`; do not add a separate plugin
  framework unless there is a concrete gap those surfaces cannot cover cleanly.

## Watchlist For Next Upstream Sync

- Re-check upstream `channels/*` when new transport defaults, streaming semantics, or multi-instance
  behavior changes land.
- Re-check upstream `providers/*` when request routing, retry behavior, or error surfaces change.
- Re-check upstream `cron/service.py` and `cron/types.py` whenever run-state persistence or wake-up
  semantics change.
- Re-check upstream `agent/hook.py`, `agent/runner.py`, and related tests whenever lifecycle
  contracts move.
- Re-check upstream `config/schema.py` only when behavior changes; avoid meaningless field-order
  churn.
- Re-check docs/admin/AGENTS together whenever an upstream config toggle becomes user-visible in the
  local runtime.
- Re-check `lsdefine/GenericAgent` when its planning SOPs, memory-management SOPs, skill
  accumulation flow, or autonomous scheduler meaningfully change, and decide whether hahobot should
  adopt the idea through local skills / Dream / heartbeat / admin surfaces rather than copying the
  implementation verbatim.

## Update Checklist

When syncing from upstream `nanobot`, or when recording a meaningful `GenericAgent`
adoption/divergence decision, update this file in the same patch:

1. Identify whether the upstream change is `synced`, `local_extension`, `intentional_divergence`,
   or `watchlist`.
2. Record the concrete local surface that now owns the behavior: code path, command, config field,
   or doc section.
3. If local behavior intentionally differs, write the reason here instead of leaving the difference
   implicit.
4. If a change affects user-visible behavior or contributor workflow, update `README.md`,
   `README_ZH.md`, and `AGENTS.md` together.
