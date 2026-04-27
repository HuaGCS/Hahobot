# Upstream Parity

This file is the living parity ledger for the local `hahobot` fork.

It is inspired by the `PARITY.md` style used in `soongenwong/claudecode`: keep one explicit
document for "already matched", "intentionally different", and "still worth checking" instead of
burying that state across commit messages and chat logs.

## Scope

Primary upstreams tracked here:

- `HKUDS/nanobot`
- `lsdefine/GenericAgent`
- `thedotmack/claude-mem`

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
- `claude-mem` is tracked as a memory-architecture inspiration source. Hahobot adopts compatible
  ideas such as structured observations, progressive-disclosure recall, file timelines, and private
  tags through its existing archive/Dream/admin surfaces rather than copying the AGPL-licensed
  implementation or Claude Code hook layout.

This file therefore records both:

- direct upstream parity work for `nanobot`
- explicit adoption / divergence decisions for `GenericAgent` and `claude-mem` where the ideas are
  relevant to hahobot's local runtime

## Status Legend

- `synced`: behavior already exists locally, possibly with extra local wiring or tests
- `local_extension`: local-only capability; upstream parity is not the main goal
- `intentional_divergence`: local behavior deliberately differs from upstream
- `watchlist`: area should be re-checked during the next upstream sync

## Latest Audit

- `nanobot`: re-checked against upstream `main` at `ca66dd8c` (`2026-04-27`), covering the
  post-`3441d5f8` changes around `ask_user`, finite LLM request timeouts, session timestamp anchors,
  safer `tools.exec.pathAppend`, local/LAN provider connection behavior, configurable consolidation
  ratio, proactive delivery/thread context continuity, Feishu group-thread isolation, video/media
  envelopes, lazy document parser imports, and provider factory ownership. No direct bulk merge was
  taken in this audit; the concrete candidates are recorded below.
- `GenericAgent`: re-checked against upstream `main` at `db6bf00d` (`2026-04-27`), with the new
  delta mostly in file-read UX (`PARTIAL` hints), file paste/upload handling, inherited proxy-env
  hygiene for WeChat-style long polling, SSE residual-block cleanup, ask-user display/prompt polish,
  and reasoning/thinking conversion robustness rather than new skill-governance primitives.
- `claude-mem`: re-checked from `thedotmack/claude-mem` at latest visible GitHub commit `49ab404`
  (`2026-04-27`). The previously adopted private tags, structured observations, progressive recall,
  and optional rebuildable SQLite FTS archive index remain the relevant local borrow points; the
  service/Chroma/vector-memory architecture remains an intentional divergence.

## Current Snapshot

| Area | Status | Local State |
| --- | --- | --- |
| Tool/runtime policy | `synced` | Runtime tool enable/disable checks are centralized, hot reload can add/remove tool families, doctor output reuses the same policy layer, and shell env passthrough stays explicit through `tools.exec.allowedEnvKeys`. |
| Read-only self inspection tool | `synced` | Local `self_inspect` exposes a JSON snapshot of runtime/session/tool/subagent state, bound to the actual session key and intentionally read-only instead of porting upstream's mutable `self.py` surface. |
| Notebook editing tool | `synced` | Local `notebook_edit` supports bounded `.ipynb` cell replace/insert/delete flows for the main agent and `spawn(mode="implement")` workers without porting upstream's broader file-state machinery. |
| Session persistence durability | `synced` | Session full rewrites now use atomic replace, corrupt JSONL can be repaired for load/list flows, and recovered sessions are forced through the next clean rewrite instead of silently staying in a broken state. |
| Turn recovery / idle compact safety | `synced` | Session recovery now restores runtime checkpoints before the next request, `/stop` cancellation materializes the latest runtime checkpoint immediately instead of waiting for the next message, plain-text user turns are persisted early so crashes do not lose the prompt, orphaned pending user turns are closed cleanly, and proactive auto-compact still skips sessions with an in-flight task. |
| Subagent follow-up persistence | `synced` | Subagent announce messages now carry task metadata, are persisted into session history before prompt assembly, deduped by `subagent_task_id`, and avoid double-injecting the same follow-up as both history and current message. |
| Hook lifecycle semantics | `synced` | Hook fan-out supports `reraise` semantics and keeps compatibility behavior for legacy hooks. |
| OpenAI direct reasoning routing | `synced` | Direct OpenAI GPT-5/o-series requests prefer Responses API and fall back to Chat Completions only for compatibility errors. |
| Responses compatibility circuit breaker | `synced` | Repeated Responses-API compatibility failures now open a short-lived per-`(model, reasoningEffort)` circuit before re-probing automatically, so direct OpenAI fallback does not keep paying the same failing probe cost every turn. |
| Provider thinking toggles | `synced` | `ProviderSpec.thinking_style` now owns DashScope, DeepSeek, VolcEngine/BytePlus, and MiniMax thinking wire formats; legacy assistant turns receive empty `reasoning_content` when thinking mode is enabled mid-session. |
| Anthropic adaptive / Opus reasoning | `synced` | `reasoningEffort=adaptive` maps to Anthropic `thinking={"type":"adaptive"}`, Opus 4.7 requests omit deprecated `temperature`, and tool-result image blocks are normalized before Anthropic submission. |
| Anthropic message alternation recovery | `synced` | Anthropic request normalization now also enforces leading-user, strips trailing assistant prefill, and recovers the empty-array edge case without rerouting `tool_use`-carrying assistant blocks into invalid user turns. |
| Tool hint formatting | `synced` | Exec hints handle quoted paths, path abbreviation, and duplicate collapse in one formatter. |
| Exec `pathAppend` safety | `synced` | Local POSIX `tools.exec.pathAppend` now passes the appended path through `HAHOBOT_PATH_APPEND` instead of interpolating the raw config value into shell syntax, while Windows still appends through the subprocess env. |
| Finite LLM request timeout | `synced` | `AgentRunner` wraps provider calls and finalization retries with a finite timeout (`HAHOBOT_LLM_TIMEOUT_S`, legacy `NANOBOT_LLM_TIMEOUT_S`, default 300s, `0` disables) so hung gateways return a timeout error instead of starving a session lock. |
| Session timestamp anchors in model context | `synced` | `Session.get_history(..., include_timestamps=True)` can annotate user/assistant text with `[Message Time: ...]`, and normal prompt assembly plus compaction probes use that timestamped view while persisted session format stays unchanged. |
| Ask-user clarification tool | `watchlist` | Upstream added an `ask_user` tool plus CLI/WebUI choice rendering. Local hahobot should only adopt this after mapping the UX across CLI, gateway channels, buttons, and session-lock semantics. |
| Provider request sanitization | `synced` | Role alternation repair now recovers a trailing assistant message as `user` when otherwise only `system` content would remain, preventing empty/invalid provider requests after assistant-scoped injections. |
| Local/LAN provider transport behavior | `watchlist` | Upstream tightened local endpoint detection and disables HTTP keepalive for local/LAN endpoints to avoid stale socket failures. Local provider error reporting is strong, but transport pooling policy should be rechecked for Ollama/vLLM/custom LAN endpoints. |
| Provider factory ownership | `watchlist` | Upstream moved provider snapshot/refresh creation into a factory layer and subsystem owners. Local provider pool/runtime config code is richer; borrow only if it reduces duplication without weakening hot reload. |
| On-demand context microcompact | `synced` | Older tool results are compacted before the next model call when a long tool-heavy turn would otherwise crowd out system prompt memory or the freshest working context. |
| Skill filtering / idle compact / MCP tool filtering | `synced` | `agents.defaults.disabledSkills`, `agents.defaults.idleCompactAfterMinutes` (plus `sessionTtlMinutes` alias), and `tools.mcpServers.<name>.enabledTools` are wired through local runtime, tests, and docs. |
| MCP resources / prompts as tool surfaces | `synced` | MCP connections already wrap remote tools, resources, and prompts into native hahobot tool entries, keeping non-mutating resource/prompt calls read-only while preserving local timeout/error handling and `enabledTools` filtering. |
| Cron state / scheduler behavior | `synced` | Cron preserves last-run status plus merged run history on disk, and the workspace scheduler periodically wakes to reload external `cron/jobs.json` edits via `gateway.cron.maxSleepMs`. |
| Proactive delivery session continuity | `synced` | Cross-session `message` tool sends, cron delivery, and heartbeat notify now record delivered assistant text into the target `channel:chat` session so later user replies can see what was actually sent. |
| Configurable consolidation ratio | `watchlist` | Upstream exposes a bounded `consolidationRatio` for token compaction targets. Local compaction has richer archive/Dream behavior; adding the knob may help large-context users if docs/admin make the tradeoff clear. |
| Telegram / Discord streaming | `synced` | Telegram uses configurable `channels.telegram.streamEditInterval`; Discord keeps edit-based streaming enabled by default, and the related runtime knobs are exposed in local schema/docs/admin surfaces. |
| Telegram inline buttons | `synced` | `channels.telegram.inlineKeyboards` can render `OutboundMessage.buttons` as Telegram inline keyboards, caps callback payload bytes, and falls back to inline text when native keyboards are disabled. |
| WebSocket server channel | `synced` | Local runtime already ships `channels.websocket`, including tokenless local mode, optional `tokenIssuePath` / `tokenIssueSecret`, and the simple `ready` / `message` / `delta` / `stream_end` frame contract. |
| Legacy rename compatibility | `synced` | `nanobot` CLI/module/import compatibility stays live, and default config fallback is preserved. |
| Config fallback behavior | `intentional_divergence` | When no config path is passed, hahobot checks `~/.hahobot/config.json` first, then copies `~/.nanobot/config.json` into the hahobot location instead of migrating in place. |
| Web search backend mix | `synced` | Built-in web search now supports Brave, SearXNG, and DuckDuckGo; DuckDuckGo runs as an exclusive tool so concurrent tool batches do not group multiple searches together. |
| Search provider breadth | `watchlist` | Upstream now also carries Kagi search support; local runtime still intentionally limits `tools.web.search.provider` to Brave, SearXNG, and DuckDuckGo until there is real demand for another paid backend plus matching config/admin/docs wiring. |
| MCP transient reconnect retry | `watchlist` | Upstream now retries one connection-class MCP failure after a short backoff; local MCP wrappers already distinguish true task cancellation from server-side `CancelledError`, but they still surface the first broken-pipe/closed-resource failure directly. Re-check if bridge restarts or transient MCP reconnects become noisy in practice. |
| OpenAI-compatible API file inputs | `synced` | `hahobot serve` now accepts both JSON and `multipart/form-data`, extracts text-like uploaded or inline base64 file payloads into the prompt, and emits stable placeholders for binary/image attachments while keeping the direct API path single-message and non-streaming. |
| OpenAI-compatible API streaming | `intentional_divergence` | Upstream now supports SSE when `stream=true`; local `hahobot serve` intentionally stays non-streaming until the API contract is deliberately expanded across docs, tests, and client expectations together. |
| Memory/history pollution caps | `synced` | Recent-history prompt injection, raw archive fallback, and consolidated history entries now have explicit size caps so failed summarization or oversized legacy entries cannot bloat every future prompt. |
| claude-mem-style private tags | `synced` | `<private>...</private>` blocks are stripped before session persistence, history archives, `HISTORY.md` entries, and Mem0 writes so marked secrets do not become long-term memory. |
| claude-mem-style observations | `synced` | Archive sidecars now include observation metadata (`type`, `facts`, `concepts`, `files`, title/subtitle/narrative) derived from summarized turns and tool traces. |
| Progressive memory recall | `synced` | `history_search` returns compact observation indexes, `history_timeline` gives chronological/file context, and `history_expand` remains the explicit transcript expansion step. |
| Document read support | `synced` | `read_file` extracts text from `.docx`, `.xlsx`, and `.pptx` files through a small local OOXML parser while keeping images and text files on the existing path. |
| Lazy document parser imports | `watchlist` | Upstream lazy-loads heavier document parser dependencies. Local OOXML parsing is already small, but startup/import cost should be checked before adding more document formats. |
| Video/media envelope parity | `watchlist` | Upstream added Telegram/WebSocket video and WebUI media rendering. Local QQ already handles local `.mp4` uploads and image/voice paths; Telegram/WebSocket/video parity should be considered only with channel-specific delivery tests. |
| Transcription language hints | `synced` | `channels.transcriptionLanguage` validates ISO-639-like language hints, hot-reloads into running channels, and is passed to Groq/OpenAI transcription requests. |
| Mid-turn follow-up injection | `watchlist` | Local dispatch stays per-session serialized and crash-safe, but it does not splice new user turns into an already running session; upstream-style active-turn injection would touch locks, checkpoints, streaming, `/stop`, and compaction semantics together. |
| Dream skill discovery automation | `intentional_divergence` | Upstream lets Dream discover/write reusable skills automatically; local skill accumulation stays operator-visible and reviewable through `/skill derive` instead of unattended Dream promotion. |
| GenericAgent-style SOP workflow | `synced` | Hahobot now ships built-in workflow skills (`workflow-core`, `plan`, `verify`), subagent execution modes (`explore` / `implement` / `verify`), and persisted `working_checkpoint` state across session/admin/status surfaces. |
| GenericAgent-style skill accumulation | `synced` | Hahobot now supports local skill derivation through `/skill derive <name> [brief] [--force]`, turning recent successful session workflow into a reusable workspace skill draft. |
| Skill lifecycle hygiene / prompt budget control | `local_extension` | Runtime skill summaries are now query-aware top-k views, `supersedes` can hide replaced skills from the shared summary, `/skill supersede` plus `remove` / `clear` maintain that metadata explicitly, and `/skill lint` reports overlap or missing supersedes targets before local skill growth turns chaotic. |
| Skill usage writeback | `local_extension` | Workspace skill `last_used` / `success_count` now update from real runtime `read_file` usage instead of staying frozen at derive-time defaults. |
| GenericAgent layered memory semantics | `synced` | hahobot already separates conversation archive, `MEMORY.md`, `PROFILE.md`, and `INSIGHTS.md`, with Dream + archive sidecars providing a stronger implementation than GenericAgent's simpler layered-memory framing. |
| Hermes-inspired workspace wiki skill | `local_extension` | Built-in `llm-wiki` treats the repo itself as a local concept/config/architecture wiki, using docs + code + tests as the evidence chain without adding another runtime service. |
| Persona / companion workflow | `local_extension` | `PROFILE.md`, `INSIGHTS.md`, `STYLE.md`, `LORE.md`, companion commands, SillyTavern imports, voice overrides, and scene generation are local-first features. |
| Memory architecture | `local_extension` | Dream maintenance, archive sidecars, Mem0 backend/shadow-write, and structured profile/insight hygiene go beyond upstream nanobot. |
| claude-mem SQLite FTS archive index | `synced` | Hahobot now supports `memory.archive.indexBackend="sqlite"` as a persona-local derived FTS cache for `history_search` / `history_timeline`, rebuildable with `hahobot memory index rebuild` from JSON sidecars. |
| claude-mem Chroma/service backend | `intentional_divergence` | Hahobot keeps markdown/archive JSON sidecars as the source of truth plus optional Mem0 instead of adopting a separate Chroma/vector memory service; local recall remains inspectable and persona-scoped. |
| Gateway/admin/runtime ops | `local_extension` | Admin UI, `/status`, Star-Office push, companion doctor, runtime doctor, session inspection, and gateway-backed `/session` / `/repo` / `/review` / `/compact` controls are local operational surfaces. |
| Standalone browser WebUI | `intentional_divergence` | Upstream now ships a separate browser chat SPA over WebSocket; local web surfaces still stay in the existing gateway admin and `/status` shell rather than adopting a second chat frontend stack. |
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
| Skill lifecycle hygiene after derivation | `local_extension` | Derived drafts now seed local lifecycle metadata, runtime summaries use query-aware top-k selection plus `supersedes` hiding, and `/skill lint` reports overlap / missing supersedes targets | This is explicitly aimed at avoiding skill explosion and ambiguous skill choice without copying Hermes-style unattended self-learning loops. |
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

## Borrow Candidates From 2026-04-27 Audit

High-priority candidates now implemented locally:

- **Exec `pathAppend` hardening**: POSIX path append is env-backed through `HAHOBOT_PATH_APPEND`
  instead of raw shell interpolation.
- **LLM request timeout**: provider awaits are bounded by `HAHOBOT_LLM_TIMEOUT_S` /
  `NANOBOT_LLM_TIMEOUT_S` with a 300s default and `0` escape hatch.
- **Session timestamp anchors**: prompt history and compaction probes can include persisted message
  timestamps without changing the session file format.
- **Proactive session continuity**: cross-session message sends plus cron/heartbeat delivery record
  delivered assistant text into the target channel session.

Medium-priority candidates:

- **Local/LAN keepalive policy**: re-check custom/Ollama/vLLM endpoint detection and disable HTTP
  keepalive only where it demonstrably reduces stale socket failures.
- **Configurable consolidation ratio**: expose a bounded `agents.defaults.consolidationRatio` only
  if it can be explained in docs/admin and tested against archive/Dream behavior.
- **Lazy document imports and media envelopes**: useful quality/performance improvements, but should
  be ported per channel/filetype with focused tests rather than as broad WebUI parity.

Lower-priority / deliberate caution:

- **`ask_user` tool**: attractive for clarification flows, but adopting it safely requires a channel
  UX contract for CLI, Telegram/Discord/Slack/Feishu/etc., timeout/cancel behavior, and session-lock
  handling. Keep on watchlist until that cross-channel contract is designed.
- **Provider factory refactor**: borrow only if it materially reduces local provider-pool/runtime
  duplication; avoid churn that makes hahobot's richer hot-reload paths harder to reason about.

## GenericAgent Adoption Notes

- Hahobot now covers the two previously open GenericAgent gaps that motivated adding it as an
  upstream here: first-class workflow SOP skills and local skill derivation from successful runs.
- Local follow-up work on skill accumulation now intentionally adds lifecycle guardrails instead of
  pushing further toward Hermes-style unattended self-growth:
  - derived skills seed explicit lifecycle metadata
  - runtime skill exposure is query-scoped and top-k bounded
  - `supersedes` can hide replaced skills from the shared summary
  - `/skill lint` stays read-only and operator-visible
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
- The main agent now has a read-only `self_inspect` tool that exposes runtime/session/tool/subagent state without allowing in-band self-mutation.
- Shell exec passthrough remains explicit through `tools.exec.allowedEnvKeys`, and local admin/docs
  surfaces expose that knob instead of hiding it in raw JSON only.
- The runtime now supports bounded `.ipynb` cell edits through `notebook_edit`, and implement-mode
  subagents receive the same tool while explore/verify workers stay read-only.
- Session storage now keeps atomic full rewrites for rewrite-heavy paths and can recover usable
  history from corrupt JSONL during load/list flows.
- Interrupted turns are recovered before the next request: runtime checkpoints are replayed,
  plain-text user prompts are persisted up front, and orphaned early-persisted user turns are
  closed with an interruption placeholder instead of leaving illegal session tails.
- Subagent completion follow-ups are written into durable session history before the next model
  call, keyed by `subagent_task_id` so retries/recovery do not duplicate the same announce block.
- Proactive auto-compact skips sessions that still have an active agent task, so long-running
  turns are not archived out from under themselves.
- Older tool results are compacted on demand before the next model call when long turns threaten
  prompt budget.
- Provider request sanitation recovers a trailing assistant message as a user message when that is
  the only non-system content left after alternation repair.
- Direct OpenAI Responses fallback now uses a short-lived compatibility circuit breaker so repeated
  unsupported-probe failures do not recur every turn.
- Provider-specific thinking toggles are centralized through `ProviderSpec.thinking_style`, covering
  DashScope `enable_thinking`, DeepSeek/VolcEngine/BytePlus `thinking.type`, MiniMax
  `reasoning_split`, and DeepSeek's legacy assistant `reasoning_content` backfill.
- GitHub Copilot GPT-5/o-series routing uses the Responses path rather than falling back to an
  incompatible chat-completions probe.
- Anthropic message normalization now matches the stricter upstream invariants: no leading
  assistant, no trailing assistant prefill, and no empty message array fallback unless rerouting
  would create an invalid `tool_use`-inside-user request.
- Anthropic requests already support `reasoningEffort=adaptive`, mapping it to Anthropic adaptive
  thinking without inflating token budgets or leaving incompatible temperature handling behind.
- Anthropic Opus 4.7 requests omit the now-rejected `temperature` parameter, and `tool_result`
  blocks convert nested `image_url` content before API submission.
- Memory/history prompt pollution is bounded: recent history, LLM-written summaries, and raw archive
  fallbacks all have defensive character caps before they can enter future prompts.
- `/stop` cancellation now materializes the latest runtime checkpoint into session history
  immediately instead of waiting for the next inbound turn to trigger recovery.
- Web search supports Brave, SearXNG, and DuckDuckGo, and DuckDuckGo searches are serialized at the
  tool-runner layer when concurrent tool execution is enabled.
- The built-in WebSocket server channel is available through `channels.websocket`.
- Telegram inline keyboards are available through `channels.telegram.inlineKeyboards`, with safe
  callback-data truncation and text fallback when keyboards are disabled.
- `read_file` can extract text from `.docx`, `.xlsx`, and `.pptx` documents without changing the
  binary/image delivery contract.
- Voice transcription can pass optional ISO-639 hints through `channels.transcriptionLanguage`, and
  the field hot-reloads alongside `channels.transcriptionProvider`.
- The built-in OpenAI-compatible API accepts JSON or multipart requests with inline/uploaded file
  payloads; text-like attachments are extracted into prompt context, while binary/image inputs are
  kept as stable placeholders on the direct path.
- `agents.defaults.disabledSkills` excludes selected skills from main-agent and subagent summaries.
- Idle session auto compact is available through `agents.defaults.idleCompactAfterMinutes`, with
  the legacy `sessionTtlMinutes` alias still accepted on load.
- MCP server configs support `enabledTools` so one server can register all, none, or only a named
  subset of wrapped/raw MCP tools.
- MCP server connections already expose tools, resources, and prompts through the same local tool
  registry, with resource/prompt wrappers kept read-only.
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
  page layouts are folded into the local Jinja-admin shell instead of shipping upstream's separate
  browser chat SPA.
- Dream-driven skill accumulation stays operator-reviewed through `/skill derive`; local default is
  not to let Dream auto-write or auto-promote new skills in the background.
- Skill hygiene also stays operator-reviewed: local runtime may hide superseded skills from prompt
  summaries, but it does not auto-delete, auto-merge, or silently rewrite skills in the
  background.
- The local OpenAI-compatible API intentionally remains non-streaming even though upstream added SSE
  support for `stream=true`; keeping the contract stable matters more than feature parity there.
- Gateway chat surfaces intentionally expose local `/session`, `/repo`, `/review`, and `/compact`
  controls even though upstream parity is tracked primarily at the runtime/tool layer.
- Extension priority is currently `skills + MCP + hook bridge`; do not add a separate plugin
  framework unless there is a concrete gap those surfaces cannot cover cleanly.

## Watchlist For Next Upstream Sync

- The 2026-04-27 high-priority nanobot candidates (`pathAppend` hardening, finite LLM request
  timeout, session timestamp anchors, proactive delivery continuity) are now synced; next pass should
  focus on optional consolidation-ratio/media/provider-factory polish.
- Re-check GenericAgent's file-read `PARTIAL` hints and proxy-env hygiene when touching local
  `read_file`, WebSocket/SSE, or Weixin/WeCom long-poll code; the ideas are portable even if the
  frontend implementations are not.
- Re-check upstream `channels/*` when new transport defaults, streaming semantics, or multi-instance
  behavior changes land.
- Re-check upstream `providers/*` when request routing, retry behavior, or error surfaces change.
- Re-check upstream `session/manager.py` whenever JSONL durability or recovery semantics change
  again; local append-only persistence means the rewrite/repair tradeoffs are slightly different.
- Re-check upstream multimodal/WebUI upload and `channels.websocket` media-envelope work only if the
  local gateway/admin UX grows a matching browser-chat surface; hahobot currently treats upstream's
  standalone WebUI as intentional divergence.
- Re-check upstream search-provider additions (for example Kagi) against local config/admin/docs
  before expanding `tools.web.search.provider`.
- Re-check upstream `agent/tools/mcp.py` if transient `ClosedResourceError` / broken-pipe failures
  become common enough to justify the same one-shot reconnect retry locally.
- Re-check upstream tool-context isolation changes in `agent/tools/cron.py`, `message.py`, and
  `spawn.py` if concurrent cross-session routing leakage appears; local runtime still relies on
  per-turn `set_context()` updates instead of ContextVar-backed tool state.
- Re-check upstream `cron/service.py` and `cron/types.py` whenever run-state persistence or wake-up
  semantics change.
- Re-check upstream `agent/hook.py`, `agent/runner.py`, and related tests whenever lifecycle
  contracts move.
- Re-check any upstream active-turn follow-up injection carefully before porting it locally; the
  interaction surface spans session locks, checkpoint recovery, streamed deltas, `/stop`, and
  idle-compaction safety.
- Re-check upstream `config/schema.py` only when behavior changes; avoid meaningless field-order
  churn.
- Re-check docs/admin/AGENTS together whenever an upstream config toggle becomes user-visible in the
  local runtime.
- Re-check upstream Dream/skill-discovery automation only if it becomes an operator-reviewable flow;
  local default should stay explicit `/skill derive`, not unattended skill promotion.
- Re-check upstream `agent/tools/self.py` only if there is a concrete reason to expand beyond the
  current read-only `self_inspect` surface; keep local behavior bounded to inspection, not
  self-modification.
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
