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
| GenericAgent-style SOP workflow | `synced` | Hahobot now ships built-in workflow skills (`workflow-core`, `plan`, `verify`), subagent execution modes (`explore` / `implement` / `verify`), and persisted `working_checkpoint` state across session/admin/status surfaces. |
| GenericAgent-style skill accumulation | `synced` | Hahobot now supports local skill derivation through `/skill derive <name> [brief] [--force]`, turning recent successful session workflow into a reusable workspace skill draft. |
| GenericAgent layered memory semantics | `synced` | hahobot already separates conversation archive, `MEMORY.md`, `PROFILE.md`, and `INSIGHTS.md`, with Dream + archive sidecars providing a stronger implementation than GenericAgent's simpler layered-memory framing. |
| Hermes-inspired workspace wiki skill | `local_extension` | Built-in `llm-wiki` treats the repo itself as a local concept/config/architecture wiki, using docs + code + tests as the evidence chain without adding another runtime service. |
| Persona / companion workflow | `local_extension` | `PROFILE.md`, `INSIGHTS.md`, `STYLE.md`, `LORE.md`, companion commands, SillyTavern imports, voice overrides, and scene generation are local-first features. |
| Memory architecture | `local_extension` | Dream maintenance, archive sidecars, Mem0 backend/shadow-write, and structured profile/insight hygiene go beyond upstream nanobot. |
| Gateway/admin/runtime ops | `local_extension` | Admin UI, `/status`, Star-Office push, companion doctor, runtime doctor, session inspection, and gateway-backed `/session` / `/repo` / `/review` / `/compact` controls are local operational surfaces. |
| Extension model | `local_extension` | Skills, MCP, built-in companion helpers, and `ExternalHookBridge` are the main extension surfaces; there is no separate plugin framework today. |
| Future upstream channel/provider churn | `watchlist` | Re-audit `channels/`, `providers/`, `cron/`, `agent/hook.py`, `config/schema.py`, and runtime doctor whenever upstream lands new runtime toggles or transport behavior. |

## GenericAgent Detailed Matrix

This section tracks the more granular adoption/divergence decisions for `lsdefine/GenericAgent`.
It is intentionally finer-grained than the top-level snapshot so we can tell which ideas are
already productized locally and which ones are still only partially reflected in hahobot.

| GenericAgent Theme | Status | Local Surface | Remaining Gap / Notes |
| --- | --- | --- | --- |
| Workflow SOP packaged as built-in guidance | `synced` | Bundled `workflow-core`, `plan`, `verify`, and `skill-derive` skills under `hahobot/skills/` | Hahobot keeps SOPs as skills instead of hard-wiring a GenericAgent-style monolithic agent loop. |
| Explicit plan / verify execution roles | `synced` | `spawn(mode="explore" \| "implement" \| "verify")`, mode-aware subagent tool registry, and mode-specific subagent prompt sections | Hahobot enforces the boundary through tool availability, not only prompt wording. |
| Working-state checkpoint during long turns | `synced` | `working_checkpoint` metadata persisted by runner/checkpoint runtime and rendered in CLI sessions, admin sessions, and browser `/status` | The checkpoint is intentionally lightweight runtime metadata, not a second long-term memory store. |
| User-visible "current step / next step" runtime visibility | `synced` | Browser `/status` recent-task card, admin session list, and `hahobot sessions show` output | GenericAgent keeps this mostly inside loop state; hahobot surfaces it to local ops pages as well. |
| Skill accumulation from successful executions | `synced` | `/skill derive <name> [brief] [--force]` writes reviewable drafts under `<workspace>/skills/<slug>/SKILL.md` | The flow is deterministic and local-first; it does not auto-publish or auto-enable skills without operator review. |
| Layered memory semantics | `synced` | `SOUL.md`, `USER.md`, `PROFILE.md`, `INSIGHTS.md`, `memory/MEMORY.md`, history archive, Dream, optional Mem0 | Local implementation is richer than GenericAgent's framing and intentionally not collapsed back down. |
| Memory-layer terminology ownership | `synced` | `README.md`, `README_ZH.md`, `AGENTS.md`, Dream templates, admin persona page, and `/status` all use the same split: `PROFILE.md` for stable user facts/preferences, `INSIGHTS.md` for proven collaboration guidance | This keeps operator-facing docs and runtime surfaces aligned instead of letting each page invent its own labels. |
| Dream prompt sees current memory layers plus metadata summaries | `synced` | Dream prompt injects current `PROFILE.md` / `INSIGHTS.md` contents and their metadata summaries before reflection/edit phases | Reflective maintenance therefore works from the same layered-memory framing shown to operators elsewhere. |
| Admin persona page surfaces memory-layer metadata | `synced` | Persona detail page shows `PROFILE.md` / `INSIGHTS.md` metadata cards, confidence/verification counts, and example `hahobot-meta` usage | The admin surface stays read-only for metadata summary; it does not introduce a second metadata schema. |
| `/status` surfaces memory-layer summary without breaking machine JSON | `synced` | Chat `/status` now includes the active session persona's memory-layer summary; browser `/status` shows the recent persona's `PROFILE.md` / `INSIGHTS.md` summary card while JSON `/status` remains unchanged | Status is intentionally summary-only and operational; it does not dump full persona memory files into the status endpoint. |
| Structured write rules for profile/insight bullets | `synced` | Dream phase docs, admin/help text, and `/status` guidance all converge on `<!-- hahobot-meta: confidence=... last_verified=YYYY-MM-DD -->` with legacy `(verify)` markers kept compatibility-only | The rule is to touch one canonical bullet per fact/pattern rather than accumulating duplicate variants. |
| Memory-maintenance SOP as background hygiene | `synced` | Dream phase 1/2 reflection, idle compact, archive sidecars, metadata hygiene for profile/insight bullets | The local maintenance path is heavier-weight than GenericAgent's simpler autonomous memory loop. |
| Narrow autonomous background workflow loop | `local_extension` | Heartbeat, cron scheduler, Dream system job, gateway runtime status, and Star-Office push | Hahobot splits these responsibilities across cron / Dream / heartbeat rather than copying one GenericAgent autonomous scheduler abstraction. |
| Minimal single-surface local architecture | `intentional_divergence` | Hahobot keeps CLI, gateway, admin, status pages, channel adapters, and OpenAI-compatible API together | Richer operational surfaces are treated as part of the product, not as accidental complexity to remove for parity. |
| Skill derivation beyond draft creation | `watchlist` | Current flow stops at local draft creation and explicit overwrite via `--force` | Re-check whether we want later steps such as review helpers, packaging shortcuts, or admin UI promotion once there is real usage pressure. |
| Autonomous self-improvement beyond operator-visible jobs | `watchlist` | Current background behavior is bounded to Dream / heartbeat / cron and explicit config-driven jobs | Re-check only if GenericAgent's unattended improvement loop becomes concrete enough to justify a safe local analog. |

## GenericAgent Adoption Notes

- Hahobot now covers the two previously open GenericAgent gaps that motivated adding it as an
  upstream here: first-class workflow SOP skills and local skill derivation from successful runs.
- The adopted parts were intentionally mapped onto existing hahobot surfaces:
  - skills for SOP distribution
  - subagent modes for bounded role separation
  - `working_checkpoint` for in-flight task state
  - `/skill derive` for operator-reviewed skill accumulation
- Memory-layer visibility is now also aligned end-to-end:
  - Dream prompt sees current `PROFILE.md` / `INSIGHTS.md` plus metadata summaries
  - admin persona page shows the same layers' metadata cards
  - chat and browser `/status` expose summary-only operational views of those layers
  - docs/runtime guidance all converge on the same `hahobot-meta` write contract
- Areas still left on the watchlist are not "missing parity bugs"; they are explicit product
  choices to avoid introducing unattended self-modification or an extra autonomous loop before the
  operational need is proven.

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
