# GenericAgent Adoption Notes

[`lsdefine/GenericAgent`](https://github.com/lsdefine/GenericAgent) is an architecture and workflow
ideas upstream, not a behavior-parity target. Hahobot borrows concepts through existing skills,
memory, hooks, subagents, Dream, heartbeat, and admin surfaces.

## Current Boundary

- Audited ref: `main@71cf559fa` (2026-09-02; audited on 2026-09-03)
- Previous recorded boundary: `63f9db74e`
- Range reviewed: 31 commits, 22 first-parent; history advanced linearly.
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
| Provider retry-delay safety | Shared `LLMProvider._run_with_retry` finite/persistent policy | `synced` |
| Minimal autonomous loop | Richer Hahobot runtime surfaces remain first-class | `intentional_divergence` |

The latest range is primarily conductor/desktop/Streamlit, Hub/P2P, and upstream loop-shape work;
no direct local port was needed. Its summary false-positive fix (`f06d55038`) targets a heuristic
Hahobot does not use, linear history trimming (`0c235a806`) does not match local Dream/compaction,
and stream/retry abort changes (`3327a6c88`, `3d62523d4`) are already represented by Hahobot's
cancellation and provider retry owners. Native Claude API-header handling (`c9cb4b53e`) belongs to a
different provider construction path. `7ffc95823`'s oversized `Retry-After` cap is adapted into the
shared retry owner: positive finite hints through 60 seconds are honored, finite/standard retry
returns the current transient response immediately for larger or infinite hints, and persistent
retry caps those hints at 60 seconds so its explicit keep-recovering contract remains intact.

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

See the [current root ledger](../../UPSTREAM_PARITY.md) and the
[complete 2026 audit log](AUDIT_LOG_2026.md) for the older detailed matrix and dated decisions.
