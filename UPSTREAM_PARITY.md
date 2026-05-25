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
- `Dataojitori/nocturne_memory`

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
- `nocturne_memory` is tracked as a memory-architecture inspiration source (MIT-licensed). It is a
  graph-backed long-term memory MCP server (Node→Memory→Edge→Path, URI addressing, per-entry
  disclosure triggers, patch-only updates). Hahobot evaluates its ideas against the local
  file-first memory model: the graph/DB backend and separate service are intentional divergences,
  but individual write-safety/recall ideas such as patch-only memory writes are tracked for
  adoption through the existing Markdown/Dream/Consolidator surfaces.

This file therefore records both:

- direct upstream parity work for `nanobot`
- explicit adoption / divergence decisions for `GenericAgent`, `claude-mem`, and `nocturne_memory`
  where the ideas are relevant to hahobot's local runtime

## Status Legend

- `synced`: behavior already exists locally, possibly with extra local wiring or tests
- `local_extension`: local-only capability; upstream parity is not the main goal
- `intentional_divergence`: local behavior deliberately differs from upstream
- `watchlist`: area should be re-checked during the next upstream sync

## Latest Audit

- `nanobot`: re-checked against upstream `main` at `92f2ff3a` (`2026-05-25`). This pass adopted
  three contract-stable fixes: the non-streaming OpenAI-compat response parser now preserves the
  upstream `tool_call` id when present (instead of always minting a fresh `_short_tool_id`); the
  Responses replay converter dedupes generated `msg_*` / `fc_*` item ids so Codex no longer rejects
  resumed conversations with duplicate `rs_*` ids while `call_id` linkage stays intact; and
  `WebFetchTool` now validates every redirect hop's resolved IP before issuing the next request
  (per-hop SSRF check), rather than only checking the final URL after httpx silently followed
  redirects. The exec-config timeout uncap (config `timeout=0` = no limit), per-subagent sampling
  temperature, OpenAI `apiType` + `extraBody`, transcription `apiBase` normalization, MCP preset
  setup, Codex / OpenAI image-gen providers, Zhipu image-gen, apply-patch edits-only refactor, and
  the various WebUI / settings churn are all reviewed but intentionally skipped or left on the
  watchlist (see borrow-candidates section below).
- `GenericAgent`: re-checked against upstream `main` at `a33b2259` (`2026-05-25`). All visible
  deltas are a QQApp Markdown message-type tweak, a Python <3.10 `from __future__ import
  annotations` compat fix for cost-tracker, and an A3Agent workbench docs link — no runtime or
  memory ideas to adopt this pass.
- `claude-mem`: re-checked against upstream `main` at `c3d2af7c` (`2026-05-21`, release v13.3.0).
  Two new workflow skills (`oh-my-issues` for GitHub issue clustering, `weekly-digests` for
  ISO-week serial narrative) plus an MCP root-config fix and a Codex transcript replay fix after
  the hooks migration. Both new skills are skill-layer ideas that could be ported into hahobot's
  bundled skill set; tracked on watchlist pending operator demand.
- `nocturne_memory`: re-checked against upstream `main` at `68f0ebf3` (`2026-05-24`, release
  v2.5.3). New surface area is "boot URI presets management" (database-backed CRUD over boot URIs,
  promoted from legacy `config.json`) and an "AntiGravity heartbeat" engine for the AntiGravity
  IDE via Chrome DevTools Protocol. Boot URI presets sit on top of nocturne's mandatory boot
  protocol — already an intentional divergence locally — so nothing to adopt there. The
  AntiGravity heartbeat is IDE-integration-specific and outside hahobot's scope.

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
| Tool hint formatting / length control | `synced` | Exec hints handle quoted paths, path abbreviation, duplicate collapse, and hot-reloadable `agents.defaults.toolHintMaxLength` for channels that expose tool-call hints. |
| Exec `pathAppend` safety | `synced` | Local POSIX `tools.exec.pathAppend` now passes the appended path through `HAHOBOT_PATH_APPEND` instead of interpolating the raw config value into shell syntax, while Windows still appends through the subprocess env. |
| Exec stdin isolation | `synced` | `ExecTool._spawn` launches both the POSIX bash and Windows COMSPEC subprocesses with `stdin=asyncio.subprocess.DEVNULL`, so a shell command that reads from stdin returns immediately instead of hanging on the inherited terminal. |
| Tool-call id uniqueness | `synced` | Streaming responses deduplicate reused `tool_call` ids before building `LLMResponse` (some providers reuse one id for parallel calls), and `_sanitize_messages` assigns each `tool_call` within a message a unique normalized id while routing tool results back through a per-id FIFO so duplicate ids cannot create an ambiguous assistant/tool pairing. Non-streaming response parsing now also preserves the original upstream `tool_call` id when present (instead of always minting a fresh `_short_tool_id`), so log correlation and downstream tool-result linkage stay readable. |
| OpenAI Responses replay item id dedup | `synced` | `openai_responses.converters.convert_messages` now routes every assistant `message` and `function_call` item id through `_unique_item_id`, so resumed conversations with duplicate `msg_*` / `fc_*` items no longer get rejected by the Responses API while `call_id` (tool-result linkage) remains untouched. |
| Per-hop WebFetch redirect SSRF check | `synced` | `WebFetchTool` now walks redirect chains manually via `_get_with_safe_redirects`, validating each `Location` against the SSRF policy before issuing the next request. httpx's `follow_redirects=True` could otherwise briefly hit a disallowed intermediate hop even when the final URL passed validation. |
| Finite LLM request timeout | `synced` | `AgentRunner` wraps provider calls and finalization retries with a finite timeout (`HAHOBOT_LLM_TIMEOUT_S`, legacy `NANOBOT_LLM_TIMEOUT_S`, default 300s, `0` disables) so hung gateways return a timeout error instead of starving a session lock. |
| Session timestamp anchors in model context | `synced` | `Session.get_history(..., include_timestamps=True)` can annotate user/assistant text with `[Message Time: ...]`, and normal prompt assembly plus compaction probes use that timestamped view while persisted session format stays unchanged. |
| Ask-user clarification tool | `watchlist` | Upstream added an `ask_user` tool plus CLI/WebUI choice rendering. Local hahobot should only adopt this after mapping the UX across CLI, gateway channels, buttons, and session-lock semantics. |
| CLI input Unicode sanitization | `synced` | Interactive CLI input and prompt history writes replace malformed surrogate code points before dispatch/persistence while preserving valid surrogate pairs as normal Unicode characters. |
| Provider request sanitization | `synced` | Role alternation repair now recovers a trailing assistant message as `user` when otherwise only `system` content would remain, and multimodal content sanitization drops whitespace-only text blocks before strict provider APIs see them. |
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
| Feishu topic reply routing | `synced` | Topic/thread replies keep every split outbound part on the Reply API path using the root/message id, so card/table chunks do not fall out of the active topic after the first segment. |
| WebSocket server channel | `synced` | Local runtime already ships `channels.websocket`, including tokenless local mode, optional `tokenIssuePath` / `tokenIssueSecret`, and the simple `ready` / `message` / `delta` / `stream_end` frame contract. |
| Legacy rename compatibility | `synced` | `nanobot` CLI/module/import compatibility stays live, and default config fallback is preserved. |
| Config fallback behavior | `intentional_divergence` | When no config path is passed, hahobot checks `~/.hahobot/config.json` first, then copies `~/.nanobot/config.json` into the hahobot location instead of migrating in place. |
| Web search backend mix | `synced` | Built-in web search now supports Brave, SearXNG, and DuckDuckGo; DuckDuckGo runs as an exclusive tool so concurrent tool batches do not group multiple searches together. |
| Search provider breadth | `watchlist` | Upstream now also carries additional search backends such as Kagi/Olostep; local runtime still intentionally limits `tools.web.search.provider` to Brave, SearXNG, and DuckDuckGo until there is real demand for another backend plus matching config/admin/docs wiring. |
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
| Transcription language hints / retry | `synced` | `channels.transcriptionLanguage` validates ISO-639-like language hints, hot-reloads into running channels, is passed to Groq/OpenAI transcription requests, and transient Whisper failures retry before returning an empty transcription. |
| Mid-turn follow-up injection | `watchlist` | Local dispatch stays per-session serialized and crash-safe, but it does not splice new user turns into an already running session; upstream-style active-turn injection would touch locks, checkpoints, streaming, `/stop`, and compaction semantics together. |
| Dream skill discovery automation | `intentional_divergence` | Upstream lets Dream discover/write reusable skills automatically; local skill accumulation stays operator-visible and reviewable through `/skill derive` instead of unattended Dream promotion. |
| GenericAgent-style SOP workflow | `synced` | Hahobot now ships built-in workflow skills (`workflow-core`, `plan`, `verify`), subagent execution modes (`explore` / `implement` / `verify`), persisted `working_checkpoint` state across session/admin/status surfaces, and an independent-review gate for plans/TODOs that create future autonomous work. |
| GenericAgent-style skill accumulation | `synced` | Hahobot now supports local skill derivation through `/skill derive <name> [brief] [--force]`, turning recent successful session workflow into a reusable workspace skill draft. |
| Skill lifecycle hygiene / prompt budget control | `local_extension` | Runtime skill summaries are now query-aware top-k views, `supersedes` can hide replaced skills from the shared summary, `/skill supersede` plus `remove` / `clear` maintain that metadata explicitly, and `/skill lint` reports overlap or missing supersedes targets before local skill growth turns chaotic. |
| Skill usage writeback | `local_extension` | Workspace skill `last_used` / `success_count` now update from real runtime `read_file` usage instead of staying frozen at derive-time defaults. |
| GenericAgent layered memory semantics | `synced` | hahobot already separates conversation archive, `MEMORY.md`, `PROFILE.md`, and `INSIGHTS.md`, with Dream + archive sidecars providing a stronger implementation than GenericAgent's simpler layered-memory framing. |
| Hermes-inspired workspace wiki skill | `local_extension` | Built-in `llm-wiki` treats the repo itself as a local concept/config/architecture wiki, using docs + code + tests as the evidence chain without adding another runtime service. |
| Persona / companion workflow | `local_extension` | `PROFILE.md`, `INSIGHTS.md`, `STYLE.md`, `LORE.md`, companion commands, SillyTavern imports, voice overrides, and scene generation are local-first features. |
| Memory architecture | `local_extension` | Dream maintenance, archive sidecars, Mem0 backend/shadow-write, and structured profile/insight hygiene go beyond upstream nanobot. |
| claude-mem SQLite FTS archive index | `synced` | Hahobot now supports `memory.archive.indexBackend="sqlite"` as a persona-local derived FTS cache for `history_search` / `history_timeline`, rebuildable with `hahobot memory index rebuild` from JSON sidecars. |
| claude-mem Chroma/service backend | `intentional_divergence` | Hahobot keeps markdown/archive JSON sidecars as the source of truth plus optional Mem0 instead of adopting a separate Chroma/vector memory service; local recall remains inspectable and persona-scoped. |
| nocturne patch-only memory writes | `synced` | The Consolidator `save_memory` tool no longer takes a full `memory_update` rewrite of `MEMORY.md`. It now takes an optional `new_facts` fragment that is *appended* via `MemoryStore.append_memory` (capped, private-stripped); `write_memory` is atomic. Deduplication/compaction is handled by Dream — `dream_phase1.md` / `dream_phase2.md` were updated to explicitly flag and merge append-accumulated MEMORY.md redundancy. A truncated or lossy LLM response can no longer overwrite existing long-term memory. |
| nocturne disclosure triggers | `watchlist` | nocturne attaches a "recall when X" condition to each memory unit. hahobot loads core memory files always-on and uses query-aware top-k for skills; per-entry recall triggers for `PROFILE.md` / `INSIGHTS.md` bullets could help prompt budget but add a retrieval step and complexity. Low priority. |
| nocturne graph memory backend | `intentional_divergence` | nocturne stores memory in a graph DB (Node/Memory/Edge/Path) behind a FastAPI/MCP service. hahobot keeps human-readable, git-diffable Markdown as the source of truth; a graph/DB backend is rejected for the same source-first reason as the claude-mem Chroma divergence. |
| nocturne boot protocol / active recall | `intentional_divergence` | nocturne requires every session to call `read_memory("system://boot")` and depends on the agent reliably invoking recall tools. hahobot loads identity/relationship/profile layers into the system prompt always-on, which does not depend on model recall discipline. |
| Gateway/admin/runtime ops | `local_extension` | Admin UI, `/status`, Star-Office push, companion doctor, runtime doctor, session inspection, and gateway-backed `/session` / `/repo` / `/review` / `/compact` controls are local operational surfaces. |
| Standalone browser WebUI | `intentional_divergence` | Upstream now ships a separate browser chat SPA over WebSocket; local web surfaces still stay in the existing gateway admin and `/status` shell rather than adopting a second chat frontend stack. |
| Extension model | `local_extension` | Skills, MCP, built-in companion helpers, and `ExternalHookBridge` are the main extension surfaces; there is no separate plugin framework today. |
| Chinese rate-limit transient error markers | `synced` | `providers/base.py` now recognizes `"访问量过大"` as a general transient error and `"速率限制"` as a retryable 429 signal, matching nanobot's Chinese-provider retry handling. |
| Consolidator session-refresh guard | `synced` | `maybe_consolidate_by_tokens` now refreshes the session reference with `get_or_create` after acquiring the consolidation lock, preventing stale-reference overwrites when AutoCompact truncates concurrently. |
| Background task LLM runtime resolver | `watchlist` | nanobot introduced `LLMRuntime`/`LLMRuntimeResolver` so heartbeat/background tasks fetch a fresh provider+model at call time. hahobot's `apply_runtime_config` covers model hot-reload; pool-provider rotation benefit remains. |
| Ant Ling provider | `watchlist` | Upstream added `ant_ling` as an OpenAI-compatible provider (`https://api.ant-ling.com/v1`, models `Ling-2.6-flash`, `Ling-2.6-1T`). Add when there is real demand with schema/docs/admin wiring. |
| Novita / Skywork / APIFree providers | `watchlist` | Upstream added three more OpenAI-compatible providers (Novita AI, Skywork via the APIFree agent endpoint, APIFree). Same stance as Ant Ling: add per-provider only with real demand plus schema/docs/admin wiring, not as speculative breadth. |
| Image-generation provider breadth | `watchlist` | Upstream added Gemini, StepFun, and MiniMax image-generation providers behind a provider registry. Local `tools.imageGen` has its own contract; adopt new backends only with config/docs/admin treatment and per-provider delivery tests. |
| Signal channel | `watchlist` | Upstream added a Signal channel (signal-cli SSE receive loop, DM pairing-code flow, configurable attachments dir, UTF-16 textStyle offsets). Local channel set does not include Signal; add only with a concrete operator need and full schema/docs/multi-instance treatment. |
| Kimi/MiMo OpenRouter reasoning injection | `watchlist` | Upstream injects OpenRouter's unified `reasoning.effort` for Kimi/MiMo thinking models and drops the redundant top-level `reasoning_effort` for Moonshot Kimi (which 400s on both). Local `moonshot`/`xiaomi_mimo` specs carry no `thinking_style`, so there is no native thinking injection to reconcile yet; revisit if Kimi/MiMo thinking toggles are added locally. |
| Weixin silent message-drop hardening | `watchlist` | Upstream hardened the weixin iLink channel against silent drops (log inbound poll exceptions, check both `ret` and `errcode` on send, proactively refresh `context_token` via `getconfig` when older than 60s). Re-check local `channels/weixin.py` against this if weixin message loss is reported in practice. |
| Exec config timeout uncap (`timeout=0` = no limit) | `watchlist` | Upstream lifted the 600s `_MAX_TIMEOUT` cap from the **config-level** exec timeout so operators can set `tools.exec.timeout=0` to disable the limit entirely (per-call LLM-supplied timeout still caps at `_MAX_TIMEOUT`). Local `ExecToolConfig.timeout` still clamps via `min(timeout or self.timeout, self._MAX_TIMEOUT)`; this loosens a safety boundary, so adopt only with explicit operator docs and an admin-surface explanation. |
| Per-subagent sampling temperature | `watchlist` | Upstream `spawn` now accepts an optional `temperature` argument so a model can pick determinism per subtask. Local `spawn(mode=...)` already enforces role boundaries through tool registry; add only if there is a concrete persona/subagent need that the model-level temperature default can't cover. |
| OpenAI provider `apiType` + `extraBody` | `watchlist` | Upstream added an `apiType` (chat-completions vs responses) selector and `extraBody` passthrough for the OpenAI provider, with admin/WebUI settings wiring. Local `openai_compat_provider` already routes GPT-5/o-series to Responses via heuristics + circuit breaker; an explicit `apiType` would make it operator-controllable. Adopt with schema/admin/docs treatment together. |
| OpenAI / OpenAI Codex / Zhipu / Ollama image-generation providers | `watchlist` | Upstream landed image-gen for OpenAI, OpenAI-Codex, Zhipu (智谱), and Ollama plus an HTTP-handling refactor and MiniMax mime-detection fix. Local `tools.imageGen` has its own contract — adopt per-provider only with config/docs/admin treatment and per-provider delivery tests. |
| Transcription `apiBase` normalization | `watchlist` | Upstream now accepts chat-style transcription bases (e.g. `https://api.groq.com/openai/v1`) and appends `audio/transcriptions` automatically, with `OPENAI_TRANSCRIPTION_BASE_URL` / `GROQ_BASE_URL` env hooks. Local `OpenAITranscriptionProvider` / `GroqTranscriptionProvider` use hardcoded URLs and do not expose `apiBase` config; adopt only when a `channels.transcriptionApiBase` (or equivalent) is added with schema/admin/docs treatment. |
| Apply-patch edits-only tool | `intentional_divergence` | Upstream removed the legacy unified-diff `patch` mode from `apply_patch` and now accepts only the structured `edits` array. Hahobot does not ship the `apply_patch` tool — file edits go through `notebook_edit` (for `.ipynb`) plus general `read_file` / shell write flows — so there is nothing to converge here. |
| MCP preset setup / capability mentions | `intentional_divergence` | Upstream added a Settings-UI driven MCP preset wizard (`mcp_presets_api` + WebUI). Hahobot keeps MCP wiring under `tools.mcpServers.*` (file-first config with `enabledTools`); adopting a preset wizard would imply pulling in the WebUI settings stack and is rejected for the same reason as the standalone browser SPA divergence. |
| Future upstream channel/provider churn | `watchlist` | Re-audit `channels/`, `providers/`, `cron/`, `agent/hook.py`, `config/schema.py`, and runtime doctor whenever upstream lands new runtime toggles or transport behavior. |

## GenericAgent Detailed Matrix

This section tracks the more granular adoption/divergence decisions for `lsdefine/GenericAgent`.
It is intentionally finer-grained than the top-level snapshot so we can tell which ideas are
already productized locally and which ones are still only partially reflected in hahobot.

| GenericAgent Theme | Status | Local Surface | Remaining Gap / Notes |
| --- | --- | --- | --- |
| Workflow SOP packaged as built-in guidance | `synced` | Bundled `workflow-core`, `plan`, `verify`, and `skill-derive` skills under `hahobot/skills/`, including independent review before executing generated future-task plans/TODOs | Hahobot keeps SOPs as skills instead of hard-wiring a GenericAgent-style monolithic agent loop. |
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

## Borrow Candidates From 2026-05-07 Audit

Implemented locally in this pass:

- **Tool-hint length control**: adopted upstream's `agents.defaults.toolHintMaxLength` so operators
  can widen or shorten tool-call progress hints without disabling `channels.sendToolHints`.
- **Whisper transcription retry and validation**: Groq/OpenAI audio transcription now retries
  transient HTTP/network failures, preserves `language` across attempts, and turns malformed
  successful responses into safe empty transcriptions.

Still worth re-checking before porting:

- **Per-channel progress overrides**: local runtime has global `channels.sendProgress` /
  `channels.sendToolHints`; borrow per-channel overrides only if a concrete channel needs a quieter
  or noisier default than the global setting.
- **Soft workspace / SSRF boundaries**: upstream now favors recoverable boundary failures in more
  places; local security errors are stricter and should be softened only where the agent can safely
  continue.
- **Provider/search additions**: Bedrock, Hugging Face, LongCat, OpenAI-compatible `extraBody`, and
  Olostep-style search need schema/provider/docs/admin treatment before becoming local features.
- **GenericAgent ACP/BBS/worker experiments**: watch for stable protocol ideas, but do not import
  another frontend or team-worker architecture into hahobot without a matching local ops need.

## Borrow Candidates From 2026-05-08 Audit

Implemented locally in this pass:

- **Future-task plan review gate**: mapped GenericAgent's subagent-review hardening onto
  hahobot's built-in workflow skills. `workflow-core`, `plan`, and `verify` now require
  independent review before executing generated future-task plans/TODOs and explicitly reject
  self-review as sufficient approval.

Intentionally skipped / watchlist:

- **OpenAI-compatible API SSE compression fix**: upstream removed aiohttp compression for real SSE
  streaming. Local `hahobot serve` still returns 400 for `stream=true`, so this remains covered by
  the existing non-streaming API divergence.
- **GenericAgent Textual TUI and ACP/frontend churn**: useful upstream product direction, but
  hahobot keeps runtime surfaces in CLI/gateway/admin/status and should not import another frontend
  stack without a local operator need.
- **claude-mem changelog-only update**: no new memory architecture idea to adopt.

## Borrow Candidates From 2026-05-19 Audit

Implemented locally in this pass:

- **Chinese rate-limit transient error markers**: nanobot added `"访问量过大"` (traffic overload) to
  its transient error marker list. Local `providers/base.py` now includes it in
  `_TRANSIENT_ERROR_MARKERS`, and the matching Chinese rate-limit phrase `"速率限制"` in
  `_RETRYABLE_429_TEXT_MARKERS`, so Chinese-endpoint rate-limit responses trigger retry instead of
  surfacing as hard failures.
- **Consolidator session-refresh guard**: nanobot fixed a race condition where
  `maybe_consolidate_by_tokens` could proceed with a stale session reference after AutoCompact
  truncated the session while the consolidation lock was being acquired. Local
  `agent/memory.py:maybe_consolidate_by_tokens` now refreshes the session via
  `self.sessions.get_or_create(session.key)` immediately after acquiring the lock.

Reviewed and intentionally skipped:

- **WebUI streaming / live file-edit activity / session-title polish**: WebUI-only changes; hahobot
  keeps runtime surfaces in the existing gateway/admin/status shell.
- **Ant Ling provider** (`ant_ling`, `https://api.ant-ling.com/v1`): new OpenAI-compatible provider
  (Ling-2.6-flash, Ling-2.6-1T). Add when there is real demand; requires schema/docs/admin wiring.
- **Model Preset wizard in onboard**: CLI onboarding wizard for selecting a model preset. hahobot
  has its own onboarding flow; revisit only if the interactive selection UX has concrete adoption
  pressure.
- **CLI reasoning token buffering**: nanobot buffers streaming reasoning tokens and flushes on
  newlines / sentence punctuation / 60+ chars to avoid one-token-per-line display. hahobot's CLI
  uses `_print_cli_progress_line` which does not yet have per-token reasoning streaming; revisit
  when reasoning streaming is added to the interactive CLI.
- **Background task LLM runtime resolver** (`LLMRuntime` + `LLMRuntimeResolver`): nanobot
  introduced a resolver abstraction so heartbeat and background tasks always fetch a fresh
  provider/model snapshot at call time instead of holding a static startup reference. hahobot's
  `HeartbeatService.apply_runtime_config` already handles model hot-reload; the resolver would
  additionally benefit pool-provider rotation. Port as part of a broader hot-reload or pool-provider
  improvement rather than in isolation.
- **GenericAgent TUI v2, ACP/BBS, hive-worker changes**: no runtime or memory ideas to adopt.

## Borrow Candidates From 2026-05-22 Audit

Implemented locally in this pass:

- **Exec stdin isolation**: `ExecTool._spawn` now launches the POSIX bash and Windows COMSPEC
  subprocesses with `stdin=asyncio.subprocess.DEVNULL`. A shell command that reads from stdin
  previously inherited the parent terminal and could hang the per-session lock; it now sees EOF
  immediately.
- **Streaming tool_call id dedup**: `OpenAICompatProvider.chat_stream` deduplicates reused
  `tool_call` ids before building `LLMResponse`. Some providers (Zhipu/GLM) reuse one id for
  parallel streaming tool calls, which would otherwise collide downstream tool messages.
- **History tool_call id dedup**: `_sanitize_messages` now assigns each `tool_call` within a
  message a unique normalized id and routes tool results back through a per-raw-id FIFO, so an
  assistant message carrying duplicate `tool_call` ids no longer produces an ambiguous
  assistant/tool-result pairing.

Reviewed and intentionally skipped / left on watchlist:

- **Shell guard URL path detection**: upstream added a negative lookbehind so `https://` URLs are
  not extracted as Windows drive paths. The local `_extract_absolute_paths` Windows regex still
  requires a backslash after the drive letter (`[A-Za-z]:\\...`), so URLs never matched locally and
  no change is needed.
- **Novita / Skywork / APIFree providers, Gemini/StepFun/MiniMax image generation, Signal
  channel**: new provider/channel surfaces; tracked on the watchlist, add only with real demand and
  full schema/docs/admin wiring.
- **Kimi/MiMo OpenRouter `reasoning.effort` injection and the Moonshot `reasoning_effort` drop**:
  both depend on upstream's Kimi/MiMo native-thinking injection, which has no local equivalent
  (`moonshot`/`xiaomi_mimo` carry no `thinking_style`). Revisit only if Kimi/MiMo thinking toggles
  are added locally.
- **Gateway cold-start optimization**: useful upstream perf work, but it is entangled with the
  WebUI/lazy-import boundary churn; re-check only if local gateway startup latency becomes a
  measured problem.
- **Coding-workflow tool contract / patch+session workflow changes**: upstream internalized a
  general tool-workflow contract prompt and tightened apply-patch/session tooling. Hahobot keeps
  workflow guidance in bundled skills (`workflow-core`, `plan`, `verify`); re-check only if a
  concrete local tool-recovery gap appears.

## Borrow Candidates From 2026-05-09 Audit

Implemented locally in this pass:

- **CLI surrogate sanitization**: mapped nanobot's Windows/prompt_toolkit history hardening onto
  hahobot's `SafeFileHistory` and interactive CLI dispatch path, preserving valid emoji while
  replacing malformed surrogate code points before persistence or message-bus entry.
- **Feishu topic multipart replies**: when Feishu metadata indicates a topic/thread, every split
  outbound part now uses the Reply API with the topic root/message id instead of only the first
  segment.
- **Whitespace-only provider text blocks**: adopted GenericAgent's stricter request cleanup by
  dropping blank multimodal text blocks before OpenAI-compatible/Anthropic/Responses conversion
  sees the request.

Reviewed and intentionally skipped / left on watch:

- **nanobot replay-window consolidation fix**: local token consolidation and persona rollover
  archive from the full unconsolidated tail (`session.messages[last_consolidated:]`) rather than
  from a replay-window slice, so there is no matching local replay-window gap to port.
- **nanobot WebUI/settings/image-generation churn**: useful upstream direction, but hahobot keeps
  local ops in the existing gateway/admin/status runtime and already has its own image-gen tool
  contract.
- **GenericAgent configure wizard, goal mode, and frontend churn**: no direct local parity target;
  revisit only if the ideas become runtime contracts rather than frontend implementation detail.
- **claude-mem homepage metadata fix**: tracked for audit freshness; no memory architecture change
  to adopt.

## Borrow Candidates From 2026-05-25 Audit

Implemented locally in this pass:

- **Preserve OpenAI-compat `tool_call` ids in non-streaming responses**: nanobot's
  `openai_compat_provider` non-streaming response parser now passes the upstream `tc_map.get("id")`
  (or `getattr(tc, "id", None)`) into `ToolCallRequest` instead of always minting a fresh
  `_short_tool_id`. Local `_sanitize_messages` will still normalize/dedupe ids before the next
  outbound request, so this is purely a "preserve correlation in the first stored assistant turn"
  change — useful for logs and any downstream consumer that compares pre-/post-sanitization ids.
- **Responses replay item-id dedup**: ported the upstream `_unique_item_id` helper into
  `hahobot/providers/openai_responses/converters.py`. Resumed conversations with duplicate
  `msg_*` / `fc_*` ids no longer get rejected by Codex while `call_id` (tool-result linkage)
  stays unchanged.
- **Per-hop WebFetch redirect SSRF check**: replaced the existing "validate only the final
  resolved URL after httpx followed redirects" behavior with `_get_with_safe_redirects`, which
  walks the chain manually (`follow_redirects=False`) and revalidates each `Location` against
  the SSRF policy before issuing the next request. Applied to both the image-pre-fetch and the
  readability fallback paths in `WebFetchTool`.

Reviewed and intentionally skipped / left on watchlist:

- **Exec config `timeout=0` = no limit** (`5b71f61f`): loosens a safety boundary
  (`_MAX_TIMEOUT=600` no longer caps the config-level value). Adopt only with explicit operator
  docs and admin-surface explanation; per-call LLM-supplied timeout stays capped either way.
- **Per-subagent sampling `temperature`** (`7a6cc657`): nice-to-have; `spawn(mode=...)` already
  enforces role boundaries via tool registry. Add only with a concrete persona/subagent need.
- **OpenAI `apiType` + `extraBody`** (`d4725954`, `c433d606`): operator-controllable Responses-vs-
  Chat-Completions selection plus an `extraBody` passthrough. Local routing already prefers
  Responses for GPT-5/o-series via heuristics + circuit breaker; adopt with schema/admin/docs
  treatment together.
- **Transcription `apiBase` normalization** (`ef2ef4f7`): only matters if a `channels.transcriptionApiBase`
  is added; local providers use hardcoded URLs today.
- **Image-gen provider breadth** (`3483121e` OpenAI/Codex, `3e6f9907` Zhipu, `84603f4c` Ollama,
  `e6587a8d` MiniMax mime, `a7b34422` Gemini base): per-provider adopt only with config/docs/admin
  treatment plus per-provider delivery tests.
- **MCP preset setup + capability mentions** (`704ac558`): WebUI Settings-driven flow. Same
  stance as the standalone browser-chat SPA divergence.
- **Apply-patch refactor + edits-only tightening** (`3d9f50a0`, `b0d30696`): hahobot does not
  ship the `apply_patch` tool; nothing to converge.
- **CLI Apps settings MVP + WebUI churn** (`e2d00ffc`, locale fills, activity-cluster polish):
  WebUI-only; hahobot keeps local ops in CLI/gateway/admin/status.
- **Shell-guard URL path detection revert** (`3f789bd9` revert of `65cecc01`): no change to
  port either direction; local regex was already URL-safe.
- **`claude-mem` `oh-my-issues` + `weekly-digests` skills** (v13.3.0): potentially portable as
  hahobot bundled workflow skills (GitHub issue clustering, ISO-week serial narrative). Track on
  watchlist pending operator demand; do not import claude-mem AGPL files verbatim.
- **`nocturne_memory` boot URI presets + AntiGravity heartbeat** (v2.5.3): boot URI presets sit
  on top of the mandatory boot protocol that hahobot has already rejected as intentional
  divergence; AntiGravity heartbeat is IDE-integration-specific. Nothing to adopt.

## GenericAgent Adoption Notes

- Hahobot now covers the two previously open GenericAgent gaps that motivated adding it as an
  upstream here: first-class workflow SOP skills and local skill derivation from successful runs.
- The workflow skills now also include a guardrail from the 2026-05-08 GenericAgent audit:
  generated future-task plans/TODOs remain drafts until independently reviewed, preferably through
  `spawn(..., mode="verify")`, and self-review must not be treated as permission to execute them.
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

## nocturne_memory Memory-Architecture Review (2026-05-19)

`Dataojitori/nocturne_memory` is a graph-backed long-term memory MCP server: `Node` (concept,
persistent UUID) → `Memory` (versioned content, chained via `migrated_to`) → `Edge` (parent→child
relationship) → `Path` (materialized URI cache). It uses URI addressing (`core://nocturne/identity`),
per-entry disclosure triggers, patch-only updates, a changeset audit trail, and a React review
dashboard. It explicitly rejects vector RAG in favor of agent-controlled first-person memory.

Decisions from this review:

- **Adopted: append-only memory writes.** nocturne's `update_memory` has no full-replacement mode
  by design. hahobot's Consolidator `save_memory` previously requested a full `memory_update`
  markdown blob, so a truncated or malformed LLM response could overwrite all of `MEMORY.md`.
  `save_memory` now takes an optional `new_facts` fragment appended via `MemoryStore.append_memory`
  (length-capped, private-stripped), and `MemoryStore.write_memory` is atomic. The Consolidator
  path is fast append-only archival; deduplication and compaction stay with Dream's incremental
  edits. Because that division of labor depends on Dream actually compacting the append-grown
  file, the `dream_phase1.md` / `dream_phase2.md` prompts were also updated to explicitly flag
  overlapping/near-duplicate MEMORY.md bullets and repeated headers and merge them into a coherent
  structure — making "Consolidator appends, Dream compacts" an explicit guarantee rather than
  implicit best-effort. Implemented 2026-05-19.
- **Consider later (watchlist): addressable memory entries.** Making each `PROFILE.md` /
  `INSIGHTS.md` bullet a unit with a stable id + metadata (extending the existing `hahobot-meta`
  comment) would enable per-entry versioning, ranked recall, and optional disclosure triggers
  without leaving Markdown. Larger scope; pair with a recall refactor in `context.py`.
- **Reject (intentional divergence): graph DB backend.** Same source-first reasoning as the
  claude-mem Chroma divergence — human-readable, git-diffable Markdown stays the source of truth;
  any index (FTS, embeddings, relationships) must be a rebuildable derived layer.
- **Reject (intentional divergence): separate memory service + mandatory boot protocol.** hahobot's
  memory is the workspace (zero runtime dependency) and core layers load always-on, which does not
  depend on the model remembering to call a recall tool.

Net: nocturne validates hahobot's layered, file-first memory direction. The actionable takeaway is
write safety on the Consolidator path, not a backend replacement. The optimal target stays the
four-layer model (always-on identity / Dream-maintained semantic layer / append-only episodic
archive / rebuildable derived index) over inspectable Markdown — an in-place evolution, not a
replacement.

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
- Runtime tool hints format shell commands more robustly, including quoted paths, repeated calls, and
  configurable visible length through `agents.defaults.toolHintMaxLength`.
- Groq/OpenAI Whisper transcription retries transient failures and validates malformed responses
  before channel adapters fall back to an empty transcription.
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
- Chinese rate-limit responses from providers that return `"访问量过大"` or `"速率限制"` are now
  treated as transient/retryable errors rather than hard failures.
- `maybe_consolidate_by_tokens` refreshes its session reference after acquiring the consolidation
  lock, preventing AutoCompact from being silently undone by a concurrent consolidation run holding
  a stale session object.
- Shell exec subprocesses are spawned with `stdin=asyncio.subprocess.DEVNULL` so a command reading
  from stdin sees EOF immediately instead of hanging on the inherited terminal.
- Reused `tool_call` ids are deduplicated both in streaming responses and during history
  sanitization, so providers that emit one id for parallel calls cannot create an ambiguous
  assistant/tool-result pairing.
- Non-streaming OpenAI-compat response parsing preserves the original upstream `tool_call` id
  when present (instead of always minting a fresh `_short_tool_id`), so log correlation and
  downstream tool-result linkage stay readable.
- Responses-API converter routes assistant `message` and `function_call` items through
  `_unique_item_id`, so resumed Codex conversations with duplicate `msg_*` / `fc_*` items no
  longer get rejected while `call_id` linkage stays intact.
- `WebFetchTool` validates every redirect hop's resolved IP via `_get_with_safe_redirects`
  before issuing the next request, not just the final URL after httpx has silently followed
  the chain.

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
- Memory stays file-first: human-readable, git-diffable Markdown is the source of truth, and any
  index (FTS, embeddings, graph relationships) is a rebuildable derived layer. Graph-DB or
  separate-service memory backends (nocturne_memory style) are rejected for the same reason as the
  claude-mem Chroma divergence; core memory layers also load always-on rather than behind a
  model-invoked boot/recall protocol.

## Watchlist For Next Upstream Sync

- The 2026-05-25 pass synced non-streaming `tool_call` id preservation, Responses replay item-id
  dedup, and per-hop WebFetch redirect SSRF validation. Next pass should track the exec config
  timeout uncap (loosens a safety boundary; needs operator-facing docs first), per-subagent
  sampling temperature, OpenAI `apiType` + `extraBody` (with schema/admin/docs treatment), the
  transcription `apiBase` normalization (only if `channels.transcriptionApiBase` is added), and
  the OpenAI/Codex/Zhipu/Ollama image-generation providers (only with per-provider delivery
  tests). The MCP preset wizard and CLI Apps settings MVP are explicit WebUI divergences and
  should not be ported as parity work.
- The 2026-05-22 pass synced exec stdin isolation and streaming/history `tool_call` id dedup; the
  background-task LLM runtime resolver (pool-provider rotation benefit), the Novita/Skywork/APIFree
  providers and Signal channel (when there is an operator need), the weixin silent message-drop
  hardening (if weixin message loss is reported), and CLI reasoning token buffering (when reasoning
  streaming is added to the interactive CLI) remain on the watchlist.
- claude-mem v13.3.0 added two new workflow skills (`oh-my-issues` for GitHub issue clustering,
  `weekly-digests` for ISO-week serial narrative). Consider porting as hahobot bundled workflow
  skills only when operator demand exists; do not import the AGPL implementation verbatim.
- The 2026-05-19 pass synced Chinese rate-limit markers and the Consolidator session-refresh guard.
- Re-check `thedotmack/claude-mem` directly on the next pass; the 2026-05-19 audit could not
  fetch reliable content and was deferred.
- The `nocturne_memory` append-only memory-write idea is now synced (Consolidator `save_memory`
  appends `new_facts` instead of full-rewriting `MEMORY.md`). The remaining nocturne idea on watch
  is addressable memory entries (stable id + metadata per `PROFILE.md` / `INSIGHTS.md` bullet for
  per-entry versioning and ranked recall); pair it with a `context.py` recall refactor if pursued.
  Re-review `nocturne_memory` itself only if it grows runtime ideas beyond the graph-DB/service
  model already evaluated.
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
