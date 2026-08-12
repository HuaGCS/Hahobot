# Upstream Parity

This is the current parity ledger for Hahobot. It records the latest audited upstream boundaries,
the decisions that still affect the codebase, and the next items worth checking. Detailed historical
notes are kept outside this root file so routine contributor and agent context stays bounded.

## Reading Guide

Use these sources in this order:

1. This file for current boundaries, dispositions, divergences, and watchlist items.
2. The source-specific notes for durable mappings and rationale:
   - [`nanobot`](docs/upstream-parity/NANOBOT.md)
   - [`GenericAgent`](docs/upstream-parity/GENERICAGENT.md)
   - [`claude-mem` and `nocturne_memory`](docs/upstream-parity/MEMORY_UPSTREAMS.md)
   - [`jiuwenswarm`](docs/upstream-parity/JIUWENSWARM.md)
3. [`AUDIT_LOG_2026.md`](docs/upstream-parity/AUDIT_LOG_2026.md) for the complete dated audit
   record and superseded evaluations.
4. Git history for the implementation diff itself.

The current ledger is authoritative when an older audit entry describes a superseded state.

## Scope And Current Boundaries

| Upstream | Tracking role | Audited ref | Previous boundary | Audit date |
| --- | --- | --- | --- | --- |
| `HKUDS/nanobot` | Primary behavior-parity target | `main@abfcdd481` | `3778e7e62` | 2026-08-12 |
| `lsdefine/GenericAgent` | Architecture/workflow ideas | `main@63f9db74e` | `d426d45e` | 2026-08-12 |
| `thedotmack/claude-mem` | Memory-architecture ideas | `main@4702c337` | `132b4634` | 2026-08-10 |
| `Dataojitori/nocturne_memory` | Memory-architecture ideas | `main@54c48eea` | `2cbfb8a` | 2026-08-10 |
| `openJiuwen/jiuwenswarm` | Architecture/channel ideas | `develop@fb43da6c` | `de623dd9` | 2026-08-10 |

`nanobot` and `GenericAgent` remotes must retain `tagOpt = --no-tags`. Hahobot owns its independent
`v0.x` release line; upstream tags are not imported into the local `v*` namespace.

Related projects such as NanoMate, Hermes Agent, and `soongenwong/claudecode` may inspire local
design, but they are not tracked parity targets.

## Status Legend

- `synced`: the behavior exists locally, possibly through a different implementation.
- `local_extension`: Hahobot-owned behavior for which upstream parity is not the goal.
- `intentional_divergence`: local behavior deliberately differs from upstream.
- `watchlist`: re-evaluate when the related local surface or upstream behavior changes.

## Latest Audit / Port Update — 2026-08-12

### nanobot

Audited 19 linear commits from `3778e7e62` through `abfcdd481`. Tool JSON Schema validation rejects
non-finite `number` values after casting and at nested paths (`99e07e138`); Matrix text and media
thread replies now use root-event-scoped sessions (`057e8f7af`). Provider environment isolation,
weather workflow portability, MCP runtime status, and OpenRouter server-tool merging are recorded
for follow-up against their local owners. The Agent Plugins/marketplace and React PWA work
remain intentional architecture/product divergences: Hahobot keeps its workspace skill lifecycle,
explicit MCP config, and server-rendered WebUI instead of adding a second package-market surface.

Follow-up audited 16 commits from `55ecda27` through `3778e7e62`, after the prior 188-commit pass.
The portable deltas are adapted onto local owners:

- generated-image URL downloads now pin validated DNS, revalidate every redirect, honor explicit
  proxy DNS without trusting private literals, reject IPv6 unspecified targets, stream with a
  32 MiB cap, and verify image bytes (`4408cde0..b3d3a3e6`);
- Dream history compaction never drops entries beyond its processed cursor; malformed idle-summary
  timestamps/metadata and raw-archive message fields degrade safely (`7fd28c9f`, `e633f867`,
  `39bb20c7`, `4c387f66`);
- real Dulwich object IDs cross the `/dream-log` and restore boundary (`92361cbe`), blank
  `finish_reason=length` responses enter continuation (`511c764f`), and cron expressions are
  validated before persistence (`73a00804`);
- Gemini Flash hints use live `generationConfig.imageConfig` syntax (`08fe9f7b`); Anthropic's
  versioned adaptive-thinking/effort rules cover Opus 4.7+ and Opus/Sonnet 5 (`4e8702a4`);
- Telegram preserves special-character and single-line fences (`a13e29bf`, `170c7083`), while
  Matrix sends a non-empty join body and retries eligible sync invites (`5c4c2cb8`);
- unknown slash commands are rejected locally with session-language wording and a nearest-command
  suggestion instead of reaching the model (`f45436b61`);
- temporary WebUI chats adapt nanobot's ephemeral-chat behavior onto the server-rendered gateway:
  process-local sessions skip persistence, memory/skill writeback, and cron ownership, while an
  explicit **Save copy** creates a normal persisted session (`c9a614587`, `a5bc3bfbb`, `75e333a3c`,
  `af52fbcbc`);
- `edit_file` rejects identical old/new text before reading or rewriting an existing file, so a
  malformed no-op request cannot report false success (`b3b051761`);
- nanobot's bounded persistent-exec buffer is adapted to Hahobot's one-shot shell path: stdout and
  stderr are drained concurrently with incremental UTF-8 decoding and fixed head/tail retention
  before the combined response cap (`5e67fbf93`).

The new React settings/runtime refactors remain architecture-specific. Browser OAuth for remote MCP
servers is useful but remains a security-sensitive watchlist item until Hahobot defines local token
storage, callback, and admin-session boundaries. The React/Vite marketplace, cross-session mention
UI, pairing/triggers, provider breadth, and whole-file session retention remain demand-driven or
architecture-specific. See
[`NANOBOT.md`](docs/upstream-parity/NANOBOT.md).

### GenericAgent

Audited 7 linear commits from `d426d45e` through `63f9db74e`. The new changes are Hub/P2P,
conductor, and world-model-specific; overload retry behavior remains covered by Hahobot's provider
retry layer and no portable local delta was identified. The prior 30-commit pass's Responses
terminal-event handling and maximum
reasoning effort map to Hahobot's existing provider normalization plus the newly updated Anthropic
capability matrix. The 60-second oversized `Retry-After` guard is useful, but remains a watchlist
item until Hahobot defines one policy across finite and persistent retry modes. Hub/P2P, desktop,
TUI, and conductor changes remain architecture-specific. See
[`GENERICAGENT.md`](docs/upstream-parity/GENERICAGENT.md).

### Memory upstreams

`claude-mem` added 11 commits through `4702c337`: Chroma write-storm control, sensitive
observations, a custom-mode creator, and hosted/install work. Local WAL/worker serialization,
privacy tags, and reviewed skill/subagent modes already cover the portable concepts.
`nocturne_memory` added 7 commits through `54c48eea`; bloat diagnostics and block-matched patching
are worth revisiting through local doctor/Dream surfaces, without adopting its graph store. See
[`MEMORY_UPSTREAMS.md`](docs/upstream-parity/MEMORY_UPSTREAMS.md).

### jiuwenswarm

Audited 271 commits through `develop@fb43da6c`. Relevant deltas—session-delete traversal guards,
config secret masking, cancellation cleanup, context compression, cron crash recovery, and stream
whitespace—are already covered by Hahobot's collision-safe session store, redacted admin/config
surfaces, task/tool lifecycle, bounded memory pipeline, claimed atomic cron store, and streaming
tests. Team/warm-pool, desktop/TUI, Symphony, PDF/PPT skills, and sandbox infrastructure remain
ideas-only. See [`JIUWENSWARM.md`](docs/upstream-parity/JIUWENSWARM.md).

## Current Snapshot

| Area | Status | Current local disposition |
| --- | --- | --- |
| Tool/runtime policy | `synced` | Central policy controls tool availability, hot reload, doctor output, explicit exec environment passthrough, and finite-number validation before dispatch. |
| File editing | `synced` | Exact replacements reject identical old/new text before I/O; oversized reads fail from metadata before allocation. |
| File/config durability | `synced` | Oversized reads fail before allocation; config/admin and cron commits use mode-preserving atomic replacement. |
| Exec isolation | `synced` | Segment-wise allow rules, deny-first matching, assignment/home-path guards, bounded-at-read one-shot output, bounded execution, and robust process cleanup remain local invariants. |
| Provider normalization | `synced` | Empty content, malformed surrogates, reasoning fields, versioned Anthropic adaptive effort, model-specific thinking, and provider error detail are normalized before transport. |
| Image generation | `synced` | Gemini Flash uses live `imageConfig`; generated-image downloads are redirect-safe, DNS-pinned, byte-capped, and content-verified while persona `/scene` keeps local reference-image behavior. |
| Length recovery | `synced` | Truncated provider segments are retried/merged and streamed as one visible response without losing already-produced text. |
| Hook streaming ownership | `synced` | Composite hooks fan out safely; only the primary output owner suppresses runner-side delta accumulation. |
| MCP schemas | `synced` | Local URI-decoded JSON Pointers are resolved/hoisted into `$defs`, including recursion and unresolved-ref fallback. |
| MCP lifecycle | `synced` | Each connection generation has one owner task; terminated sessions reconnect without cross-task context-manager teardown. |
| Session persistence | `synced` | Atomic rewrites, malformed-row tolerance, bounded strong LRU caching, and checkpoint recovery protect saved conversations. |
| Temporary WebUI chat | `synced` | Bounded process-local sessions skip JSONL, memory/skill writeback, and scheduling; **Save copy** is the explicit transition into persisted history. |
| Memory/archive | `local_extension` | Markdown remains source of truth; JSON sidecars and optional SQLite FTS are rebuildable recall indexes. |
| Dream maintenance | `local_extension` | Two-phase reflection updates local memory layers, advances only after completed phase 2, preserves all pending history, and emits real Git object IDs. |
| Skill lifecycle | `local_extension` | Query-aware summaries, usage metadata, derive/supersede/lint commands, and operator review govern local skill growth. |
| Subagent modes | `local_extension` | Explore/implement/verify tool boundaries and durable completion announcements extend the local runtime. |
| Cron persistence | `synced` | Syntax validation, cross-process transactions, expiring claims, merged history, worker-pool I/O, cancellation linearization, and safe store rebinding prevent common invalid/duplicate/lost-update paths. |
| Channel streaming | `synced` | Stateful delivery IDs, retry cursors, special-character-safe Telegram fences, and channel-specific overflow handling preserve exactly-once chunk progress within a delivery attempt. |
| Matrix joins | `synced` | Invite joins send `{}` for strict homeservers and retry allowed pending invites from the same sync response. |
| Matrix thread sessions | `synced` | Text and media replies isolate conversation state by room + thread root while delivery remains room-scoped. |
| Slack/Feishu rendering | `synced` | Fenced tables stay intact and malformed/null rich-message fields degrade safely. |
| WebUI persisted media | `synced` | Initial history and live frames share the traversal-guarded `workspace/out` media mapping. |
| WebUI connection recovery | `local_extension` | Per-session drafts, offline send gating, capped reconnect backoff, and request-id turn receipts reattach safely without duplicate model calls. |
| Slash-command UX | `synced` | Unknown or mistyped slash commands are rejected before model dispatch with localized nearest-command guidance. |
| Proactive delivery | `local_extension` | Cron, heartbeat, and cross-session messages persist into the destination session and can push to an open WebUI connection. |
| Server-rendered operations UI | `intentional_divergence` | WebUI/admin/status remain in the aiohttp/Jinja gateway instead of adopting a React/Vite or desktop stack. |
| Legacy compatibility | `local_extension` | `nanobot` CLI/module/SDK aliases and legacy config/cookie migration remain supported during the rename. |
| Versioning | `intentional_divergence` | Hahobot owns its `v0.x` tags independently of every upstream. |

The detailed historical matrix remains searchable in
[`AUDIT_LOG_2026.md`](docs/upstream-parity/AUDIT_LOG_2026.md#current-snapshot).

## Intentional Local Differences

- Hahobot is workspace-first and keeps richer CLI, gateway, admin, status, review, and channel
  surfaces instead of converging on a minimal single-loop runtime.
- WebUI stays server-rendered in the existing aiohttp/Jinja process; no parallel React/Vite SPA is
  introduced solely for parity.
- Memory stays human-readable and file-first. SQLite, embeddings, or graph relationships may be
  rebuildable indexes, but a graph database or hosted service does not become the source of truth.
- `PROFILE.md` and `INSIGHTS.md` remain separate memory layers, maintained through Dream and explicit
  metadata rules.
- Skill derivation and supersession remain operator-reviewed; background processes do not silently
  promote, merge, or delete skills.
- GenericAgent and jiuwenswarm are ideas upstreams. Their file layouts, Tauri/desktop applications,
  distributed Team mode, and separate sandbox runtimes are not parity requirements.
- The OpenAI-compatible API remains non-streaming until its contract is deliberately expanded.
- Workspace switching remains single-workspace-per-instance and uses runtime/admin configuration,
  rather than adopting a multi-workspace UI abstraction.

## Active Watchlist

| Upstream | Revisit when | Candidate |
| --- | --- | --- |
| nanobot | Channel identity or provider breadth is touched | DingTalk DM gating/sender labels; demand-driven provider/channel additions. |
| nanobot | Provider construction or CLI child processes are touched | Stop process-global credential mutation and audit minimal child-process environments. |
| nanobot | OpenRouter server tools or provider `extraBody` are exposed locally | Merge configured server tools without replacing Hahobot function tools. |
| nanobot | Session retention is redesigned | Session-file retention/archiving and cross-session references adapted to Hahobot's incremental JSONL and session-authority model. |
| nanobot | Reasoning model routing expands | Kimi/MiMo and other model-specific reasoning parameters not already covered locally. |
| nanobot | Remote authenticated MCP becomes an operator requirement | Browser OAuth with explicit callback binding, token storage/redaction, refresh, revocation, and admin-session security. |
| GenericAgent | Workflow or unattended background behavior becomes concrete | Reviewable planning/memory SOPs without copying desktop/conductor structure. |
| GenericAgent | Retry policy is redesigned | One bounded policy for oversized `Retry-After` across finite and persistent modes. |
| claude-mem | Archive sidecar schema changes | `filesRead` / `filesModified` evidence with migration and query semantics. |
| nocturne_memory | Memory diagnostics are expanded | Bloat reporting and stable per-entry IDs without replacing Markdown as source of truth. |
| jiuwenswarm | Config concurrency/security work begins | Cross-process config transactions and secret redaction. |
| jiuwenswarm | Local exec or orchestration requirements materially expand | `jiuwenbox` isolation or Team concepts, only with explicit authority boundaries. |

## Update Protocol

When manually porting, adapting, or intentionally skipping tracked upstream behavior, update parity
state in the same patch:

1. Fetch without importing upstream tags and record the exact audited ref, previous boundary, date,
   and whether history was rewritten.
2. Classify each meaningful delta as `synced`, `local_extension`, `intentional_divergence`, or
   `watchlist`.
3. Update the boundary and current disposition here; do not append old audit prose to this root file.
4. Update the relevant source-specific document when durable mapping or rationale changed.
5. Add a dated entry to the yearly audit log with commit IDs and implementation/test evidence.
6. Record the concrete local owner: code path, command, config field, documentation, and regression
   tests where applicable.
7. If behavior or contributor workflow changed, update `README.md`, `README_ZH.md`, and `AGENTS.md`
   together.

This keeps current decisions fast to load while preserving the complete audit trail.
