<div align="center">
  <img src="hahobot_logo.png" alt="hahobot" width="420">
  <h1>hahobot</h1>
  <p>Persona-first local AI agent runtime and companion framework.</p>
  <p>
    <img src="https://img.shields.io/badge/status-local_project-orange" alt="Local Project">
    <img src="https://img.shields.io/badge/python-%3E%3D3.11-blue" alt="Python">
    <img src="https://img.shields.io/badge/license-MIT-green" alt="License">
    <img src="https://img.shields.io/badge/package-source_only-lightgrey" alt="Source Only">
  </p>
</div>

`hahobot` is the canonical local project in this repository.

It started from the lightweight runtime and channel/tool/provider architecture of
[HKUDS/nanobot](https://github.com/HKUDS/nanobot), moved toward the persona and companion
workflow popularized by [shenmintao/NanoMate](https://github.com/shenmintao/NanoMate), and
adapts the "context files + persistent memory + reflective self-maintenance" philosophy from
[Hermes Agent](https://hermes-agent.nousresearch.com/docs/getting-started/quickstart) into a
workspace-first implementation that is easy to inspect and modify locally.

This repo is not a thin mirror of upstream `nanobot`. It is a reorganized local runtime with its
own persona model, memory layout, gateway/admin workflow, compatibility layer, and companion
oriented built-in skills.

## Table of Contents

- [Project DNA](#project-dna)
- [What Hahobot Ships](#what-hahobot-ships)
- [Install](#install)
- [Quick Start](#quick-start)
- [Workspace Model](#workspace-model)
- [Personas and Companion Workflow](#personas-and-companion-workflow)
- [Memory and Self-Iteration](#memory-and-self-iteration)
- [Channels, Gateway, and API](#channels-gateway-and-api)
- [Tools, Skills, and MCP](#tools-skills-and-mcp)
- [External Hook Bridge](#external-hook-bridge)
- [Compatibility with nanobot](#compatibility-with-nanobot)
- [Upstream Parity](#upstream-parity)
- [Repository Layout](#repository-layout)
- [Development](#development)

## Project DNA

| Source | Borrowed idea | Current hahobot implementation |
| --- | --- | --- |
| `HKUDS/nanobot` | Small, readable agent runtime; channel/provider/tool layering; gateway and CLI spirit | `hahobot agent`, `hahobot gateway`, `hahobot serve`, provider registry, channel manager, OpenAI-compatible API, WhatsApp bridge, `nanobot` compatibility entrypoints |
| `shenmintao/NanoMate` | SillyTavern character workflow and companion orientation | Persona import commands, `STYLE.md` / `LORE.md`, persona-local `VOICE.json`, reference-image manifests, built-in `living-together` and `emotional-companion` skills |
| Hermes Agent docs | Context-file driven memory, persistent long-term memory, reflective maintenance over time | Split memory files (`SOUL.md`, `USER.md`, `PROFILE.md`, `INSIGHTS.md`, `memory/MEMORY.md`), history archive, Dream phase 1/2 reflection, optional Mem0 backend and shadow-write mode |

## What Hahobot Ships

Hahobot is a local-first agent runtime centered on one workspace directory and optional named
personas.

Core capabilities:

- Direct CLI chat with a single local workspace as the source of truth.
- Built-in gateway for messaging channels, status pages, and an admin UI.
- Persona workspaces with SillyTavern import, voice overrides, reference images, and companion
  focused skills.
- Long-term memory split across stable user facts, learned collaboration patterns, relationship
  framing, and project memory.
- Dream-style reflective maintenance that can update memory files over time instead of only
  appending chat history.
- Structured archived history for lossless recall via `history_search` / `history_expand`.
- Optional Mem0 integration for external long-term memory, with `file` fallback kept available.
- Built-in tools for web, files, shell, image generation, history recall, cron, messaging, and
  MCP.
- OpenAI-compatible HTTP API for embedding the runtime behind other local systems.
- A compatibility layer that still accepts legacy `nanobot` config paths, imports, CLI entrypoints,
  and old admin cookie names.

## Install

This local project is source-first. The repository itself is the distribution.

### Option 1: `uv` sync

```bash
cd /path/to/Hahobot
uv sync --extra api --extra matrix --extra weixin --extra mem0 --extra dev
```

Use fewer extras if you do not need them:

- `api`: `hahobot serve` / gateway HTTP dependencies
- `matrix`: Matrix support
- `weixin`: Weixin QR login helpers
- `mem0`: Mem0 backend
- `dev`: pytest, ruff, and local development tooling

### Option 2: editable install with `pip`

```bash
cd /path/to/Hahobot
pip install -e ".[api,matrix,weixin,mem0]"
```

If you only want the base runtime:

```bash
pip install -e .
```

### Optional runtime requirements

- Node.js >= 18 if you use the local WhatsApp bridge.
- `npm` available in `PATH` for `hahobot channels login whatsapp`.

## Quick Start

### 1. Create config and workspace

```bash
hahobot onboard --wizard
```

If you want the non-interactive bootstrap:

```bash
hahobot onboard
```

By default hahobot uses:

- Config: `~/.hahobot/config.json`
- Workspace: `~/.hahobot/workspace`

### 2. Set a provider and model

Minimal config example:

```json
{
  "providers": {
    "openrouter": {
      "apiKey": "sk-or-v1-xxx"
    }
  },
  "agents": {
    "defaults": {
      "provider": "openrouter",
      "model": "openai/gpt-4.1-mini"
    }
  }
}
```

### 3. Start chatting

Interactive CLI:

```bash
hahobot agent
hahobot agent --continue
hahobot agent --pick-session
hahobot agent --multiline
```

One-shot prompt:

```bash
hahobot agent -m "Summarize this repository."
```

### 4. Start other surfaces

Gateway:

```bash
hahobot gateway
```

OpenAI-compatible API:

```bash
hahobot serve
```

Useful checks:

```bash
hahobot doctor
hahobot model
hahobot tools
hahobot sessions list
hahobot status
hahobot channels status
hahobot companion init --persona Aria
hahobot companion doctor --persona Aria
```

## Workspace Model

The workspace is the runtime brain. Hahobot reads and writes files there directly instead of
hiding behavior in opaque internal state.

Typical layout:

```text
~/.hahobot/
  config.json
  workspace/
    AGENTS.md
    TOOLS.md
    HEARTBEAT.md
    SOUL.md
    USER.md
    memory/
      MEMORY.md
      history.jsonl
      archive/
    personas/
      Aria/
        SOUL.md
        USER.md
        PROFILE.md
        INSIGHTS.md
        STYLE.md
        LORE.md
        VOICE.json
        .hahobot/
          st_manifest.json
    skills/
    out/
```

Important files:

| File | Role |
| --- | --- |
| `SOUL.md` | Core identity, tone, values, and long-lived persona behavior |
| `USER.md` | Relationship framing, boundaries, and interaction stance |
| `PROFILE.md` | Stable user facts and preferences |
| `INSIGHTS.md` | Proven collaboration heuristics, recurring pitfalls, and working patterns |
| `memory/MEMORY.md` | Project and long-term task context |
| `memory/history.jsonl` | Consolidated conversation history |
| `memory/archive/` | Structured archive chunks for search/expand recall |
| `skills/` | Workspace-local skills; these override built-ins with the same slug |
| `out/` | Delivery artifacts such as generated images and files prepared for outbound channels |

`PROFILE.md` and `INSIGHTS.md` are optional overlays. Hahobot will create or update them only when
the workflow requires them.

## Personas and Companion Workflow

The default persona is the workspace root. Named personas live under `personas/<name>/` and can
override prompts, voice behavior, and companion metadata without affecting the default persona.

### SillyTavern asset import

Import a character card:

```bash
hahobot persona import-st-card ./aria.character.json
```

Import a preset into an existing persona as `STYLE.md`:

```bash
hahobot persona import-st-preset ./preset.json --persona Aria
```

Import world info into `LORE.md`:

```bash
hahobot persona import-st-worldinfo ./world.json --persona Aria
```

Inside chat you can switch personas with the slash-command layer, for example:

```text
/persona set Aria
```

Companion-oriented aliases are also available:

```text
/stchar list
/stchar show Aria
/stchar load Aria
/preset
/preset show Aria
/scene list
/scene daily
/scene comfort
/scene date
/scene rainy_walk
/scene generate rainy bookstore evening together
```

`/scene` is a direct shortcut over the built-in `image_gen` tool. It returns an actual generated
image when `tools.imageGen.enabled` is configured, and it automatically prefers the active
persona's reference images when available. If `.hahobot/st_manifest.json` defines custom
`scene_prompts`, `scene_captions`, or scene-specific `reference_images`, those scene names also
become valid `/scene <name>` shortcuts.

The admin persona page can preview a `/scene` result with the current runtime image settings and
save that preview back into `.hahobot/st_manifest.json` as a named scene template.

If you want a fresh persona scaffold for companion use, bootstrap it first:

```bash
hahobot companion init --persona Aria
hahobot companion init --persona Aria --reference-image ./aria.png
```

Before wiring up voice, heartbeat, and image generation for a persona, you can run a read-only
diagnostic pass:

```bash
hahobot doctor
hahobot model
hahobot tools
hahobot companion doctor --persona Aria
hahobot companion doctor --persona Aria --json
```

- `hahobot doctor`: read-only runtime readiness check for config, workspace, model route, channels,
  and tools
- `hahobot model`: show the active model route, provider resolution, and provider-pool targets
- `hahobot tools`: show tool-family readiness for web, exec, image generation, and MCP
- `hahobot sessions list`: inspect recent saved sessions before resuming them with
  `hahobot agent --continue` or `hahobot agent --session <key>`
- `hahobot sessions show <key>`: inspect one saved session's metadata and recent messages
- `hahobot agent --pick-session`: interactively choose a recent CLI session before sending the
  next message
- `hahobot agent --multiline`: interactive multiline input mode; `Enter` inserts a newline and
  `Ctrl+J` submits the message
- `hahobot agent`: interactive slash completion for built-in commands such as `/status`,
  `/skill ...`, `/persona ...`, `/dream ...`, workspace persona / scene names, and the local
  `/session ...` controls

Inside `hahobot agent`, the local interactive shell also supports session control commands that do
not go through the model:

- `/session current`
- `/session list`
- `/session show [key]`
- `/session use <key>`
- `/session new [name]`

### Persona-local extras

- `VOICE.json`: persona-specific TTS overrides
- `.hahobot/st_manifest.json`: imported SillyTavern metadata plus response filters and reference
  images, plus optional `scene_prompts` / `scene_captions` overrides for `/scene`
- `STYLE.md`: style/preset guidance
- `LORE.md`: world knowledge or setting notes

This is the main place where hahobot follows the NanoMate direction: companion-facing personas,
SillyTavern interoperability, character-consistent image generation, and voice behavior that tracks
the active persona rather than a single global bot identity.

## Memory and Self-Iteration

Hahobot does not treat memory as a single dump file.

It separates:

- identity (`SOUL.md`)
- relationship framing (`USER.md`)
- user facts (`PROFILE.md`)
- collaboration insights (`INSIGHTS.md`)
- project memory (`memory/MEMORY.md`)
- archived interaction history (`history.jsonl` + `memory/archive/`)

### File backend by default

The default user-memory backend is file-based. That keeps state inspectable and reviewable in the
workspace.

### Optional Mem0 backend

You can switch `memory.user.backend` to `mem0` and optionally keep
`memory.user.shadowWriteMem0: true` to dual-write completed turns while preserving the file-backed
context fallback.

### Consolidation and archive recall

When context grows too large, older turns are consolidated into durable memory and archived into
structured chunks. Those archives can later be searched and expanded through tools such as:

- `history_search`
- `history_expand`

This gives hahobot a lossless recall path without keeping every old turn in the active prompt.

### Dream-style reflective maintenance

The built-in Dream workflow reviews history and current memory files in two phases:

1. analyze what should be added, corrected, merged, or removed
2. apply targeted edits to `SOUL.md`, `USER.md`, `PROFILE.md`, `INSIGHTS.md`, and `memory/MEMORY.md`

That design is closer to Hermes Agent's "persistent files as long-term state" philosophy than to a
simple rolling transcript. In practice this means hahobot can gradually refine stable facts,
validated working preferences, and stale memory cleanup instead of endlessly appending notes.

For `PROFILE.md` and `INSIGHTS.md`, hahobot supports structured metadata comments such as:

```md
- Prefers short review loops. <!-- hahobot-meta: confidence=high last_verified=2026-04-08 -->
```

## Channels, Gateway, and API

### Built-in channel surfaces

Hahobot includes built-in support for:

- Telegram
- WhatsApp
- Discord
- WebSocket
- Feishu
- DingTalk
- Slack
- QQ
- Matrix
- Weixin
- WeCom
- Email
- Mochat

Several channels support multi-instance configuration through `channels.<name>.instances`.

Recent upstream nanobot syncs already included here:

- Telegram progressive reply editing now uses `channels.telegram.streamEditInterval` to throttle
  `edit_message_text` frequency instead of a hardcoded interval.
- Discord supports progressive streamed replies when `channels.discord.streaming` is enabled; the
  same config block also exposes `readReceiptEmoji`, `workingEmoji`, and
  `workingEmojiDelay`. Discord can also connect through `channels.discord.proxy`, with optional
  `proxyUsername` / `proxyPassword`.
- `agents.defaults.unifiedSession` can collapse cross-channel conversations into one shared
  session key when you want one conversation state across Telegram, Discord, CLI, and other
  surfaces.
- `tools.exec.allowedEnvKeys` lets you pass specific parent environment variables such as
  `JAVA_HOME` or `GOPATH` into shell tool subprocesses without exposing the whole parent env.
- A built-in `websocket` channel can expose hahobot as a local WebSocket server; see
  [`docs/WEBSOCKET.md`](docs/WEBSOCKET.md) for the handshake and frame contract.
- Direct OpenAI requests for GPT-5 / o1 / o3 / o4 models, or requests with
  `reasoningEffort`, auto-try the Responses API first and fall back to Chat Completions when a
  compatibility error indicates the route is unsupported.

### Gateway

`hahobot gateway` starts the messaging runtime, cron service, HTTP routes, and status tracking.

Notable gateway features:

- `/status` endpoint for machine-readable or browser-readable runtime state
- optional status push integration for Star-Office-UI style dashboards
- optional built-in admin UI at `/admin`
- built-in slash-command reference in the admin page
- persona editor in the admin page, including companion scene fields for `/scene` reference images,
  prompt overrides, and caption overrides
- one-click `/scene` preview generation in the persona editor, using the current runtime imageGen
  configuration
- save the current `/scene` preview back into persona `scene_prompts` / `scene_captions` as a named
  template from the same editor
- Weixin QR login helper in the admin page
- workspace-scoped cron periodically reloads its store, so jobs added by another process can still
  be picked up even when the current scheduler was idle or waiting on a far-future task
- that periodic wake interval is configurable through `gateway.cron.maxSleepMs`

Admin and status routes are disabled by default and should be explicitly configured.

### OpenAI-compatible API

`hahobot serve` exposes:

- `POST /v1/chat/completions`

This API is intentionally narrow:

- local bind by default
- fixed session key `api:default`
- exactly one `user` message per request
- `stream=true` is not supported unless the API contract is deliberately expanded later

## Tools, Skills, and MCP

### Built-in tools

The runtime can expose:

- web search and fetch
- file reads/writes and directory listing
- grep / glob style search
- shell execution
- image generation
- cron scheduling
- outbound messaging
- history search and expansion
- subagent spawning

Workspace restrictions for shell/file tools can be enforced through config.
The shell tool can also forward a narrow allowlist of environment variables through
`tools.exec.allowedEnvKeys`.

### Skills

Built-in skills currently include:

- `living-together`
- `emotional-companion`
- `translate`
- `memory`
- `memorix`
- `summarize`
- `cron`
- `weather`
- `github`
- `tmux`
- `clawhub`
- `skill-creator`

Workspace-local skills live under `workspace/skills/` and override built-ins with the same name.

### MCP

Hahobot supports MCP servers through `tools.mcpServers`. When Memorix MCP tools are connected,
hahobot can auto-load the built-in `memorix` skill and initialize the session against the active
workspace.

## External Hook Bridge

If you already have shell or Python automation and do not want to implement a Python `AgentHook`,
you can bridge selected hook events to an external command:

```python
import asyncio

from hahobot import ExternalHookBridge, Hahobot


async def main() -> None:
    bot = Hahobot.from_config()
    hook = ExternalHookBridge(
        ["python", "scripts/audit_hook.py"],
        events=["before_iteration", "before_execute_tools", "after_iteration"],
    )
    result = await bot.run("Summarize the repo", hooks=[hook])
    print(result.content)


asyncio.run(main())
```

The command receives one JSON object on stdin with `schema_version`, `event`, and `context`
fields. By default the bridge stays non-streaming; add `on_stream` or `on_stream_end` explicitly
if you really want per-delta events.

For explicit policy blocks, return JSON like `{"continue": false, "message": "..."}` or exit with
code `2` during `before_iteration` or `before_execute_tools`. Other non-zero exits are fail-open by
default and only logged unless you set `fail_open=False`.

## Compatibility with nanobot

This repo keeps a deliberate compatibility layer for the rename transition.

What still works:

- `nanobot` CLI entrypoint
- `python -m nanobot`
- `from nanobot import Nanobot`
- legacy config fallback from `~/.nanobot/config.json`
- legacy default workspace preservation for `~/.nanobot/workspace`
- legacy admin cookie names
- legacy metadata keys accepted during import / skill parsing

Config-path behavior is intentionally conservative:

- if no config path is specified, hahobot checks `~/.hahobot/config.json` first
- only if that file is missing does it fall back to `~/.nanobot/config.json`
- when the legacy path is used, the config is copied into the hahobot location instead of moved

Legacy upstream runtime behaviors also kept in sync here include:

- Telegram streaming reply edits can now be tuned through config rather than code constants
- Discord streaming replies follow the same edit-then-finalize model as upstream nanobot
- cross-channel unified-session routing is available through `agents.defaults.unifiedSession`
- the shell tool supports `tools.exec.allowedEnvKeys` for explicit env passthrough
- the built-in WebSocket server channel is available through `channels.websocket`
- direct OpenAI reasoning requests keep the upstream Responses-API-first fallback strategy

This lets existing `nanobot` automation keep running while new installs converge on `hahobot`.

## Upstream Parity

`[UPSTREAM_PARITY.md](./UPSTREAM_PARITY.md)` is the living ledger for upstream sync status.

Use it when manually porting changes from `HKUDS/nanobot`: it records what is already matched
locally, what is intentionally different, and which areas should be re-audited on the next sync.

## Repository Layout

High-level layout:

- `hahobot/`: Python package
- `hahobot/agent/`: loop, memory, personas, tools, hooks, skills
- `hahobot/channels/`: built-in messaging channels
- `hahobot/providers/`: model providers and registry
- `hahobot/config/`: config schema, path helpers, loader
- `hahobot/gateway/`: HTTP server, admin UI, status routes
- `hahobot/templates/`: seeded workspace templates such as `SOUL.md`, `USER.md`, `AGENTS.md`,
  `TOOLS.md`, and Dream prompts
- `hahobot/skills/`: bundled skills
- `bridge/`: TypeScript WhatsApp bridge
- `tests/`: pytest suite
- `nanobot/`: compatibility package for legacy imports and CLI usage

## Development

Set up the development environment:

```bash
uv sync --extra api --extra matrix --extra weixin --extra mem0 --extra dev
```

Run tests:

```bash
./.venv/bin/python -m pytest
```

Run a focused test file:

```bash
./.venv/bin/python -m pytest tests/test_gateway_http.py -q
```

Lint:

```bash
uv run ruff check .
```

Build the WhatsApp bridge:

```bash
cd bridge
npm install
npm run build
```

For a Chinese summary focused on local deployment and companion workflows, see
[`README_ZH.md`](./README_ZH.md).
