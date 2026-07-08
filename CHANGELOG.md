# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[0.1.3]: https://github.com/HuaGCS/Hahobot/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/HuaGCS/Hahobot/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/HuaGCS/Hahobot/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/HuaGCS/Hahobot/releases/tag/v0.1.0
