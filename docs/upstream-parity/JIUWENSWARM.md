# jiuwenswarm Adoption Notes

[`openJiuwen/jiuwenswarm`](https://atomgit.com/openJiuwen/jiuwenswarm) is an Apache-2.0
architecture/channel ideas upstream. It is hosted on AtomGit, so audits may require a temporary Git
clone or AtomGit web inspection rather than GitHub APIs.

## Current Boundary

- Audited ref: `develop@1b2221adf` (2026-09-22)
- Previous boundary: `896664ce0`
- Range reviewed: 454 commits, 346 first-parent; history advanced linearly

## Adopted Or Mapped Ideas

| Upstream idea | Hahobot disposition | Status |
| --- | --- | --- |
| Huawei Xiaoyi A2A WebSocket channel | `channels.xiaoyi` implements signed outbound connections, init/heartbeat frames, inbound bus routing, and artifact-update replies. | `synced` |
| Complete persisted-store transaction lock (`1d5c54bdf`) | Cron locks the fresh read-modify-write transaction and commits by atomic replace. | `synced` |
| Async persistence separation | Hahobot adds a dedicated cron store worker pool, pinned workspace context, and nonblocking gateway behavior. | `local_extension` |
| Execution ownership | Persisted claims, bounded leases, merged outcomes, separate sleeper/execution tasks, and cancellation linearization harden local cron delivery. | `local_extension` |
| Persisted WebUI media (`94310a3ad`) | Existing server-rendered history uses the guarded `workspace/out` `/app/media` mapping after refresh. | `synced` |
| PLAN/AGENT/CODE modes | Existing plan/verify skills and explore/implement/verify subagent modes cover the useful authority split. | `synced` |
| Self-evolving skills | `/skill derive`, lifecycle metadata, supersede/lint, and explicit review provide a bounded local analogue. | `intentional_divergence` |
| Session/config safety deltas | Collision-safe session paths, scoped deletion, redacted config/admin output, and atomic persistence already cover the portable traversal/masking changes. | `synced` |
| Stream/cancellation/cron reliability | Existing whitespace-preserving streams, task/tool cleanup, claimed cron transactions, and crash-ambiguity rules cover the behavioral intent. | `synced` |
| MCP prewarm and failure isolation (`b2c8c0ca5`, `7ebebe3fe`) | Turn preparation connects servers concurrently and preserves healthy servers when one connection fails. | `synced` |
| Context-window accounting (`f2d661384`) | Provider-aware limits and local compaction already bound assembled turn context. | `synced` |
| Orphaned process groups (`92ff09692`) | The analogous one-shot exec boundary now owns POSIX sessions/process groups and Windows Job Objects. | `synced` |
| Past one-shot schedule rejection (`63b796bbe`) | Cron add rejects `at` values that are not in the future; this reinforces the same boundary ported from nanobot. | `synced` |

## Intentional Divergences

- Hahobot does not copy distributed Team mode, multi-instance authority, terminal/TUI, or the
  `jiuwenbox` runtime without a concrete orchestration or isolation requirement.
- The existing shell workspace guard and process controls remain the local execution boundary;
  introducing a second sandbox service requires its own threat model.
- Experience memory and context compression map onto Hahobot's archive, Dream, layered persona
  memory, and compaction surfaces rather than a parallel memory subsystem.
- WebUI remains in the aiohttp/Jinja gateway instead of adopting another standalone frontend.
- Arbitrary local-path skill import hardening (`0da7b2ce3`) is not applicable because Hahobot's
  `/skill install` does not expose a local-path import surface.

## Active Watchlist

- Cross-process configuration transactions when multiple writers become a supported deployment.
- Secret redaction across config/admin diagnostics and persisted runtime snapshots.
- `jiuwenbox` only if local exec isolation requirements exceed the current workspace guard.
- Team/distributed orchestration only with explicit operator authority, ownership, recovery, and
  observability contracts.
- Warm pools and system-operation inheritance only if Hahobot introduces a concrete multi-tenant
  runtime; they are not needed for the current single-instance workspace model.
- Explicit one-gateway-per-workspace ownership if deployment expands beyond one process; existing
  cron claims prevent duplicate job execution but are not a general gateway ownership lease.

The 2026-09-22 range's event-loop, archive/config, Team, marketplace, UI, and tool-system changes
were reviewed. They are already covered by current owners, architecture-specific, or remain
ideas-only; no parallel service or frontend was introduced.

See the [current root ledger](../../UPSTREAM_PARITY.md) and the
[complete 2026 audit log](AUDIT_LOG_2026.md) for detailed dated analysis.
