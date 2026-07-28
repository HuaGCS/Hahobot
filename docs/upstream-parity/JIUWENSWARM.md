# jiuwenswarm Adoption Notes

[`openJiuwen/jiuwenswarm`](https://atomgit.com/openJiuwen/jiuwenswarm) is an Apache-2.0
architecture/channel ideas upstream. It is hosted on AtomGit, so audits may require a temporary Git
clone or AtomGit web inspection rather than GitHub APIs.

## Current Boundary

- Audited ref: `develop@de623dd9` (2026-07-26; audited on 2026-07-27)
- Previous boundary: `caec89ca`
- Range reviewed: 116 commits

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

## Intentional Divergences

- Hahobot does not copy distributed Team mode, multi-instance authority, terminal/TUI, or the
  `jiuwenbox` runtime without a concrete orchestration or isolation requirement.
- The existing shell workspace guard and process controls remain the local execution boundary;
  introducing a second sandbox service requires its own threat model.
- Experience memory and context compression map onto Hahobot's archive, Dream, layered persona
  memory, and compaction surfaces rather than a parallel memory subsystem.
- WebUI remains in the aiohttp/Jinja gateway instead of adopting another standalone frontend.

## Active Watchlist

- Cross-process configuration transactions when multiple writers become a supported deployment.
- Secret redaction across config/admin diagnostics and persisted runtime snapshots.
- `jiuwenbox` only if local exec isolation requirements exceed the current workspace guard.
- Team/distributed orchestration only with explicit operator authority, ownership, recovery, and
  observability contracts.

See the [current root ledger](../../UPSTREAM_PARITY.md) and the
[complete 2026 audit log](AUDIT_LOG_2026.md) for detailed dated analysis.
