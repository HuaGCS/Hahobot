# GenericAgent Adoption Notes

[`lsdefine/GenericAgent`](https://github.com/lsdefine/GenericAgent) is an architecture and workflow
ideas upstream, not a behavior-parity target. Hahobot borrows concepts through existing skills,
memory, hooks, subagents, Dream, heartbeat, and admin surfaces.

## Current Boundary

- Audited ref: `main@63f9db74e` (2026-08-12; audited on 2026-08-12)
- Previous recorded boundary: `d426d45e`
- Range reviewed: 7 linear commits (the older force-rewrite note remains historical).
- Remote rule: keep `remote.genericagent-upstream.tagOpt = --no-tags`

## Current Mapping

| GenericAgent idea | Hahobot owner | Status |
| --- | --- | --- |
| Planning and verification SOPs | Bundled `workflow-core`, `plan`, and `verify` skills plus `spawn(mode=verify)` | `synced` |
| Skill accumulation | `/skill derive`, lifecycle metadata, supersede/lint, and operator review | `local_extension` |
| Layered user/context memory | `USER.md`, `PROFILE.md`, `INSIGHTS.md`, archive sidecars, and Dream | `synced` |
| Memory maintenance | Dream phase 1/2, idle compact, history archive, metadata hygiene | `local_extension` |
| Hookable execution | `AgentHook`, composite hooks, and the external hook bridge | `synced` |
| Background workflows | Explicit cron, heartbeat, Dream, runtime status, and Star-Office push | `local_extension` |
| Minimal autonomous loop | Richer Hahobot runtime surfaces remain first-class | `intentional_divergence` |

The latest range is Hub/P2P, conductor, and world-model-specific; its overload retry behavior is
already covered by Hahobot's provider retry layer, so no new local change was needed. The prior
Responses incomplete/failed-event and maximum-effort changes map to Hahobot's existing Responses
parsing and updated versioned Anthropic effort handling. `7ffc9582`'s oversized
`Retry-After` cap is useful but remains deferred until finite/persistent retry modes share one
documented policy.

## Intentional Divergences

- Hahobot does not copy GenericAgent's file layout, minimal-tool philosophy, conductor runtime, or
  desktop/Tauri applications.
- Multi-channel delivery, gateway/admin/status pages, review/doctor commands, MCP, and hot reload are
  deliberate product surfaces rather than complexity to remove for parity.
- Plans and derived skills remain drafts until independently reviewed; unattended self-improvement
  is not enabled merely because an upstream workflow can generate it.
- Hahobot splits background behavior across explicit services instead of introducing a second
  autonomous scheduler abstraction.

## Active Watchlist

- Adopt workflow or memory SOP improvements only when they map cleanly to current Hahobot skills and
  preserve operator review.
- Revisit richer skill promotion/packaging only after `/skill derive` has concrete usage pressure.
- Re-evaluate autonomous background behavior only with explicit authority, visibility, and failure
  boundaries.
- Treat future force-pushes as new audit lineages and record the comparison method in the root ledger.
- Revisit a maximum accepted `Retry-After` when provider retry policy next changes.

See the [current root ledger](../../UPSTREAM_PARITY.md) and the
[complete 2026 audit log](AUDIT_LOG_2026.md) for the older detailed matrix and dated decisions.
