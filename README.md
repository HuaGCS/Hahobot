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
| Hermes Agent docs | Context-file driven memory, persistent long-term memory, reflective maintenance over time | Split memory files (`SOUL.md`, `USER.md`, `PROFILE.md`, `INSIGHTS.md`, `memory/MEMORY.md`), history archive, Dream phase 1/2 reflection, embedded SQLite-FTS top-K retrieval backend with file fallback |

## What Hahobot Ships

Hahobot is a local-first agent runtime centered on one workspace directory and optional named
personas.

Core capabilities:

- Direct CLI chat with a single local workspace as the source of truth.
- Built-in gateway for messaging channels, status pages, and an admin UI.
- Hermes-style gateway surfaces: the built-in admin/status pages now use a darker dashboard shell
  and include read-only sessions, skills, and cron views for the active runtime workspace.
- Persona workspaces with SillyTavern import, voice overrides, reference images, and companion
  focused skills.
- Long-term memory split across stable user facts, learned collaboration patterns, relationship
  framing, and project memory.
- Dream-style reflective maintenance that can update memory files over time instead of only
  appending chat history.
- Structured archived history for lossless recall via `history_search` / `history_expand`.
- Top-K BM25 retrieval over the persona's `MEMORY.md` via an embedded SQLite-FTS5
  derived index, with whole-file injection kept as a conservative fallback.
- Built-in tools for web, files, shell, image generation, notebook editing, history recall, cron,
  messaging, runtime self-inspection, and MCP.
- OpenAI-compatible HTTP API for embedding the runtime behind other local systems.
- A compatibility layer that still accepts legacy `nanobot` config paths, imports, CLI entrypoints,
  and old admin cookie names.

## Install

This local project is source-first. The repository itself is the distribution.

### Option 1: `uv` sync

```bash
cd /path/to/Hahobot
uv sync --extra api --extra matrix --extra weixin --extra dev
```

Use fewer extras if you do not need them:

- `api`: `hahobot serve` / gateway HTTP dependencies
- `matrix`: Matrix support
- `weixin`: Weixin QR login helpers
- `dev`: pytest, ruff, and local development tooling

### Option 2: editable install with `pip`

```bash
cd /path/to/Hahobot
pip install -e ".[api,matrix,weixin]"
```

If you only want the base runtime:

```bash
pip install -e .
```

### Optional runtime requirements

- Node.js >= 18 if you use the local WhatsApp bridge.
- `npm` available in `PATH` for `hahobot channels login whatsapp`.

Windows installs automatically include the IANA timezone database through the conditional `tzdata`
dependency, so cron, heartbeat, and configured `ZoneInfo` timezones do not depend on OS-bundled data.

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

Provider requests keep a finite wall-clock timeout through `HAHOBOT_LLM_TIMEOUT_S` (`0` disables
it). Streaming requests use the wider `max(300, 2 * timeout)` budget in addition to provider idle
timeouts, allowing healthy long reasoning to finish while still bounding trickle streams.

### 3. Start chatting

Interactive CLI:

```bash
hahobot agent
hahobot agent --continue
hahobot agent --pick-session
hahobot agent --multiline
```

Streamed CLI replies finish with a compact throughput footer such as
`⚡ ≈24.6 tok/s · ≈120 tok · 4.9s`. It estimates visible output tokens with the shared tokenizer and
measures active streaming time across tool-call segments; provider usage remains the billing source.
When an interactive `hahobot agent` process exits, it also prints an invocation-only summary with
completed main-agent turns, model-call count, provider-reported input/output/total and cached tokens,
weighted average streaming throughput, and wall-clock duration. Usage observation covers completed
retry-layer requests made through the shared runtime provider, including subagents, memory work,
`/compact`, and `/review`. Resumed history is not added; cancelled requests without returned usage
cannot be counted.

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

The API binds to `127.0.0.1` by default and is unauthenticated for local use. Set
`api.authKey` to require `Authorization: Bearer <authKey>` on every request (the
`/health` probe stays open). Binding to a wildcard address (`api.host` = `0.0.0.0`
or `::`) **requires** `api.authKey`; `hahobot serve` refuses to start otherwise so
a network-reachable API is never left unauthenticated.

Per-skill config (OpenClaw `skills.entries` shape, stored in `<workspace>/skills.json`):

```bash
hahobot config set skills.entries.today-task.config.authCode OoPs0gbbd5BH
hahobot config get skills.entries.today-task.config
hahobot config unset skills.entries.today-task.config.authCode
```

Only `skills.*` paths are accepted. Values are stored as strings unless `--json` is
passed (so numeric-looking codes keep their type). A skill's secrets stay in this
workspace file — they are never injected into the shell environment or into other
skills' context; a skill reads its own `entries.<name>.config` at runtime via
`read_file`. Do not commit `skills.json` if it holds secrets and the workspace is a git repo.

`openclaw` is a CLI alias for `hahobot` (identical subcommands), so the OpenClaw-style
`openclaw config set skills.entries.today-task.config.authCode <value>` works verbatim.

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
- `hahobot sessions show <key>`: inspect one saved session's metadata, working checkpoint, and
  recent messages
- `hahobot sessions export <key> --format md|json`: write one saved session to a local export
  artifact under `workspace/out/sessions/` by default
- `hahobot sessions compact <key>`: manually run the existing session token compaction flow for one
  saved session and persist the updated consolidation cursor
- `hahobot memory index rebuild`: rebuild the optional SQLite FTS archive index from JSON sidecars
- `hahobot repo status`: inspect the active workspace's local Git state and change counts
- `hahobot repo diff --staged --name-only`: inspect tracked diff summaries without mutating the
  repository
- `hahobot review --staged` / `hahobot review --base main`: run a local diff review with the
  configured model, without modifying files
- `hahobot agent --pick-session`: interactively choose a recent CLI session before sending the
  next message
- `hahobot agent --multiline`: interactive multiline input mode; `Enter` inserts a newline and
  `Ctrl+J` submits the message
- `hahobot agent`: interactive slash completion for built-in commands such as `/status`,
  `/update`, `/skill ...`, `/persona ...`, `/dream ...`, workspace persona / scene names, and the local
  `/session ...` / `/repo ...` / `/review ...` / `/compact` controls
- Interactive CLI input and history writes sanitize malformed Unicode surrogate code points before
  dispatch, while preserving valid emoji and other Unicode text.

Inside `hahobot agent`, the local interactive shell also supports commands that do not go through
the model:

- `/session current`
- `/session list`
- `/session show [key]`
- `/session export [key]`
- `/session use <key>`
- `/session new [name]`
- `/repo status`
- `/repo diff`
- `/repo diff staged`
- `/review`
- `/review staged`
- `/compact`
- `/compact [key]`

The same `/session ...`, `/repo ...`, `/review ...`, and `/compact ...` commands are also available
through gateway-backed chats. In gateway mode, `/session use <key>` and `/session new [name]`
reroute only the current chat; use `/session use default` to return to the origin session key.

`/repo diff` only reports tracked Git changes. Use `/repo status` when you also need untracked
file counts.

`/compact` reuses the same automatic token-consolidation logic that hahobot already uses under
pressure; it does not invent a second memory pipeline.

`/update` supports three modes:

- `/update`: fast-forward the current Git checkout, run `uv sync --locked --all-extras`, refresh
  the local WhatsApp bridge when `channels.whatsapp` is enabled, and restart on success
- `/update check`: dry-run the same preflight checks and show whether the update is currently ready
- `/update force`: skip the clean-working-tree precheck and keep going with the normal update flow
- `/update bridge`: rebuild only the local WhatsApp bridge from the current repo and restart

The default `/update` flow still refuses dirty working trees and branches without upstream tracking.
After `/restart` or a successful `/update`, the new process waits for the originating channel to
reconnect before sending the completion notice, and retries transient delivery failures during the
startup window instead of silently dropping the confirmation.
The built-in WebSocket server currently uses process-local connection ids, so a client that reconnects
after process replacement cannot yet inherit the old completion-notice target; that retry expires at
the startup deadline instead of being reported as a successful delivery.

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

### Default backend: SQLite-FTS with file fallback

`memory.user.backend` defaults to `sqlite`. Each persona's `memory/MEMORY.md` stays
the source of truth; a derived `memory/facts.sqlite` (FTS5) is rebuilt automatically
when the markdown file's mtime changes. Per turn, the inbound user text is used as a
BM25 query and the top-K matching fragments are injected as the long-term memory
block. If the query is empty the most recent fragments are returned instead.

Tunable under `memory.user.sqlite`:

- `topK` — fragments per turn (default 8)
- `maxContextChars` — hard upper bound on the assembled block (default 4000)
- `maxFragmentChars` — per-fragment truncation (default 500)

The `file` backend remains a one-line switch (`memory.user.backend: "file"`) and is
also used automatically as a fallback when the SQLite backend returns empty or raises.

### Structured fragment headers

Consolidator-written fragments carry a server-controlled provenance header so the
fact source cannot be forged by prompt injection:

```markdown
<!-- ts:2026-05-26T17:30 tag:preference src:turn -->
User prefers concise replies.
```

The LLM only proposes the optional `tag:` token (one of `preference`, `project`,
`reference`, `feedback`, `user`, `experience`); `ts` and `src` are filled by code.
`experience` is reserved for distilled task patterns (e.g. a successful tool
sequence for a class of problem), not one-off facts. Fragments without a header
are still readable and become `tag=legacy`/`src=unknown`.

### Consolidation and archive recall

When context grows too large, older turns are consolidated into durable memory and archived into
structured chunks. Those archives can later be searched and expanded through tools such as:

- `history_search`
- `history_timeline`
- `history_expand`

Set `memory.archive.indexBackend: "sqlite"` to enable an optional persona-local SQLite FTS index
for faster archive lookup. The JSONL index and chunk files remain the source of truth;
`hahobot memory index rebuild` can recreate `memory/archive/index.sqlite` at any time.

This gives hahobot a lossless recall path without keeping every old turn in the active prompt.
Subagent completion follow-ups are also persisted into session history before the next model call,
so background-task results survive retries or crashes instead of existing only as transient prompt
injections.

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

The chat `/status` command and browser `/status` page now surface the active or recent persona's
memory-layer summary using the same terminology: `PROFILE.md` for stable user facts/preferences,
`INSIGHTS.md` for proven collaboration guidance, and structured `hahobot-meta` comments for
confidence / verification tracking.

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
- Long Telegram streams are split while generation is still in progress: rendered HTML chunks stay
  within Telegram's 4096-character limit, malformed-HTML rejections fall back to plain text, and the
  remaining live buffer keeps raw Markdown so formatting survives later deltas. A transient send
  failure resumes from the first unsent overflow chunk instead of repeating chunks already accepted.
- Telegram can render `OutboundMessage.buttons` as native inline keyboards when
  `channels.telegram.inlineKeyboards` is enabled; otherwise button labels are preserved as inline
  text fallback.
- Feishu topic replies keep every split outbound part in the same thread instead of only replying
  with the first segment.
- `read_file` can extract text from Office Open XML documents (`.docx`, `.xlsx`, `.pptx`) without
  adding a second document service.
- Channel audio transcription can pass an optional `channels.transcriptionLanguage` ISO-639 hint to
  the configured Groq/OpenAI transcription backend, and transient Whisper upload failures now retry
  before the channel falls back to an empty transcription.
- Discord supports progressive streamed replies when `channels.discord.streaming` is enabled; the
  same config block also exposes `readReceiptEmoji`, `workingEmoji`, and
  `workingEmojiDelay`. Discord can also connect through `channels.discord.proxy`, with optional
  `proxyUsername` / `proxyPassword`.
- `agents.defaults.unifiedSession` can collapse cross-channel conversations into one shared
  session key when you want one conversation state across Telegram, Discord, CLI, and other
  surfaces.
- `tools.exec.allowedEnvKeys` lets you pass specific parent environment variables such as
  `JAVA_HOME` or `GOPATH` into shell tool subprocesses without exposing the whole parent env.
- `agents.defaults.toolHintMaxLength` controls how much of each tool-call hint is shown when
  `channels.sendToolHints` is enabled; it hot-reloads with the rest of the safe agent defaults.
- A built-in `websocket` channel can expose hahobot as a local WebSocket server; see
  [`docs/WEBSOCKET.md`](docs/WEBSOCKET.md) for the handshake and frame contract.
- Direct OpenAI requests for GPT-5 / o1 / o3 / o4 models, or requests with
  `reasoningEffort`, auto-try the Responses API first and fall back to Chat Completions when a
  compatibility error indicates the route is unsupported.

### Gateway

`hahobot gateway` starts the messaging runtime, cron service, HTTP routes, and status tracking.

Notable gateway features:

- `/status` endpoint for machine-readable or browser-readable runtime state; the browser view also
  shows the latest task's current step, next step, response preview, and the recent persona's
  `PROFILE.md` / `INSIGHTS.md` memory-layer summary
- optional status push integration for Star-Office-UI style dashboards
- optional built-in admin UI at `/admin`
- optional built-in **chat WebUI** at `/app` (enable with `gateway.webui.enabled`): a nanobot-style,
  server-rendered chat surface in the same aiohttp/Jinja runtime — there is no separate SPA. It shares
  the admin login session (enable `gateway.admin` with an `authKey`) and folds the admin pages in as
  its "Settings" area (`/app/settings`: runtime + last-turn token usage, the active persona's
  memory-layer summary, and links to every admin section). Chat is scoped to `webui:*` sessions, so it
  never writes into a live channel conversation. It includes:
    - streaming replies and a conversation sidebar over a WebSocket (`/app/ws`)
    - inline media (images served from `workspace/out` via `/app/media/...`)
    - an in-chat persona selector, a live working-checkpoint panel, and conversation forking
    - voice input (mic → `/app/transcribe`, using the configured transcription provider)
    - proactive/scheduled delivery: cron, heartbeat, and the `message` tool push into an open
      conversation (and persist so they show on reload) — ask the agent to "remind me in 10 minutes"
      or use the composer's reminder form
    - a responsive mobile layout
- Hermes-inspired dashboard styling for `/admin` and browser `/status`, without introducing a
  second SPA runtime
- read-only sessions, skills, and cron pages in the admin UI for the active runtime workspace
- visual config coverage for `tools.exec.*`, channel runtime controls such as
  `channels.transcriptionProvider` / `channels.transcriptionLanguage`,
  `agents.defaults.toolHintMaxLength`, and the common
  Telegram/Discord single-instance extras (`channels.telegram.streamEditInterval`,
  `channels.telegram.inlineKeyboards`, Discord streaming/emoji/proxy fields)
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
- accepts either `application/json` or `multipart/form-data`
- content arrays may include inline base64/data-URL file blocks, and multipart uploads are also accepted
- text-like attachments are extracted into the prompt; binary/image attachments fall back to stable placeholders on the direct API path
- `stream=true` is not supported unless the API contract is deliberately expanded later

For `multipart/form-data`, send the usual `messages` payload as a JSON string field and attach one
or more uploaded files alongside it.

### Standard A2A (Agent2Agent) adapter

When `a2a.enabled` is set, `hahobot serve` additionally speaks the standard
[A2A](https://a2a-protocol.org) protocol (v0.3.0) so other A2A-compliant agents can call this bot:

- `GET /.well-known/agent-card.json` (plus the legacy `/.well-known/agent.json` alias) — the Agent Card
- `POST /a2a` — JSON-RPC 2.0 endpoint supporting `message/send`, `message/stream`, `tasks/get`, and `tasks/cancel`

Each A2A `contextId` maps to a hahobot session (`a2a:{contextId}`). `message/send` runs the turn
synchronously and returns a completed `Task` whose artifact holds the reply. `message/stream`
returns a `text/event-stream` (SSE): an initial `Task` (state `working`), one
`TaskArtifactUpdateEvent` per streamed delta, and a final `TaskStatusUpdateEvent`
(`state=completed`, `final=true`); the completed task is also retained for `tasks/get`. Configure
it under the top-level `a2a` block:

```toml
[a2a]
enabled = true
name = "hahobot"
description = "Persona-first local AI agent."
version = "0.1.0"
# public_url advertised in the Agent Card; defaults to http://{api.host}:{api.port}
public_url = ""
timeout = 120.0
max_tasks = 2048
streaming = true   # advertise + serve message/stream over SSE
```

This is distinct from `channels.xiaoyi`, which speaks Huawei Xiaoyi's separate A2A WebSocket dialect.

## Tools, Skills, and MCP

### Built-in tools

The runtime can expose:

- web search and fetch
- file reads/writes and directory listing
- grep / glob style search
- shell execution
- image generation
- Jupyter notebook cell editing through `notebook_edit`
- cron scheduling
- outbound messaging
- history search and expansion
- read-only runtime/session/subagent inspection through `self_inspect`
- subagent spawning with `explore` / `implement` / `verify` execution modes

Workspace restrictions for shell/file tools can be enforced through config.
The shell tool can also forward a narrow allowlist of environment variables through
`tools.exec.allowedEnvKeys`.
`web_search` supports `brave`, `searxng`, and `duckduckgo`; DuckDuckGo needs no extra
credentials and is executed exclusively so concurrent tool turns do not batch multiple
DuckDuckGo searches together.
`self_inspect` is intentionally read-only and returns a JSON snapshot of the active runtime,
registered tools, actual session key, and currently running subagents. `notebook_edit` stays
limited to `.ipynb` files; `spawn(mode="implement")` workers also receive it, while `explore` and
`verify` workers do not.

### Skills

Built-in skills currently include:

- `living-together`
- `emotional-companion`
- `translate`
- `llm-wiki`
- `workflow-core`
- `plan`
- `verify`
- `skill-derive`
- `memory`
- `memorix`
- `summarize`
- `cron`
- `weather`
- `github`
- `tmux`
- `clawhub`
- `skill-creator`

`workflow-core` is now an always-on workflow guide. `plan`, `verify`, and `skill-derive` stay
available as opt-in built-ins for planning, validation, and turning repeatable workflows into
workspace skills. Subagents can also be spawned in explicit `explore`, `implement`, or `verify`
mode so their available tools match the job.

Plans that create or reorder future tasks/TODOs are treated as drafts until an independent
verification pass reviews them. The built-in workflow skills prefer `spawn(..., mode="verify")`
for that review and explicitly avoid using self-review as permission to execute follow-up work.

`/skill derive <name> [brief] [--force]` can turn the current session's recent workflow and
`working_checkpoint` into a local draft skill under `workspace/skills/<name>/SKILL.md`. Existing
drafts are left untouched unless you pass `--force`. Derived drafts now seed a `metadata` block
for hahobot-local lifecycle hints such as `triggers`, `tool_tags`, `supersedes`, `last_used`, and
`success_count`.

`/skill lint` is the read-only hygiene command for local skill growth. It reports visible
overlap, missing `supersedes` targets, and which older skills are currently hidden from the runtime
summary because a newer skill supersedes them.

`/skill supersede <newer> <older> [more...]` is the explicit metadata-management command for that
same lifecycle. `remove` and `clear` variants now cover the subtractive side as well:
`/skill supersede remove <newer> <older> [more...]` and `/skill supersede clear <newer>`. These
flows update `supersedes` without deleting or merging the older skills, so prompt selection can
shrink while the old drafts remain reviewable.

When the agent actually reads a workspace `skills/<name>/SKILL.md` during a turn, hahobot now
best-effort writes back `last_used`, and increments `success_count` only when that turn finishes
without an error, empty-final-response, or max-iteration stop.

Workspace-local skills live under `workspace/skills/` and override built-ins with the same name.
If you want to hide specific bundled or workspace skills from the main agent and subagents, set
`agents.defaults.disabledSkills` to a list of skill directory names such as `["github", "weather"]`.
The runtime skill summary is also query-aware and top-k scoped instead of dumping every skill into
every prompt; when a newer skill declares `supersedes`, the older target is hidden from the shared
summary as long as the newer skill is available.

### MCP

Hahobot supports MCP servers through `tools.mcpServers`. When Memorix MCP tools are connected,
hahobot can auto-load the built-in `memorix` skill and initialize the session against the active
workspace.
You can also use `enabledTools` on one MCP server to register only a subset of raw MCP tool names
or wrapped hahobot names such as `mcp_filesystem_write_file`. Omit it, or use `["*"]`, to keep all
tools; use `[]` to register none from that server.

Servers are connected concurrently, each bounded by `tools.mcpServers.<name>.connectTimeout`
(seconds, default `20`). If a server's transport spawn or `initialize` handshake exceeds it, that
server is logged and skipped instead of blocking startup or stalling shutdown — the rest still
connect. Raise it for heavy stdio servers that are slow to boot (e.g. a Node-based
`chrome-devtools` server), or lower it to fail fast. `toolTimeout` (default `30`) separately bounds
each tool call.

Each connection generation is kept inside a dedicated owner task. That task opens and closes the
MCP transport/session stack itself, which avoids AnyIO cancel-scope corruption when shutdown or
reconnect happens from another task. Reconnect swaps in a fresh owner before the old generation is
closed; timeout, cancellation, or partial registration failures are cleaned up without leaking the
stdio subprocess or HTTP session.

For HTTP-based MCP transports (`sse` / `streamableHttp`), the server URL you configure is trusted —
local servers such as `http://127.0.0.1:3211/mcp` keep working as expected. As defense-in-depth,
if a configured (public) server then *redirects* a request to a different internal/private address,
that redirect hop is refused, so a remote server cannot bounce hahobot into your internal network.

### Auto Compact

Set `agents.defaults.idleCompactAfterMinutes` to proactively archive stale live session prefixes in
the background while keeping a recent legal suffix ready for the next reply. When the user returns,
hahobot injects a one-shot resume summary into runtime context before continuing the conversation.
`sessionTtlMinutes` remains accepted as a legacy alias.

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
- idle auto compact is available through `agents.defaults.idleCompactAfterMinutes`
- `agents.defaults.disabledSkills` filters both main-agent and subagent skill summaries
- MCP per-server tool filtering is available through `tools.mcpServers.<name>.enabledTools`
- MCP transports use same-task owner lifecycles for safe shutdown and reconnect
- streaming LLM calls use a wider finite wall-clock timeout than ordinary calls
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
uv sync --extra api --extra matrix --extra weixin --extra dev
```

Run tests:

```bash
./.venv/bin/python -m pytest
```

Run a focused test file:

```bash
./.venv/bin/python -m pytest tests/test_gateway_http.py -q
```

Lint and format check (run both before committing — CI runs the same):

```bash
uv run ruff check .
uv run ruff format --check .
```

`ruff check` only lints; it does not catch formatting. CI fails if
`ruff format --check` reports differences, so run it too. To apply fixes:

```bash
uv run ruff format .
```

Build the WhatsApp bridge:

```bash
cd bridge
npm install
npm run build
```

For a Chinese summary focused on local deployment and companion workflows, see
[`README_ZH.md`](./README_ZH.md).
