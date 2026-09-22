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
| `HKUDS/nanobot` | Primary behavior-parity target | `main@6bf604d43` | `d81aa5a4a` | 2026-09-22 |
| `lsdefine/GenericAgent` | Architecture/workflow ideas | `main@9d1add778` | `71cf559fa` | 2026-09-22 |
| `thedotmack/claude-mem` | Memory-architecture ideas | `main@4520de9e0` | `18b3dab76` | 2026-09-22 |
| `Dataojitori/nocturne_memory` | Memory-architecture ideas | `main@ffb5c709b` | `ffb5c709b` | 2026-09-22 |
| `openJiuwen/jiuwenswarm` | Architecture/channel ideas | `develop@1b2221adf` | `896664ce0` | 2026-09-22 |

`nanobot` and `GenericAgent` remotes must retain `tagOpt = --no-tags`. Hahobot owns its independent
`v0.x` release line; upstream tags are not imported into the local `v*` namespace.

Related projects such as NanoMate, Hermes Agent, and `soongenwong/claudecode` may inspire local
design, but they are not tracked parity targets.

## Status Legend

- `synced`: the behavior exists locally, possibly through a different implementation.
- `local_extension`: Hahobot-owned behavior for which upstream parity is not the goal.
- `intentional_divergence`: local behavior deliberately differs from upstream.
- `watchlist`: re-evaluate when the related local surface or upstream behavior changes.

## Latest Audit / Port Update — 2026-09-22

### nanobot

Audited 242 linear commits from `d81aa5a4a` through `main@6bf604d43`; the old boundary remains an
ancestor. Portable changes were adapted onto Hahobot's existing owners:

- shared message splitting preserves indentation and CRLF boundaries, suppresses empty chunks, and
  always advances (`01760b738`, `8509432dc`, `b04aeeac0`); plain-value tool hints now obey their
  configured cap (`eddfa0dd6`);
- relative exec working directories resolve from the configured workspace (`7aaff4f66`), and
  glob/grep implement `**` as zero-or-more complete path segments (`104917aae`);
- cron add rejects conflicting schedule fields and past one-shot times (`835cac0ae`, `b1d54b2ca`),
  while JSON OpenAI-compatible requests require `stream` to be boolean or null and retain the local
  non-streaming contract (`12c2c95cb`, `b6b7caafd`);
- QQ attachment downloads validate the initial target before opening a client and refuse redirects
  (`9b50a0227`, `0afebd474`); email accepts valid international display names and quoted mailbox
  authentication identities without weakening unique-From or strict IDNA checks (`47f77bdbc`).

Slow-client WebUI isolation, Matrix stream retry ownership, provider tool-call content preservation,
incremental UTF-8 exec decoding, and provider-pool exception handling were checked and are already
covered locally. The range's SPA/TUI, plugin, pairing, broad provider/channel, and storage-layout
work remains architecture-specific or demand-driven. Temporary-chat/private diagnostic redaction is
retained on the watchlist because it needs one task-local logging policy across runner, MCP, and hook
owners rather than a partial copy. See [`NANOBOT.md`](docs/upstream-parity/NANOBOT.md).

### GenericAgent

Audited 9 linear commits from `71cf559fa` through `main@9d1add778`. They concern desktop/P2P flows
and urllib3 socket-cancellation internals that do not map cleanly to Hahobot's gateway, channel, or
aiohttp/provider owners, so no behavior was ported. See
[`GENERICAGENT.md`](docs/upstream-parity/GENERICAGENT.md).

### Memory upstreams

`claude-mem` advanced by 182 commits (164 first-parent) from `18b3dab76` through
`main@4520de9e0`. Its CJK-oriented search fallback is adapted to the rebuildable persona SQLite
index: queries containing Han, Japanese, Hangul, or Bopomofo use literal, escaped substring matching
with the existing tag and result bounds (`bfe469449`). Hosted UI/telemetry/installer and
Chroma-specific work remain outside the file-first boundary; `filesRead` / `filesModified` evidence
still needs a versioned sidecar contract. `nocturne_memory` did not advance from `main@ffb5c709b`.
See [`MEMORY_UPSTREAMS.md`](docs/upstream-parity/MEMORY_UPSTREAMS.md).

### jiuwenswarm

Audited 454 commits (346 first-parent) from `896664ce0` through `develop@1b2221adf`; history advanced
linearly. Its rejection of past one-shot cron times (`63b796bbe`) reinforces the schedule boundary
ported with nanobot. Event-loop ownership, archive/config, Team, marketplace, UI, and tool-system
changes were reviewed but are already covered, architecture-specific, or ideas-only. Cross-process
config transactions/secret redaction and one-gateway-per-workspace ownership remain explicit
watchlist items. See [`JIUWENSWARM.md`](docs/upstream-parity/JIUWENSWARM.md).

## Current Snapshot

| Area | Status | Current local disposition |
| --- | --- | --- |
| Tool/runtime policy | `synced` | Central policy controls tool availability, hot reload, doctor output, explicit exec environment passthrough, finite-number validation before dispatch, and bounded plain-value tool hints. |
| Recursive file search | `synced` | Glob/grep traversal runs off the event loop, skips directory symlink descent and special files, and fails after 500,000 paths or a caller-enforced 30-second wall clock; a four-slot gate bounds non-cooperative daemon workers, timeout-capable concurrent regex matching and a 10,000-character pattern cap prevent GIL-bound backtracking, and `**` matches zero or more complete path segments. |
| File editing | `synced` | Exact replacements reject identical old/new text before I/O; oversized reads fail from metadata before allocation. |
| File/config durability | `synced` | Oversized reads fail before allocation; config/admin and cron commits use mode-preserving atomic replacement; Git snapshots detect staged content even across same-size, same-mtime rewrites. |
| Exec isolation | `synced` | Segment-wise allow rules, deny-first matching, assignment/home-path guards, workspace-relative working directories, bounded-at-read one-shot output, bounded execution, and POSIX/Windows process-tree ownership remain local invariants. |
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
| Memory/archive | `local_extension` | Markdown remains source of truth; JSON sidecars and optional SQLite FTS are rebuildable recall indexes, with literal substring fallback for CJK-family scripts that `unicode61` cannot segment reliably. |
| Dream maintenance | `local_extension` | Two-phase reflection updates local memory layers, advances only after completed phase 2, preserves all pending history, and emits real Git object IDs. |
| Skill lifecycle | `local_extension` | Query-aware summaries, usage metadata, derive/supersede/lint commands, and operator review govern local skill growth. |
| Bundled skill portability | `synced` | The weather workflow uses one scope-matched HTTPS request, PowerShell-safe `curl.exe`, and platform-neutral PNG output. |
| Subagent modes | `local_extension` | Explore/implement/verify tool boundaries and durable completion announcements extend the local runtime. |
| Cron persistence | `synced` | Add-time validation requires exactly one schedule form and a future one-shot time; syntax validation, cross-process transactions, expiring claims, merged history, worker-pool I/O, cancellation linearization, and safe store rebinding prevent common invalid/duplicate/lost-update paths. |
| Channel streaming | `synced` | Stateful delivery IDs, retry cursors, indentation/CRLF-safe generic message splitting, special-character-safe Telegram fences, and channel-specific overflow handling preserve exactly-once chunk progress within a delivery attempt. |
| Email polling | `synced` | Stable UID search and header-first self/auth/allowlist checks reject unwanted mail before body or attachment download, with 30-second socket I/O, structurally strict From parsing that still accepts valid international display names, serialized stop/restart, committed-batch draining, and bounded dedupe reset on UIDVALIDITY namespace changes. Authentication-Results is only a receiving-service policy hint: the nearest field is parsed outside quoted/comment text, quoted mailbox identities are supported, exact strict-IDNA From-domain matches are required, explicit DMARC failure is rejected, and Seen failures never discard accepted delivery. |
| QQ inbound media | `synced` | Attachment URLs pass asynchronous SSRF validation before the HTTP session is opened, and redirects are refused so a public URL cannot hop to a private target. |
| OpenAI-compatible API | `intentional_divergence` | JSON `stream` accepts only boolean or null; `true` remains explicitly unsupported while false/null use the fixed-session non-streaming response contract. |
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
| nanobot | Private diagnostics or temporary-turn logging is redesigned | One task-local redaction policy spanning runner, MCP, hooks, and logs; do not copy only one sink. |
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
