# nanobot Parity Notes

[`HKUDS/nanobot`](https://github.com/HKUDS/nanobot) is Hahobot's primary behavior-parity target for
runtime, provider, channel, configuration, and persistence changes. Hahobot adapts behavior onto its
own architecture; file-for-file mirroring is not required.

## Current Boundary

- Audited ref: `main@3778e7e62` (2026-08-10; audited 2026-08-11)
- Previous boundary: `55ecda27`
- Range reviewed: 16 commits
- Remote rule: keep `remote.nanobot-upstream.tagOpt = --no-tags`

## Latest Adopted Clusters

| Upstream commits | Local disposition |
| --- | --- |
| `cdb2df49` | `read_file` rejects inputs over 100 MiB from `stat()` before reading. |
| `28102382`, `b2cf37da` | Config and admin writes use a mode-preserving, fsynced atomic replacement helper. |
| `89d8c055` | Provider-bound nested values recursively sanitize malformed UTF-16 surrogates. |
| `79d94553` | Qwen model families receive model-scoped `enable_thinking` behavior without affecting unrelated models on the same endpoint. |
| `4986590b..cf1e801a` | Gemini image requests use the final model-specific aspect-ratio/image-size matrix. |
| `b81c0558`, `299bcf49`, `7c94ba96`, `745757cc`, `259d8a01` | Cron, sessions, history, and skill metadata tolerate compatible string/null/malformed persisted values. |
| `5851bd43`, `81951817`, `fb881543`, `aaf2eef5` | Slack/Feishu preserve fenced tables; Feishu rich payloads tolerate null fields. |
| `78f4c132` | Exec workspace guards recognize assignment-form absolute paths. |
| `017a4946`, `98d66177`, `7e9426d9` | Telegram Markdown splitting always advances, including tiny budgets and minified fences. |
| `9aae7485`, `c1899e2c` | MCP schemas resolve and hoist arbitrary URI-decoded local JSON Pointers into `$defs`. |
| `4e2640f2`, `15e42059` | Dream advances/compacts only after phase 2 completes. |
| `b19039f9..b55b76d7` | Provider length recovery preserves partial text and one-message streaming continuity. |
| `4408cde0..b3d3a3e6` | Generated-image downloads use per-hop SSRF validation, pinned direct DNS, explicit-proxy DNS delegation, byte limits, and content verification; IPv6 unspecified is blocked. |
| `7fd28c9f`, `e633f867`, `39bb20c7`, `4c387f66` | Pending Dream input and malformed idle/raw-archive metadata remain safe and retryable. |
| `92361cbe` | Dream Git history exposes real Dulwich object IDs instead of hex-of-hex identifiers. |
| `511c764f`, `73a00804` | Blank truncated responses continue; invalid cron expressions fail before persistence. |
| `08fe9f7b`, `4e8702a4` | Gemini Flash and versioned Anthropic thinking requests use their current provider-native wire shapes. |
| `a13e29bf`, `170c7083`, `5c4c2cb8` | Telegram fenced code and strict Matrix invite joins retain interoperable channel behavior. |
| `f45436b61` | Unknown slash commands are rejected before model dispatch with localized nearest-command suggestions. |
| `c9a614587`, `a5bc3bfbb`, `75e333a3c`, `af52fbcbc` | Temporary chat semantics are adapted onto the server-rendered WebUI: process-local session state, no persistence/memory/cron ownership, and an explicit persisted **Save copy**. |
| `b3b051761` | `edit_file` rejects identical old/new text before any existing-file read or rewrite. |
| `5e67fbf93` (adapted follow-up) | Nanobot's bounded persistent-session buffer is mapped to Hahobot's one-shot exec: stdout/stderr are drained concurrently with incremental UTF-8 decoding and fixed head/tail retention before the combined response cap. |

## Established Local Mapping

- Runtime and tools: policy gating, doctor reuse, bounded shell execution, `self_inspect`, notebook
  editing, search, MCP, cron, and message routing live in Hahobot's existing registries.
- Providers: local normalization owns reasoning fields, retry/failover, token usage, image request
  shaping, and compatibility fallbacks.
- Persistence: session JSONL, archive sidecars, Dream, skills, and cron keep their local formats while
  accepting compatible upstream shapes.
- Channels: transport-specific rendering and retry state stay in each adapter; manager-generated
  delivery IDs provide retry identity across streaming channels.
- Web surfaces: useful behavior is adapted into the aiohttp/Jinja gateway rather than copying
  nanobot's React/Vite frontend. Temporary chats therefore use `SessionManager`'s bounded in-memory
  namespace and Jinja forms instead of browser-local SPA state. Connection recovery stays local to
  this server-rendered architecture: request-id receipts and `WebUIBroadcaster` own detached turns
  across socket replacement, rather than adopting nanobot's React event-projection layer.

## Intentional Divergences

- Hahobot keeps an independent `v0.x` release/tag namespace.
- The built-in WebUI stays server-rendered and shares the gateway/admin runtime.
- The OpenAI-compatible API remains non-streaming until that public contract is intentionally
  expanded.
- Session persistence is incremental JSONL, so nanobot's whole-file retention/archiver cannot be
  ported mechanically.
- Pairing, triggers, new channel manifests, native runtimes, and broad provider additions remain
  demand-driven rather than automatic parity work.

## Active Watchlist

- DingTalk DM gating and sender-label behavior when that adapter is next modified.
- Kimi/MiMo and other model-scoped reasoning parameters when provider routing expands.
- A Hahobot-native retention/archival design if incremental session stores need bounded history.
- New channel/provider surfaces only when operator demand and local config/admin/test coverage exist.
- Browser OAuth for remote MCP servers, once Hahobot has an explicit contract for callback binding,
  token storage/redaction, refresh/revocation, and authenticated admin initiation.

See the [current root ledger](../../UPSTREAM_PARITY.md) and the
[complete 2026 audit log](AUDIT_LOG_2026.md) for dated commit-by-commit rationale.
