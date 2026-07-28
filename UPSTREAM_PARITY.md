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
| `HKUDS/nanobot` | Primary behavior-parity target | `main@cf1e801a` | `d5658dbc` | 2026-07-27 |
| `lsdefine/GenericAgent` | Architecture/workflow ideas | `main@5c3fc72d` | `d69ec880` (force-rewritten history) | 2026-07-27 |
| `thedotmack/claude-mem` | Memory-architecture ideas | `main@132b4634` | `f5633c1f` | 2026-07-27 |
| `Dataojitori/nocturne_memory` | Memory-architecture ideas | `main@2cbfb8a` | unchanged | 2026-07-27 |
| `openJiuwen/jiuwenswarm` | Architecture/channel ideas | `develop@de623dd9` | `caec89ca` | 2026-07-27 |

`nanobot` and `GenericAgent` remotes must retain `tagOpt = --no-tags`. Hahobot owns its independent
`v0.x` release line; upstream tags are not imported into the local `v*` namespace.

Related projects such as NanoMate, Hermes Agent, and `soongenwong/claudecode` may inspire local
design, but they are not tracked parity targets.

## Status Legend

- `synced`: the behavior exists locally, possibly through a different implementation.
- `local_extension`: Hahobot-owned behavior for which upstream parity is not the goal.
- `intentional_divergence`: local behavior deliberately differs from upstream.
- `watchlist`: re-evaluate when the related local surface or upstream behavior changes.

## Latest Audit — 2026-07-27

### nanobot

Audited 142 commits through `cf1e801a`. Portable changes were adapted as coherent local behavior:

- reject oversized `read_file` inputs before loading (`cdb2df49`);
- mode-preserving atomic config/admin writes (`28102382`, `b2cf37da`);
- recursive malformed UTF-16 surrogate cleanup at provider boundaries (`89d8c055`);
- model-scoped Qwen thinking parameters (`79d94553`);
- Gemini model-specific aspect-ratio and image-size request rules (`4986590b..cf1e801a`);
- tolerant persisted cron/session/history/skill parsing (`b81c0558`, `299bcf49`, `7c94ba96`,
  `745757cc`, `259d8a01`);
- Slack/Feishu fenced-table preservation and malformed Feishu payload tolerance (`5851bd43`,
  `81951817`, `fb881543`, `aaf2eef5`);
- assignment-form exec path guards (`78f4c132`), Telegram split forward progress (`017a4946`,
  `98d66177`, `7e9426d9`), arbitrary local MCP JSON Pointer handling (`9aae7485`, `c1899e2c`),
  completed-only Dream cursor advancement (`4e2640f2`, `15e42059`), and end-to-end length recovery
  (`b19039f9..b55b76d7`).

React/native UI, pairing/triggers, and broad new provider/channel surfaces were reviewed but not
copied. See [`NANOBOT.md`](docs/upstream-parity/NANOBOT.md).

### GenericAgent

Upstream rewrote `main`; the audit used date/first-parent inspection through `5c3fc72d` instead of
treating `d69ec880` as an ancestor. Its empty-text-block fix was already covered by Hahobot's shared
provider normalization. Desktop/Tauri packaging and conductor/runtime structure remain
architecture-specific. See [`GENERICAGENT.md`](docs/upstream-parity/GENERICAGENT.md).

### Memory upstreams

`claude-mem` was audited through `132b4634`; the 183-commit range mainly concerned hosted
SyncHub/worker, Chroma, plugin, and release surfaces. `filesRead` / `filesModified` observation
evidence remains useful but needs a local sidecar and query contract. `nocturne_memory` remained at
`2cbfb8a`, so its file-first/patch-only decisions did not change. See
[`MEMORY_UPSTREAMS.md`](docs/upstream-parity/MEMORY_UPSTREAMS.md).

### jiuwenswarm

Audited 116 commits through `develop@de623dd9`. Hahobot adapted complete cron read-modify-write
locking and atomic replacement from `1d5c54bdf`, then hardened it locally with worker-pool I/O,
claims, cancellation linearization, and workspace-rebind draining. Persisted WebUI media restoration
was adapted through Hahobot's existing guarded `/app/media` route (`94310a3ad`). See
[`JIUWENSWARM.md`](docs/upstream-parity/JIUWENSWARM.md).

## Current Snapshot

| Area | Status | Current local disposition |
| --- | --- | --- |
| Tool/runtime policy | `synced` | Central policy controls tool availability, hot reload, doctor output, and explicit exec environment passthrough. |
| File/config durability | `synced` | Oversized reads fail before allocation; config/admin and cron commits use mode-preserving atomic replacement. |
| Exec isolation | `synced` | Segment-wise allow rules, deny-first matching, assignment/home-path guards, bounded execution, and robust process cleanup remain local invariants. |
| Provider normalization | `synced` | Empty content, malformed surrogates, reasoning fields, model-specific thinking, and provider error detail are normalized before transport. |
| Image generation | `synced` | Gemini and compatible image endpoints receive model-appropriate request fields while persona `/scene` keeps local reference-image behavior. |
| Length recovery | `synced` | Truncated provider segments are retried/merged and streamed as one visible response without losing already-produced text. |
| Hook streaming ownership | `synced` | Composite hooks fan out safely; only the primary output owner suppresses runner-side delta accumulation. |
| MCP schemas | `synced` | Local URI-decoded JSON Pointers are resolved/hoisted into `$defs`, including recursion and unresolved-ref fallback. |
| MCP lifecycle | `synced` | Each connection generation has one owner task; terminated sessions reconnect without cross-task context-manager teardown. |
| Session persistence | `synced` | Atomic rewrites, malformed-row tolerance, bounded strong LRU caching, and checkpoint recovery protect saved conversations. |
| Memory/archive | `local_extension` | Markdown remains source of truth; JSON sidecars and optional SQLite FTS are rebuildable recall indexes. |
| Dream maintenance | `local_extension` | Two-phase reflection updates local memory layers and advances its cursor only after a completed second phase. |
| Skill lifecycle | `local_extension` | Query-aware summaries, usage metadata, derive/supersede/lint commands, and operator review govern local skill growth. |
| Subagent modes | `local_extension` | Explore/implement/verify tool boundaries and durable completion announcements extend the local runtime. |
| Cron persistence | `synced` | Cross-process transactions, expiring claims, merged history, worker-pool I/O, cancellation linearization, and safe store rebinding prevent common duplicate/lost-update paths. |
| Channel streaming | `synced` | Stateful delivery IDs, retry cursors, Telegram fence balancing, and channel-specific overflow handling preserve exactly-once chunk progress within a delivery attempt. |
| Slack/Feishu rendering | `synced` | Fenced tables stay intact and malformed/null rich-message fields degrade safely. |
| WebUI persisted media | `synced` | Initial history and live frames share the traversal-guarded `workspace/out` media mapping. |
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
| nanobot | Session retention is redesigned | Session-file retention/archiving adapted to Hahobot's incremental JSONL model. |
| nanobot | Reasoning model routing expands | Kimi/MiMo and other model-specific reasoning parameters not already covered locally. |
| GenericAgent | Workflow or unattended background behavior becomes concrete | Reviewable planning/memory SOPs without copying desktop/conductor structure. |
| claude-mem | Archive sidecar schema changes | `filesRead` / `filesModified` evidence with migration and query semantics. |
| nocturne_memory | Ranked recall needs stable addressability | Stable per-entry IDs and metadata without replacing Markdown as source of truth. |
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
