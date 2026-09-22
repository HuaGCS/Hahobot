# Memory Upstream Notes

Hahobot tracks [`thedotmack/claude-mem`](https://github.com/thedotmack/claude-mem) and
[`Dataojitori/nocturne_memory`](https://github.com/Dataojitori/nocturne_memory) as
memory-architecture inspirations. Neither is a storage-layout parity target.

## Current Boundaries

| Upstream | Audited ref | Previous boundary | Audit result |
| --- | --- | --- | --- |
| `claude-mem` | `main@4520de9e0` (2026-09-21) | `18b3dab76` | 182 commits (164 first-parent) reviewed on 2026-09-22; CJK substring recall maps locally, while hosted UI/telemetry/install and Chroma-specific work do not. |
| `nocturne_memory` | `main@ffb5c709b` (2026-08-27) | `ffb5c709b` | No new commits on 2026-09-22; the prior diagnostics/watchlist disposition is unchanged. |

`claude-mem`'s public repository has been Apache-2.0 since v13.0.0 (`36b0929fa`); its hosted service
is a separate reserved surface. `nocturne_memory` is MIT-licensed.

## Adopted Ideas

| Idea | Hahobot adaptation |
| --- | --- |
| Structured observations | Session/archive JSON sidecars store searchable observations alongside human-readable Markdown. |
| Progressive disclosure | History search returns compact matches; history expansion loads the selected chunk when needed. |
| File timelines | Archive metadata and optional SQLite FTS support session/file/time filtering. |
| Private model-only context | Existing private tags are stripped from user-visible delivery while remaining available to runtime memory logic. |
| Patch/append-oriented writes | Consolidation appends new facts instead of asking a model to rewrite the complete memory file. |
| Concurrent derived indexes | Rebuildable SQLite caches use WAL, busy timeout, and normal synchronous mode. |
| Layered memory maintenance | Dream maintains `PROFILE.md` and `INSIGHTS.md` with confidence/verification metadata. |
| Bounded startup recall | Ranked archive/Memorix context already has top-k and character limits, covering the portable intent of claude-mem's bounded injection. |
| Unsegmented-script recall (`bfe469449`) | Persona SQLite facts queries containing Han, Japanese, Hangul, or Bopomofo use escaped literal substring matching with the existing tag and result bounds; Markdown remains canonical. |

## Intentional Divergences

- Markdown files remain the source of truth: readable, editable, and Git-diffable.
- Chroma, graph databases, embeddings, and SQLite may serve only as replaceable derived indexes.
- Hahobot does not require a hosted sync service, Claude Code hook pipeline, or standalone memory MCP
  service for core recall.
- Core persona memory loads through the local runtime rather than a model-invoked boot/recall
  protocol.
- `PROFILE.md` and `INSIGHTS.md` stay separate so stable user facts and collaboration patterns can
  evolve under different verification rules.

## Active Watchlist

- `claude-mem` `filesRead` / `filesModified` evidence is useful, but adoption requires a versioned
  sidecar migration, write path, and query contract.
- Stable addressable memory-entry IDs from graph-oriented systems may help ranked recall, but must
  not replace Markdown bullets as the canonical representation.
- Nocturne's bloat report is a candidate for local doctor/admin diagnostics; its byte threshold and
  graph maintenance UI are not copied directly.
- Add observation/recall performance counters only when they can be exposed through the existing
  local doctor/admin surfaces without introducing a second frontend or telemetry channel.
- Block-matched patching may inform future Dream edit hygiene, but Hahobot keeps explicit file edits
  and Markdown review rather than adopting graph-node mutation semantics.
- New workflow skills from memory upstreams are demand-driven; they are not automatically bundled.
- Revisit either source when it changes memory semantics, not for hosted UI, telemetry, or release
  churn alone.

See the [current root ledger](../../UPSTREAM_PARITY.md) and the
[complete 2026 audit log](AUDIT_LOG_2026.md) for historical evaluations.
