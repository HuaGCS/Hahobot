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
| `HKUDS/nanobot` | Primary behavior-parity target | `main@d81aa5a4a` | `abfcdd481` | 2026-09-03 |
| `lsdefine/GenericAgent` | Architecture/workflow ideas | `main@71cf559fa` | `63f9db74e` | 2026-09-03 |
| `thedotmack/claude-mem` | Memory-architecture ideas | `main@18b3dab76` | `4702c337` | 2026-09-03 |
| `Dataojitori/nocturne_memory` | Memory-architecture ideas | `main@ffb5c709b` | `54c48eea` | 2026-09-03 |
| `openJiuwen/jiuwenswarm` | Architecture/channel ideas | `develop@896664ce0` | `fb43da6c` | 2026-09-03 |

`nanobot` and `GenericAgent` remotes must retain `tagOpt = --no-tags`. Hahobot owns its independent
`v0.x` release line; upstream tags are not imported into the local `v*` namespace.

Related projects such as NanoMate, Hermes Agent, and `soongenwong/claudecode` may inspire local
design, but they are not tracked parity targets.

## Status Legend

- `synced`: the behavior exists locally, possibly through a different implementation.
- `local_extension`: Hahobot-owned behavior for which upstream parity is not the goal.
- `intentional_divergence`: local behavior deliberately differs from upstream.
- `watchlist`: re-evaluate when the related local surface or upstream behavior changes.

## Latest Audit / Port Update — 2026-09-03

### nanobot

Audited 362 linear commits from `abfcdd481` through `d81aa5a4a`; the old boundary is an ancestor, so
no rewrite reconciliation was needed. Three portable reliability/security clusters are adapted to
Hahobot's existing owners:

- detectably credential-bearing `web_fetch` URLs (userinfo or credential-like query keys), including
  any such redirect hop, are never delegated to the third-party Jina reader; fragments are stripped
  from eligible Jina requests and fetch failures log only the origin (`31a71d6cd`, `5f916bbd3`,
  `76f629e92`);
- Git-backed Dream snapshots stage their explicit tracked paths before change detection, so a rapid
  same-size rewrite is committed even when coarse filesystem timestamps do not move
  (`9f5a56f1e`);
- one-shot exec owns the complete subprocess tree: POSIX launches in a new session and kills its
  process group, while Windows uses a kill-on-close Job Object with a `taskkill /T` fallback
  (`d64b84604`, `bcf5d8a6e`).

The larger range is dominated by the upstream React WebUI/TUI, event-projection, plugin-marketplace,
session-backend, pairing, and runner/context refactors. These remain architecture-specific or
intentional divergences from Hahobot's aiohttp/Jinja gateway, incremental JSONL sessions, workspace
skills, and decomposed runtime owners. Three portable reliability clusters are now adapted locally:
strict UID/header-first email filtering with cancellation-safe committed-batch delivery
(`f573ecfe5`, `5c71ef6e4`), bounded off-loop recursive search (`649e3958c`), and single-owner
supervised Telegram polling (`cc05fe6ed`, `302015fde`, `8a928592c`,
`2b4a04fb7`). Cron recovery, MCP readiness, Slack file-download SSRF, and Dream prompt
de-duplication were checked but are already covered locally or do not map to Hahobot's execution
path. See
[`NANOBOT.md`](docs/upstream-parity/NANOBOT.md).

### GenericAgent

Audited 31 commits (22 first-parent) from `63f9db74e` through `71cf559fa`; history advanced linearly.
The range is primarily conductor/desktop/Streamlit, Hub/P2P, and upstream loop-shape work. Its
summary heuristic, history trimming, stream-abort handling, and native Claude header changes do not
map cleanly onto Hahobot's Dream/compaction, provider normalization, cancellation, and retry owners.
The portable `Retry-After` safety boundary from `7ffc95823` is now adapted across both local retry
modes: finite retries stop immediately rather than sleeping on a server hint above 60 seconds,
while persistent retries retain recovery semantics with a 60-second cap. See
[`GENERICAGENT.md`](docs/upstream-parity/GENERICAGENT.md).

### Memory upstreams

`claude-mem` advanced linearly by 142 commits through `18b3dab76`. Bounded startup-context
injection is already covered by Hahobot's ranked/top-k memory limits; hosted trials, telemetry,
marketplace, installer, and Chroma-specific lifecycle work remain outside the local file-first
boundary. `nocturne_memory` advanced linearly by 7 commits through `ffb5c709b`; the only portable
direction is richer memory performance/bloat diagnostics, retained for doctor/admin rather than
copying its frontend build manager or graph store. See
[`MEMORY_UPSTREAMS.md`](docs/upstream-parity/MEMORY_UPSTREAMS.md).

### jiuwenswarm

Audited 348 commits (335 first-parent) from `fb43da6c` through `develop@896664ce0`; history advanced
linearly. MCP prewarming/per-server failure isolation and context-window accounting are already
covered by Hahobot's turn preparation and compaction paths. Orphaned process-group cleanup reinforces
the exec tree-ownership port above. Local-path skill-import hardening is not applicable because
Hahobot exposes no arbitrary-path skill installer. One-gateway-per-workspace ownership is retained
as a future deployment guard; Team/desktop/Web/plugin-marketplace work remains ideas-only. See
[`JIUWENSWARM.md`](docs/upstream-parity/JIUWENSWARM.md).

## Current Snapshot

| Area | Status | Current local disposition |
| --- | --- | --- |
| Tool/runtime policy | `synced` | Central policy controls tool availability, hot reload, doctor output, explicit exec environment passthrough, and finite-number validation before dispatch. |
| Recursive file search | `synced` | Glob/grep traversal runs off the event loop, skips directory symlink descent and special files, and fails after 500,000 paths or a caller-enforced 30-second wall clock; a four-slot gate bounds non-cooperative daemon workers, while timeout-capable concurrent regex matching and a 10,000-character pattern cap prevent GIL-bound backtracking. |
| File editing | `synced` | Exact replacements reject identical old/new text before I/O; oversized reads fail from metadata before allocation. |
| File/config durability | `synced` | Oversized reads fail before allocation; config/admin and cron commits use mode-preserving atomic replacement; Git snapshots detect staged content even across same-size, same-mtime rewrites. |
| Exec isolation | `synced` | Segment-wise allow rules, deny-first matching, assignment/home-path guards, bounded-at-read one-shot output, bounded execution, and POSIX/Windows process-tree ownership remain local invariants. |
| External CLI isolation | `synced` | ClawHub `npx` subprocesses receive a fixed platform/network/npm allowlist instead of inheriting provider keys, tokens, arbitrary parent variables, or loader injection. |
| Provider normalization | `synced` | Empty content, malformed surrogates, reasoning fields, versioned Anthropic adaptive effort, model-specific thinking, provider error detail, and process-global credential isolation are enforced before transport. |
| Provider retry safety | `synced` | Server retry hints up to 60 seconds are honored; oversized/non-finite positive hints stop finite retries and are capped only for persistent recovery. |
| Web fetching | `synced` | Every direct redirect hop is SSRF-validated and DNS-pinned; detectable credential-bearing URLs/chains stay local rather than reaching Jina, and failure logs retain only the origin. |
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
| Bundled skill portability | `synced` | The weather workflow uses one scope-matched HTTPS request, PowerShell-safe `curl.exe`, and platform-neutral PNG output. |
| Subagent modes | `local_extension` | Explore/implement/verify tool boundaries and durable completion announcements extend the local runtime. |
| Cron persistence | `synced` | Syntax validation, cross-process transactions, expiring claims, merged history, worker-pool I/O, cancellation linearization, and safe store rebinding prevent common invalid/duplicate/lost-update paths. |
| Channel streaming | `synced` | Stateful delivery IDs, retry cursors, special-character-safe Telegram fences, and channel-specific overflow handling preserve exactly-once chunk progress within a delivery attempt. |
| Email polling | `synced` | Stable UID search and header-first self/auth/allowlist checks reject unwanted mail before body or attachment download, with 30-second socket I/O, structurally strict From parsing, serialized stop/restart, committed-batch draining, and bounded dedupe reset on UIDVALIDITY namespace changes. Authentication-Results is only a receiving-service policy hint: the nearest field is parsed outside quoted/comment text, exact From-domain matches are required, explicit DMARC failure is rejected, and Seen failures never discard accepted delivery. |
| Telegram polling | `synced` | Completed getUpdates round trips feed a liveness watchdog; stale polling rebuilds with bounded, RetryAfter-aware backoff, readiness-gated sends, credential-free terminal propagation/dependency logs, incrementally owned request pools, and per-step-bounded serialized teardown. |
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
| nanobot | OpenRouter server tools or provider `extraBody` are exposed locally | Merge configured server tools without replacing Hahobot function tools. |
| nanobot | Session retention is redesigned | Session-file retention/archiving and cross-session references adapted to Hahobot's incremental JSONL and session-authority model. |
| nanobot | Reasoning model routing expands | Kimi/MiMo and other model-specific reasoning parameters not already covered locally. |
| nanobot | Remote authenticated MCP becomes an operator requirement | Browser OAuth with explicit callback binding, token storage/redaction, refresh, revocation, and admin-session security. |
| GenericAgent | Workflow or unattended background behavior becomes concrete | Reviewable planning/memory SOPs without copying desktop/conductor structure. |
| claude-mem | Archive sidecar schema changes | `filesRead` / `filesModified` evidence with migration and query semantics. |
| nocturne_memory | Memory diagnostics are expanded | Bloat reporting and stable per-entry IDs without replacing Markdown as source of truth. |
| jiuwenswarm | Config concurrency/security work begins | Cross-process config transactions and secret redaction. |
| jiuwenswarm | Local exec or orchestration requirements materially expand | `jiuwenbox` isolation or Team concepts, only with explicit authority boundaries. |
| jiuwenswarm | Multiple gateway processes may share one workspace | Explicit single-owner or lease semantics in addition to existing cron job claims. |

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
