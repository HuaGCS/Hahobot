# nanobot Parity Notes

[`HKUDS/nanobot`](https://github.com/HKUDS/nanobot) is Hahobot's primary behavior-parity target for
runtime, provider, channel, configuration, and persistence changes. Hahobot adapts behavior onto its
own architecture; file-for-file mirroring is not required.

## Current Boundary

- Audited ref: `main@6bf604d43` (2026-09-22; audited 2026-09-22)
- Previous boundary: `d81aa5a4a`
- Range reviewed: 242 linear commits; the previous boundary remains an ancestor
- Remote rule: keep `remote.nanobot-upstream.tagOpt = --no-tags`

## Latest Adopted Clusters

| Upstream commits | Local disposition |
| --- | --- |
| `01760b738`, `8509432dc`, `b04aeeac0` | Shared message splitting preserves leading indentation, handles CRLF boundaries, drops empty chunks, and always advances. Telegram keeps its separate fence-aware splitter. |
| `eddfa0dd6` | Registered plain-value tool hints apply `agents.defaults.toolHintMaxLength`, matching path/command/fallback hints instead of bypassing the cap. |
| `7aaff4f66` | Relative exec `working_dir` values resolve from the configured workspace; the existing resolved-path workspace guard still rejects escapes. |
| `104917aae` | Glob and grep treat `**` as zero or more complete path segments, including a match at the pattern root, while retaining traversal budgets and Windows-separator normalization. |
| `835cac0ae`, `b1d54b2ca` | Cron add requires exactly one schedule field, positive intervals/nonblank expressions, and a future one-shot time. |
| `12c2c95cb`, `b6b7caafd` | JSON OpenAI-compatible requests reject non-boolean, non-null `stream`; `null` remains non-streaming and `true` remains an intentional unsupported contract. |
| `9b50a0227`, `0afebd474` | QQ inbound attachment downloads asynchronously validate the initial URL before opening an HTTP session and reject redirects. |
| `47f77bdbc` | Email From parsing accepts valid international display names and receiver authentication parsing supports quoted mailbox identities while preserving unique-mailbox, exact-domain, and strict IDNA checks. |
| `f573ecfe5`, `5c71ef6e4` | Email polling resolves stable UIDs, skips already-processed messages before FETCH, and performs self-address, mailbox-authentication-policy, and allowlist checks on headers before downloading an accepted body or attachment. Local hardening adds 30-second socket I/O, unique mailbox-only From parsing, serialized poll ownership with cancellation-safe committed-batch draining, top-level parsing of the nearest Authentication-Results header, exact From-domain matches, explicit DMARC-failure rejection, UIDVALIDITY-aware dedupe, and best-effort Seen updates. |
| `649e3958c` | Recursive glob/grep scans run off-loop with cooperative cancellation, stable traversal, no directory-symlink descent, special-file avoidance, 500,000-path / caller-enforced 30-second budgets, exact acquire/release ownership for a four-slot non-cooperative daemon-worker cap, and timeout-capable concurrent regex matching with a 10,000-character pattern cap. |
| `cc05fe6ed`, `302015fde`, `8a928592c`, `2b4a04fb7` | Telegram long polling tracks completed getUpdates round trips, rebuilds a stale app with 5–300 second RetryAfter-aware backoff, gates delivery on readiness, prevents PTB/HTTPX token-URL logging, redacts bot/proxy credentials from surfaced errors, incrementally owns both request pools, and serializes per-step-bounded teardown plus complete supervisor stop/restart ownership. |
| `31a71d6cd`, `5f916bbd3`, `76f629e92` | `web_fetch` keeps userinfo/query-credential initial URLs and redirect chains on the direct local path, strips fragments before eligible Jina requests, and logs only URL origins on failures. |
| `9f5a56f1e` | Git-backed Dream snapshots stage their explicit tracked paths before status inspection, detecting rapid same-size rewrites even when mtime is unchanged. |
| `d64b84604`, `bcf5d8a6e` | One-shot exec owns and terminates complete subprocess trees: POSIX process groups and Windows kill-on-close Job Objects, with a Windows `taskkill /T` fallback. |
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
| `99e07e138` | Tool JSON Schema `number` fields reject non-finite values after casting, including nested object/array paths. |
| `057e8f7af` | Matrix text and media thread replies use room + root-event session overrides while keeping room-scoped delivery. |
| `f5cf4dcd2` | Configured OpenAI-compatible provider keys go directly to the SDK client and never mutate process-global environment variables or aliases. |
| `b14ac4c40`, `72d3ce6b2` | The bundled weather workflow uses one scope-matched HTTPS request, PowerShell-safe `curl.exe`, and a platform-neutral PNG output path. |
| `ec3dfb21b`, `a0e60116a`, `abfcdd481` | The CLI-child environment boundary is adapted to Hahobot's ClawHub `npx` owner: platform/network/npm essentials remain available while unrelated secrets and loader injection are excluded. |

## Established Local Mapping

- Runtime and tools: policy gating, doctor reuse, bounded shell execution, `self_inspect`, notebook
  editing, search, MCP, cron, and message routing live in Hahobot's existing registries. Search
  preserves local workspace/ignore/pagination semantics while moving bounded traversal off-loop.
  Exec process-tree ownership is adapted to the one-shot runner instead of upstream's persistent
  shell.
- Providers: local normalization owns reasoning fields, retry/failover, token usage, image request
  shaping, and compatibility fallbacks.
- Persistence: session JSONL, archive sidecars, Dream, skills, and cron keep their local formats while
  accepting compatible upstream shapes.
- Channels: transport-specific rendering and retry state stay in each adapter; manager-generated
  delivery IDs provide retry identity across streaming channels. Telegram rebuilds preserve its
  stream buffers and localized capability-driven command menu, while email retains BaseChannel's
  secondary authorization check after its earlier header-only filter. Telegram propagates only a
  sanitized terminal startup exception so the manager's second log cannot expose its token;
  Authentication-Results remains a receiving-service hint rather than local cryptographic email
  verification.
- Web surfaces: useful behavior is adapted into the aiohttp/Jinja gateway rather than copying
  nanobot's React/Vite frontend. Temporary chats therefore use `SessionManager`'s bounded in-memory
  namespace and Jinja forms instead of browser-local SPA state. Connection recovery stays local to
  this server-rendered architecture: request-id receipts and `WebUIBroadcaster` own detached turns
  across socket replacement, rather than adopting nanobot's React event-projection layer.
- Web fetching: Jina is a third-party readability path, never a transport for URL
  credentials detectable in userinfo or query keys. Direct fetch retains per-hop SSRF checks and
  pinned DNS; Jina is considered only after a successful credential-free local redirect preflight.
  Path-embedded secrets cannot be detected reliably and must not be passed to `web_fetch`.

## Intentional Divergences

- Hahobot keeps an independent `v0.x` release/tag namespace.
- The built-in WebUI stays server-rendered and shares the gateway/admin runtime.
- Agent Plugins/marketplace and the React PWA are not copied; local workspace skills, explicit MCP
  configuration, and the aiohttp/Jinja WebUI remain the product boundary.
- The OpenAI-compatible API remains non-streaming until that public contract is intentionally
  expanded.
- Session persistence is incremental JSONL, so nanobot's whole-file retention/archiver cannot be
  ported mechanically.
- Pairing, triggers, new channel manifests, native runtimes, and broad provider additions remain
  demand-driven rather than automatic parity work.
- The upstream event-projection/session-backend and runner/context decompositions are not copied;
  Hahobot keeps incremental JSONL sessions and its existing turn/runtime owner split.

## Active Watchlist

- DingTalk DM gating and sender-label behavior when that adapter is next modified.
- Kimi/MiMo and other model-scoped reasoning parameters when provider routing expands.
- A Hahobot-native retention/archival design if incremental session stores need bounded history.
- New channel/provider surfaces only when operator demand and local config/admin/test coverage exist.
- Browser OAuth for remote MCP servers, once Hahobot has an explicit contract for callback binding,
  token storage/redaction, refresh/revocation, and authenticated admin initiation.
- Private tool/MCP/hook diagnostics for temporary turns, once one task-local redaction contract can
  cover every log sink instead of partially copying `264025107` / `d37d7c07c`.

See the [current root ledger](../../UPSTREAM_PARITY.md) and the
[complete 2026 audit log](AUDIT_LOG_2026.md) for dated commit-by-commit rationale.
