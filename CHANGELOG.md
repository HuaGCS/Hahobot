# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.8] - 2026-07-28

### Added
- **Explicit Mem0 backfill:** preview and migrate eligible pre-Mem0 local memory
  with `hahobot memory shared backfill`, including `--dry-run`, `--persona`,
  `--json`, and explicit `--force` resend support.
- **Durable migration receipts:** queue every candidate before delivery, retain
  offline work in the existing SQLite outbox, and skip unchanged acknowledged
  content on ordinary reruns.

### Security
- **Conservative memory routing:** send only the default user profile to the
  Hermes-visible public namespace; keep collaboration insights and long-term
  memory persona-private, remove `<private>` content, and reject source-file
  symlinks that escape the active workspace.

## [0.1.7] - 2026-07-28

### Fixed
- **Mem0 SQLite startup concurrency:** read the local snapshot before scheduling
  its background refresh, retry first-use WAL mode upgrades that lose a
  cross-thread or cross-process lock race, and always close connections when
  SQLite PRAGMA initialization fails.

## [0.1.6] - 2026-07-28

### Added
- **Layered Mem0 synchronization:** optionally share a Hermes-compatible public
  memory namespace across devices while keeping separate persona-private Mem0
  namespaces. Local Markdown and SQLite memory remain authoritative and provide
  the offline fallback.
- **Durable shared-memory delivery:** queue Mem0 writes in SQLite, retain offline
  recall snapshots, and expose hot-reloadable `memory.shared.*` settings in the
  built-in admin UI.

### Fixed
- **Shared-memory privacy and reliability:** route
  `<persona-private>...</persona-private>` content only to the active persona,
  strip private content and transport identifiers from public writes, reject
  namespace collisions, fail closed on malformed privacy markers, and preserve
  cached recall when Mem0 returns a potentially truncated 1,000-row refresh.
- **SQLite index stability:** avoid reapplying WAL mode on every read-only memory
  lookup, so reusing an unchanged FTS index no longer mutates its database file.
- **Upstream compatibility:** harden channel delivery retries and streaming,
  preserve legacy runtime entry points, and keep upstream parity records split
  into a compact current ledger plus archived audit history.

## [0.1.5] - 2026-07-21

### Added
- **Apple-inspired gateway UI:** unified the WebUI, admin, and status surfaces
  with restrained translucent materials, responsive navigation, compact language
  dropdowns, cross-surface links, and reduced-motion-aware page transitions.

### Fixed
- **Runtime hardening:** validate each top-level shell segment against exec
  allowlists, load legacy cron records defensively, bound the in-memory session
  cache, prevent non-positive message split loops, and align Kimi K2.5/K2.6
  temperature handling with Moonshot's thinking-mode contract.

## [0.1.4] - 2026-07-17

### Added
- **Restart-aware admin config:** restart-required fields are compared with the
  current process startup baseline and listed by path after a save. Gateway
  changes can restart the current process from the authenticated admin page,
  while `api.*` and `a2a.*` changes show the separate `hahobot serve` command.

### Changed
- **Web UI and admin polish:** refined press, hover, focus, tooltip, typing, and
  recording motion; improved mobile composer layout and status-page typography;
  and added reduced-motion, reduced-transparency, and high-contrast handling.

## [0.1.3] - 2026-07-08

### Fixed
- **CLI interactive streaming:** streamed assistant replies no longer leak raw
  terminal control codes (`\x1b[2K`, `\x1b[?25l`, …) as literal `?[2K` text. In the
  prompt_toolkit REPL, `StreamRenderer` now buffers deltas and renders once through
  the prompt_toolkit-safe path instead of driving Rich `Live` directly on stdout.
  Single-message mode (`hahobot agent -m …`) keeps its live animation unchanged.
- **Memory history read:** `MemoryStore._read_last_entry` no longer crashes with
  `UnicodeDecodeError` when the 4096-byte tail read starts mid-character in
  non-ASCII (`ensure_ascii=False`) history, which had crashed consolidation.

## [0.1.2] - 2026-07-07

### Fixed
- Providers/MCP upstream parity sync: Anthropic thinking passthrough, GitHub
  Copilot token refresh, and MCP tool-name length handling.

## [0.1.1] - 2026-07-07

### Added
- OpenClaw-compatible per-skill config and an `openclaw` CLI alias.
- Web UI: delete conversations, immediate processing indicator, and live
  working-checkpoint streaming during a turn.
- Admin: tabbed config sections, add-provider-by-type, and a model picker that
  fetches a provider's `/models` list with a provider dropdown.

## [0.1.0] - 2026-07-07

- Initial tagged release of Hahobot, the workspace-first local agent runtime
  (CLI agent, multi-channel gateway, OpenAI-compatible API).

[0.1.8]: https://github.com/HuaGCS/Hahobot/compare/v0.1.7...v0.1.8
[0.1.7]: https://github.com/HuaGCS/Hahobot/compare/v0.1.6...v0.1.7
[0.1.6]: https://github.com/HuaGCS/Hahobot/compare/v0.1.5...v0.1.6
[0.1.5]: https://github.com/HuaGCS/Hahobot/compare/v0.1.4...v0.1.5
[0.1.4]: https://github.com/HuaGCS/Hahobot/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/HuaGCS/Hahobot/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/HuaGCS/Hahobot/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/HuaGCS/Hahobot/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/HuaGCS/Hahobot/releases/tag/v0.1.0
