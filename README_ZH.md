<div align="center">
  <img src="hahobot_logo.png" alt="hahobot" width="500">
  <h1>hahobot：超轻量级个人 AI 助手</h1>
  <p>
    <img src="https://img.shields.io/badge/status-local_fork-orange" alt="Local Fork">
    <img src="https://img.shields.io/badge/package-unpublished-lightgrey" alt="Package">
    <img src="https://img.shields.io/badge/python-≥3.11-blue" alt="Python">
    <img src="https://img.shields.io/badge/license-MIT-green" alt="License">
  </p>
</div>

`hahobot` 是一个以 workspace 为中心、面向 persona / companion 工作流的本地 AI agent runtime。

当前目录里的 `hahobot` 不是上游 `nanobot` 的简单镜像，而是在
[HKUDS/nanobot](https://github.com/HKUDS/nanobot) 的轻量 runtime 基础上，继续吸收
[shenmintao/NanoMate](https://github.com/shenmintao/NanoMate) 的角色陪伴与 SillyTavern 工作流，
并参考 [Hermes Agent](https://hermes-agent.nousresearch.com/docs/getting-started/quickstart)
那种“上下文文件 + 持久记忆 + 反思式自维护”的思路整理出的本地项目。历史发布记录和上游讨论仍以
`nanobot` 为准；在此基础上，这个仓库又落地了更偏陪伴型与本地化的能力，例如：

- SillyTavern 角色资产导入到 persona 工作区
- persona 级参考图与角色一致性生图
- persona 级 `VOICE.json`
- `channels.voiceReply` 下的 `openai` / `edge` / `sovits`
- `living-together` / `emotional-companion` / `translate` / `llm-wiki` 内置技能
- WhatsApp 本地 bridge 代理支持

完整英文说明见 [README.md](./README.md)。

## 目录

- [项目亮点](#项目亮点)
- [安装](#安装)
- [快速开始](#快速开始)
- [可选能力](#可选能力)
- [聊天渠道](#聊天渠道)
- [Agent 社交网络](#agent-社交网络)
- [配置说明](#配置说明)
- [多实例](#多实例)
- [CLI 参考](#cli-参考)
- [外部 Hook Bridge](#外部-hook-bridge)
- [上游同步台账](#上游同步台账)
- [OpenAI 兼容 API](#openai-兼容-api)
- [周期任务](#周期任务)
- [Docker](#docker)
- [Linux 服务](#linux-服务)
- [项目结构](#项目结构)
- [贡献与路线图](#贡献与路线图)

## 项目亮点

- 超轻量：更少的代码和更低的运行复杂度
- 易扩展：provider、tool、channel、persona、skill 结构清晰
- 多渠道：Telegram、Discord、WhatsApp、QQ、Slack、Feishu、Matrix、Email、Weixin、Wecom、Mochat、WebSocket
- 本地优先：支持本地 workspace、私有部署、工作区技能和本地文件交付
- 当前仓库已增强：SillyTavern 资产导入、persona 参考图、生图、语音回复、自定义声线、陪伴技能，以及 Hermes 风格的 gateway admin/status 页面

## 安装

### 从源码安装

```bash
cd /path/to/Hahobot
pip install -e .
```

### 使用 uv 安装

```bash
uv tool install /path/to/Hahobot
```

### 更新

这个重命名后的本地仓库暂时没有公开的 `hahobot-ai` 包发布；更新方式以同步源码后重新安装为准：

```bash
pip install -e .
hahobot --version
```

### 旧配置自动复制

hahobot 启动时会自动规范化兼容配置和已废弃字段。如果检测到
未显式指定配置文件路径，hahobot 会先检查 `~/.hahobot/config.json`，只有在它不存在时才回退到
`~/.nanobot/config.json`。此时会自动把旧配置复制到新的 hahobot 目录里。如果旧的默认工作区
`~/.nanobot/workspace` 已经存在，复制后的配置会把
`agents.defaults.workspace` 固定到这个路径，避免现有数据因为目录名变更而失联。

为了兼容旧自动化，项目也保留了 `nanobot` 入口：`nanobot` 命令、
`python -m nanobot`，以及 `from nanobot import Nanobot` 这类导入仍然会映射到 hahobot。

如果你在使用 WhatsApp，升级后建议重建本地 bridge：

```bash
rm -rf ~/.hahobot/bridge
hahobot channels login whatsapp
```

## 快速开始

### 1. 初始化

```bash
hahobot onboard
```

如果想使用交互式向导：

```bash
hahobot onboard --wizard
```

### 2. 配置模型

默认配置文件路径：

- `~/.hahobot/config.json`

一个最小配置示例：

```json
{
  "providers": {
    "openrouter": {
      "apiKey": "sk-or-v1-xxx"
    }
  },
  "agents": {
    "defaults": {
      "model": "openrouter/openai/gpt-4o-mini"
    }
  }
}
```

### 3. 对话

```bash
hahobot agent
hahobot agent --continue
hahobot agent --pick-session
hahobot agent --multiline
```

网关模式：

```bash
hahobot gateway
```

## 可选能力

### Provider 池：故障切换 / 轮询

如果你想在多个已配置 provider 之间自动切换，可以使用 `agents.defaults.providerPool`：

```json
{
  "providers": {
    "openrouter": {
      "apiKey": "sk-or-v1-xxx"
    },
    "deepseek": {
      "apiKey": "sk-xxx"
    }
  },
  "agents": {
    "defaults": {
      "providerPool": {
        "strategy": "failover",
        "targets": [
          {
            "provider": "openrouter",
            "model": "openai/gpt-4o-mini"
          },
          {
            "provider": "deepseek",
            "model": "deepseek-chat"
          }
        ]
      }
    }
  }
}
```

- `strategy: "failover"`：按顺序尝试，直到某个 provider 成功返回。
- `strategy: "round_robin"`：每次请求轮换起始 provider；如果当前 provider 出错，仍会继续尝试后续目标。
- 某个 target 没写 `model` 时，会回退到 `agents.defaults.model`。
- 只要 `providerPool.targets` 非空，就会优先于 `agents.defaults.provider` 生效。

如果 provider 日志里出现 `Error calling LLM`，hahobot 现在会尽量保留底层传输错误原因，
例如 DNS 失败、TLS 校验失败、`Connection refused` 等。纯粹的连接错误通常更像
`apiBase` / 代理 / 网关不可达，而不是远端接口单纯“不支持这个模型或路由”。

对于直连 OpenAI 的请求，当前实现也已经同步了上游新逻辑：

- 当模型属于 `gpt-5` / `o1` / `o3` / `o4` 系列，或显式设置了 `reasoningEffort` 时，会优先尝试 Responses API
- 如果目标 OpenAI 兼容网关并不支持这条路由，会自动回退到 Chat Completions，而不是直接把兼容性报错暴露给最终用户

### 共享会话 unifiedSession

如果你希望 Telegram、Discord、CLI 等多个入口共享同一段会话上下文，可以开启
`agents.defaults.unifiedSession`：

```json
{
  "agents": {
    "defaults": {
      "unifiedSession": true
    }
  }
}
```

开启后，在没有显式 `session_key_override` 的情况下，多个渠道会复用同一个默认 session。

### Web 搜索

`web_search` 支持 Brave Search、SearXNG 和 DuckDuckGo。

Brave Search：

```json
{
  "tools": {
    "web": {
      "search": {
        "provider": "brave",
        "apiKey": "your-brave-api-key"
      }
    }
  }
}
```

SearXNG：

```json
{
  "tools": {
    "web": {
      "search": {
        "provider": "searxng",
        "baseUrl": "http://localhost:8080"
      }
    }
  }
}
```

DuckDuckGo：

```json
{
  "tools": {
    "web": {
      "search": {
        "provider": "duckduckgo"
      }
    }
  }
}
```

`duckduckgo` 不需要额外凭证；运行时会把 DuckDuckGo 搜索串行执行，避免在并发工具回合里把多个 DuckDuckGo 查询打成同一批。

### 图像生成

启用内置 `image_gen` 工具：

```json
{
  "tools": {
    "imageGen": {
      "enabled": true,
      "apiKey": "your-image-api-key",
      "baseUrl": "https://api.openai.com/v1",
      "model": "gpt-image-1"
    }
  }
}
```

说明：

- 生成结果统一写入 `<workspace>/out/image_gen`
- 若要把图片发送给用户，模型还需要调用 `message` 工具并把图片路径放进 `media`
- 当当前 persona 的 `.hahobot/st_manifest.json` 里有 `reference_image` 或 `reference_images` 时，`image_gen` 支持：
  - `reference_image="__default__"`
  - `reference_image="__default__:scene"`

这使得角色一致性出图、场景换装、生活陪伴类配图都可以复用 persona 参考图。

### 语音回复

当前仓库没有单独维护一套平行 TTS 系统，而是统一复用：

- `channels.voiceReply`

支持的 provider：

- `openai`
- `edge`
- `sovits`

OpenAI 兼容 TTS 示例：

```json
{
  "channels": {
    "voiceReply": {
      "enabled": true,
      "channels": ["telegram"],
      "provider": "openai",
      "url": "https://api.openai.com/v1",
      "model": "gpt-4o-mini-tts",
      "voice": "alloy",
      "instructions": "keep the delivery calm and clear",
      "speed": 1.0,
      "responseFormat": "opus"
    }
  }
}
```

`provider=edge` 示例：

```json
{
  "channels": {
    "voiceReply": {
      "enabled": true,
      "channels": ["telegram"],
      "provider": "edge",
      "edgeVoice": "zh-CN-XiaoxiaoNeural",
      "edgeRate": "+8%",
      "edgeVolume": "+0%"
    }
  }
}
```

`provider=sovits` 示例：

```json
{
  "channels": {
    "voiceReply": {
      "enabled": true,
      "channels": ["telegram"],
      "provider": "sovits",
      "sovitsApiUrl": "http://127.0.0.1:9880",
      "sovitsReferWavPath": "/data/voices/aria.wav",
      "sovitsPromptText": "这是角色参考语音。",
      "sovitsPromptLanguage": "zh",
      "sovitsTextLanguage": "zh"
    }
  }
}
```

补充说明：

- QQ 语音上传要求 `.silk`，因此在 QQ 场景里要用 `responseFormat: "silk"`
- `provider=edge` 不依赖 OpenAI API Key，但运行时需要本机安装 `edge-tts`
- `provider=sovits` 适合自定义声线 / GPT-SoVITS 克隆
- 语音回复会自动跟随当前 persona 的文本风格

### Persona 级 VOICE.json

默认 persona：

- `<workspace>/VOICE.json`

自定义 persona：

- `<workspace>/personas/<name>/VOICE.json`

示例：

```json
{
  "provider": "sovits",
  "apiBase": "http://127.0.0.1:9880",
  "voice": "nova",
  "instructions": "sound crisp, confident, and slightly faster than normal",
  "speed": 1.15,
  "referWavPath": "assets/voice/aria.wav",
  "promptText": "这是角色参考语音。",
  "promptLanguage": "zh",
  "textLanguage": "zh"
}
```

`VOICE.json` 同时兼容：

- `snake_case`
- `camelCase`

因此 persona 可以独立覆盖：

- provider
- endpoint
- voice
- Edge 的 voice / rate / volume
- GPT-SoVITS 的参考音频和采样参数

### 内置技能

当前仓库除默认技能外，还补了几类与本地产品化能力强相关的技能：

- `translate`
  忠实全文翻译，不用摘要代替翻译
- `llm-wiki`
  把当前 workspace 当成本地 wiki，用仓库里的文档、代码、配置和测试回答“这个东西在项目里是什么意思”
- `living-together`
  用 persona 参考图和 `image_gen` 把“你也在场”的生活陪伴场景做出来
- `emotional-companion`
  情绪感知、记忆跟进、heartbeat 主动关怀

这些技能复用了当前仓库已有的：

- persona
- memory
- image_gen
- heartbeat

## 聊天渠道

可接入渠道概览：

| 渠道 | 你需要准备什么 |
|------|----------------|
| Telegram | `@BotFather` 生成的 Bot Token |
| Discord | Bot Token + Message Content Intent |
| WhatsApp | 扫码登录 |
| WeChat / Weixin | 扫码登录 |
| Feishu | App ID + App Secret |
| DingTalk | App Key + App Secret |
| Slack | Bot Token + App-Level Token |
| Matrix | Homeserver + Access Token |
| Email | IMAP / SMTP 账号 |
| QQ | App ID + App Secret |
| Wecom | Bot ID + Secret |
| Mochat | Claw Token |

支持多实例的渠道包括：

- `whatsapp`
- `telegram`
- `discord`
- `feishu`
- `mochat`
- `dingtalk`
- `slack`
- `email`
- `qq`
- `matrix`
- `wecom`

多实例路由形式是 `channel/name`，例如 `telegram/main`。

### Telegram

最推荐的入门渠道。

配置示例：

```json
{
  "channels": {
    "telegram": {
      "enabled": true,
      "token": "YOUR_BOT_TOKEN",
      "allowFrom": ["YOUR_USER_ID"],
      "streaming": true,
      "streamEditInterval": 0.6,
      "inlineKeyboards": false
    }
  }
}
```

补充说明：

- `streaming` 默认就是 `true`，表示最终回复会优先走“先发一条、后续逐步编辑”的流式体验
- `streamEditInterval` 控制 Telegram `edit_message_text` 的最小节流间隔，适合按自己的频率/限流情况调整
- `inlineKeyboards` 开启后会把出站消息里的 `buttons` 渲染成 Telegram 原生内联按钮；关闭时会把按钮标签拼回正文，避免选项丢失

运行：

```bash
hahobot gateway
```

### Mochat

默认走 Socket.IO WebSocket，也支持 HTTP polling 回退。

最简单方式是直接让 hahobot 自己帮你接入 Mochat，英文 README 中保留了自动注册提示词。也可以手动配置：

```json
{
  "channels": {
    "mochat": {
      "enabled": true,
      "base_url": "https://mochat.io",
      "socket_url": "https://mochat.io",
      "socket_path": "/socket.io",
      "claw_token": "claw_xxx",
      "agent_user_id": "6982abcdef",
      "sessions": ["*"],
      "panels": ["*"]
    }
  }
}
```

### Discord

配置示例：

```json
{
  "channels": {
    "discord": {
      "enabled": true,
      "token": "YOUR_BOT_TOKEN",
      "allowFrom": ["YOUR_USER_ID"],
      "groupPolicy": "mention",
      "streaming": true,
      "readReceiptEmoji": "👀",
      "workingEmoji": "🔧",
      "workingEmojiDelay": 2.0
    }
  }
}
```

`groupPolicy`：

- `mention`
- `open`

补充说明：

- `streaming` 默认开启，Discord 现在支持和上游 nanobot 一样的流式回复编辑
- `readReceiptEmoji` / `workingEmoji` / `workingEmojiDelay` 用来控制收到消息后的已读/处理中反应提示
- 如果 Discord 需要走代理，可以配置 `proxy`，并按需补充 `proxyUsername` / `proxyPassword`

### WebSocket

如果你想把 hahobot 作为本地 WebSocket server 暴露给别的客户端，可以配置 `channels.websocket`：

```json
{
  "channels": {
    "websocket": {
      "enabled": true,
      "host": "127.0.0.1",
      "port": 8765,
      "path": "/ws",
      "allowFrom": ["*"],
      "websocketRequiresToken": false
    }
  }
}
```

- 连接形式通常是 `ws://127.0.0.1:8765/ws?client_id=your-client`
- 如果要启用短期 token，可继续配置 `tokenIssuePath` / `tokenIssueSecret`
- 详细协议说明见 [`docs/WEBSOCKET.md`](./docs/WEBSOCKET.md)

### Matrix

安装依赖：

```bash
pip install -e ".[matrix]"
```

配置示例：

```json
{
  "channels": {
    "matrix": {
      "enabled": true,
      "homeserver": "https://matrix.org",
      "userId": "@hahobot:matrix.org",
      "accessToken": "syt_xxx",
      "deviceId": "NANOBOT01",
      "e2eeEnabled": true,
      "allowFrom": ["@your_user:matrix.org"]
    }
  }
}
```

注意：

- 请保持稳定的 `deviceId`
- 多实例模式下会自动隔离到各自的 `matrix-store/<instance>`

### WhatsApp

需要：

- Node.js >= 18

登录：

```bash
hahobot channels login whatsapp
```

最小配置：

```json
{
  "channels": {
    "whatsapp": {
      "enabled": true,
      "allowFrom": ["+1234567890"]
    }
  }
}
```

运行时通常需要两个终端：

```bash
# Terminal 1
hahobot channels login whatsapp

# Terminal 2
hahobot gateway
```

当前本地 Node.js bridge 已支持以下标准代理环境变量：

- `https_proxy`
- `http_proxy`
- `all_proxy`

也支持 `SOCKS5` URL，例如：

```bash
export https_proxy=http://127.0.0.1:7890
hahobot channels login whatsapp
```

或：

```bash
export all_proxy=socks5://127.0.0.1:1080
hahobot channels login whatsapp
```

多实例时，每个实例应有自己的：

- `bridgeUrl`
- `AUTH_DIR`
- `BRIDGE_PORT`

### Feishu

配置示例：

```json
{
  "channels": {
    "feishu": {
      "enabled": true,
      "appId": "cli_xxx",
      "appSecret": "xxx",
      "allowFrom": ["ou_YOUR_OPEN_ID"],
      "groupPolicy": "mention"
    }
  }
}
```

### QQ

当前支持：

- 私聊
- 本地图片、`.mp4`、`.silk` 语音的文件上传

配置示例：

```json
{
  "channels": {
    "qq": {
      "enabled": true,
      "appId": "YOUR_APP_ID",
      "secret": "YOUR_APP_SECRET",
      "allowFrom": ["YOUR_OPENID"],
      "mediaBaseUrl": "https://files.example.com/out/"
    }
  }
}
```

补充说明：

- 对 `workspace/out` 下的本地富媒体，QQ 会优先走 `file_data`
- 本地文件不再回退到 URL 上传
- 支持的本地富媒体：图片、`.mp4`、`.silk`

### DingTalk

配置示例：

```json
{
  "channels": {
    "dingtalk": {
      "enabled": true,
      "clientId": "YOUR_APP_KEY",
      "clientSecret": "YOUR_APP_SECRET",
      "allowFrom": ["YOUR_STAFF_ID"]
    }
  }
}
```

### Slack

配置示例：

```json
{
  "channels": {
    "slack": {
      "enabled": true,
      "botToken": "xoxb-...",
      "appToken": "xapp-...",
      "allowFrom": ["YOUR_SLACK_USER_ID"],
      "groupPolicy": "mention"
    }
  }
}
```

### Email

配置示例：

```json
{
  "channels": {
    "email": {
      "enabled": true,
      "consentGranted": true,
      "imapHost": "imap.gmail.com",
      "imapPort": 993,
      "imapUsername": "my-hahobot@gmail.com",
      "imapPassword": "your-app-password",
      "smtpHost": "smtp.gmail.com",
      "smtpPort": 587,
      "smtpUsername": "my-hahobot@gmail.com",
      "smtpPassword": "your-app-password",
      "fromAddress": "my-hahobot@gmail.com",
      "allowFrom": ["your-real-email@gmail.com"]
    }
  }
}
```

### Weixin

从源码安装 Weixin 依赖：

```bash
cd /path/to/Hahobot
pip install -e ".[weixin]"
```

配置示例：

```json
{
  "channels": {
    "weixin": {
      "enabled": true,
      "allowFrom": ["YOUR_WECHAT_USER_ID"]
    }
  }
}
```

登录：

```bash
hahobot channels login weixin
```

### Wecom

安装可选依赖：

```bash
pip install -e ".[wecom]"
```

配置示例：

```json
{
  "channels": {
    "wecom": {
      "enabled": true,
      "botId": "your_bot_id",
      "secret": "your_bot_secret",
      "allowFrom": ["your_id"]
    }
  }
}
```

## Agent 社交网络

hahobot 可以接入 agent 社交网络。目前 README.md 中保留了例如：

- Moltbook
- ClawdChat

使用方式通常是把平台提供的 skill 地址作为消息发给 hahobot，让它自己读取并完成接入。

## 配置说明

默认配置文件：

- `~/.hahobot/config.json`

### Provider

支持的 provider 包括但不限于：

| Provider | 用途 |
|----------|------|
| `custom` | 任意 OpenAI 兼容接口 |
| `openrouter` | 推荐的聚合网关 |
| `openai` | GPT 官方接口 |
| `anthropic` | Claude 官方接口 |
| `azure_openai` | Azure OpenAI |
| `deepseek` | DeepSeek |
| `groq` | LLM + Whisper 语音转写 |
| `gemini` | Gemini |
| `dashscope` | 通义千问 |
| `moonshot` | Moonshot / Kimi |
| `zhipu` | GLM |
| `minimax` | MiniMax |
| `ollama` | 本地 Ollama |
| `ovms` | OpenVINO Model Server |
| `vllm` | 本地 vLLM 或任意兼容 OpenAI 的本地服务 |
| `openai_codex` | OAuth 登录的 Codex |
| `github_copilot` | OAuth 登录的 GitHub Copilot |

### OpenAI Codex OAuth

```bash
hahobot provider login openai-codex
```

配置模型：

```json
{
  "agents": {
    "defaults": {
      "model": "openai-codex/gpt-5.1-codex"
    }
  }
}
```

### GitHub Copilot OAuth

```bash
hahobot provider login github-copilot
```

### Custom Provider

```json
{
  "providers": {
    "custom": {
      "apiKey": "your-api-key",
      "apiBase": "https://api.your-provider.com/v1"
    }
  },
  "agents": {
    "defaults": {
      "model": "your-model-name"
    }
  }
}
```

### Ollama

```bash
ollama run llama3.2
```

```json
{
  "providers": {
    "ollama": {
      "apiBase": "http://localhost:11434"
    }
  },
  "agents": {
    "defaults": {
      "provider": "ollama",
      "model": "llama3.2"
    }
  }
}
```

### OVMS

适用于 Intel GPU 的 OpenVINO Model Server，本质上走 OpenAI 兼容接口。

```json
{
  "providers": {
    "ovms": {
      "apiBase": "http://localhost:8000/v3"
    }
  },
  "agents": {
    "defaults": {
      "provider": "ovms",
      "model": "openai/gpt-oss-20b"
    }
  }
}
```

### vLLM

```json
{
  "providers": {
    "vllm": {
      "apiKey": "dummy",
      "apiBase": "http://localhost:8000/v1"
    }
  },
  "agents": {
    "defaults": {
      "model": "meta-llama/Llama-3.1-8B-Instruct"
    }
  }
}
```

### Channel 通用设置

```json
{
  "channels": {
    "sendProgress": true,
    "sendToolHints": false,
    "sendMaxRetries": 3,
    "transcriptionProvider": "groq",
    "transcriptionLanguage": "zh"
  }
}
```

说明：

- `sendProgress`
  是否把 agent 的文字进度流式发到渠道
- `sendToolHints`
  是否把工具调用提示发到渠道
- `sendMaxRetries`
  出站消息失败时的最大重试次数
- `transcriptionProvider`
  语音转写后端，可选 `groq`（默认）或 `openai`；API Key 会自动从对应的
  `providers.groq` / `providers.openai` 读取，运行时重载配置后会直接更新到当前渠道实例
- `transcriptionLanguage`
  可选 ISO-639 语言提示，例如 `zh`、`en`、`ja`；留空时由转写后端自动识别

### MCP

hahobot 支持 [MCP](https://modelcontextprotocol.io/)。

配置示例：

```json
{
  "tools": {
    "mcpServers": {
      "filesystem": {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/dir"]
      },
      "my-remote-mcp": {
        "url": "https://example.com/mcp/",
        "headers": {
          "Authorization": "Bearer xxxxx"
        }
      }
    }
  }
}
```

支持两类传输：

- `command` + `args`
  本地 stdio
- `url` + `headers`
  远程 HTTP

如果你只想暴露某个 MCP 服务里的部分工具，可以在单个服务配置里加入 `enabledTools`。
它既接受原始 MCP 工具名，也接受包装后的 hahobot 工具名，例如
`mcp_filesystem_write_file`。省略该字段或设为 `["*"]` 表示注册全部工具，设为 `[]`
表示该服务一个工具也不注册。

### 通过 MCP 接入 Memorix

[Memorix](https://github.com/AVIDS2/memorix) 更适合作为工作区 / 代码库记忆层，而不是用户长期画像记忆。
建议通过 `tools.mcpServers` 接入，并继续保留当前文件式 `memory/MEMORY.md` 作为用户长期记忆主路径。
旧对话在被压缩归档时，hahobot 现在也会把结构化副本写到 `memory/archive/`，这样 agent 可以通过 `history_search` / `history_timeline` / `history_expand` 回放历史细节，而不只依赖 `HISTORY.md` 的 grep 检索。
如果需要更快的本地归档检索，可以设置 `memory.archive.indexBackend: "sqlite"` 启用 persona-local SQLite FTS 派生索引；`index.jsonl` 与 `chunks/*.json` 仍是事实来源，`hahobot memory index rebuild` 可随时重建 `memory/archive/index.sqlite`。
subagent 完成后的 follow-up 结果也会先落到 session history，再进入下一轮 prompt 组装，
这样后台任务回传不会只存在于瞬时上下文里，重试或异常恢复时也不会丢。

stdio 示例：

```json
{
  "tools": {
    "mcpServers": {
      "memorix": {
        "command": "memorix",
        "args": ["serve"],
        "toolTimeout": 60
      }
    }
  }
}
```

HTTP 示例：

```json
{
  "tools": {
    "mcpServers": {
      "memorix": {
        "type": "streamableHttp",
        "url": "http://127.0.0.1:3211/mcp",
        "toolTimeout": 60
      }
    }
  }
}
```

当 Memorix 工具可用时，hahobot 会自动：

- 把内置 `memorix` skill 注入系统提示
- 在每个 runtime MCP 连接 / chat session 首次使用时调用一次 `memorix_session_start`

### 运行时工具补充

当前内置工具除了 web / 文件 / shell / image_gen / history / cron / message / MCP 之外，还额外包括：

- `self_inspect`：只读运行时自检工具，返回当前 model、provider、注册工具、实际 session key 和运行中 subagent 的 JSON 快照
- `notebook_edit`：受控的 `.ipynb` 单元格编辑工具，支持 `replace` / `insert` / `delete`

其中 `self_inspect` 故意保持只读，不提供上游那类运行时自修改能力；`notebook_edit`
主 agent 默认可用，`spawn(mode=implement)` 的 subagent 也会拿到，而 `explore` /
`verify` 模式不会暴露它。

### 内置 Workflow Skills

hahobot 现在额外内置了一组偏工作流的 skills：

- `workflow-core`：默认 always-on，用来约束先看上下文、再拆步、再验证的基本协作节奏
- `plan`：需要显式规划时按当前代码库上下文整理步骤
- `verify`：独立做回归检查、风险复核和验证收口
- `skill-derive`：把重复工作流沉淀成 workspace skill 的模板化指引

同时，subagent 现在支持显式模式：

- `explore`：只读调查，不给写文件或执行 shell
- `implement`：正常实现模式，可读写文件并按配置使用 shell
- `verify`：独立验证模式，可读文件和执行检查命令，但不允许写文件

另外，`/skill derive <name> [brief] [--force]` 可以把当前会话最近一次成功流程和
`working_checkpoint` 提炼成当前 workspace 下的本地 skill 草稿
`workspace/skills/<name>/SKILL.md`，方便后续再人工收紧和复用。默认不会覆盖已有草稿，
只有显式传 `--force` 时才会重写。新生成的草稿会顺带写入一段 hahobot 自己用的
`metadata`，用于描述 `triggers`、`tool_tags`、`supersedes`、`last_used`、
`success_count` 这些生命周期提示。

`/skill lint` 是只读的本地 skill 体检命令，用来检查当前 workspace 里是否存在明显的
skill 重叠、缺失的 `supersedes` 目标，以及哪些旧 skill 已经因为被新 skill 取代而不会再进入
运行时 summary。

`/skill supersede <newer> <older> [more...]` 则是配套的显式元数据维护命令，用来给新的
workspace skill 补上 `supersedes` 关系；现在还支持
`/skill supersede remove <newer> <older> [more...]` 和
`/skill supersede clear <newer>`，把减法链路也补齐。它们都只更新 metadata，不会自动删除或
合并旧 skill，这样运行时选择可以收缩，但旧草稿仍然保留给你审阅。

现在如果 agent 在某一轮里真的读取了 `workspace/skills/<name>/SKILL.md`，hahobot 会尽力回写
该 skill 的 `last_used`；只有这一轮最终不是 `error`、`tool_error`、`empty_final_response`、
`max_iterations` 这类失败结束时，才会额外给 `success_count` 加一。

### 隐藏内置或工作区 Skill

如果你不希望某些 skill 暴露给主 agent 或 subagent，可以设置
`agents.defaults.disabledSkills`，填 skill 目录名列表即可：

```json
{
  "agents": {
    "defaults": {
      "disabledSkills": ["github", "weather"]
    }
  }
}
```

另外，运行时注入给模型的 skill summary 不再是“把所有 skill 全塞进去”，而是会结合当前
query 做 top-k 选择；如果新 skill 通过 `supersedes` 明确声明替代旧 skill，只要新 skill
当前可用，旧 skill 就会默认从共享 summary 里隐藏，减少 prompt 和 skill 选择混乱。

### 空闲会话自动 Compact

可以通过 `agents.defaults.idleCompactAfterMinutes` 让 hahobot 在会话空闲一段时间后，
后台归档较早的 live 消息，同时保留最近一段合法后缀。用户回来继续说话时，runtime
context 会先注入一次恢复摘要，再继续正常对话。旧别名 `sessionTtlMinutes` 仍然兼容。
- 把当前 hahobot workspace 作为 `projectRoot` 绑定给 Memorix

这意味着项目历史、设计原因、排障经验等问题可以直接利用 Memorix，但不会替代当前文件记忆主链路。
如果你使用内置 admin 页面，现在也可以直接在可视化配置里编辑专门的 `Memorix MCP` 分区。

### Mem0 用户记忆

hahobot 现在可以把 Mem0 作为真正的用户记忆后端使用。

- `memory.user.backend: "file" | "mem0"` 用来选择主用户记忆后端。默认仍是 `file`，继续把 persona 的 `MEMORY.md` 注入 prompt。
- `memory.user.backend: "mem0"` 时，会从 Mem0 检索记忆上下文，并在每轮完成后把 turn 写入 Mem0。
- 当 `memory.user.backend=mem0` 时，hahobot 仍会保留 file 侧的 `MEMORY.md` 作为 prompt 注入保底来源；如果 Mem0 没检索到内容或查询失败，会自动回退到现有文件记忆。
- `memory.user.shadowWriteMem0: true` 可以保持 `file` 为主后端，同时并行双写到 Mem0。
- 运行时需要额外安装依赖：`uv sync --extra mem0` 或 `pip install -e ".[mem0]"`。
- `memory.user.mem0.llm`、`embedder`、`vectorStore` 建议优先使用显式字段：`provider`、`apiKey`、`url`、`model`、`headers`。
- provider 私有扩展参数继续放在 `config` 中，顶层 `metadata` 会在写入 Mem0 时一起附带。

示例：

```json
{
  "memory": {
    "user": {
      "backend": "mem0",
      "shadowWriteMem0": false,
      "mem0": {
        "llm": {
          "provider": "openai",
          "apiKey": "mem0-llm-key",
          "url": "https://api.mem0.ai/v1",
          "model": "gpt-4.1-mini"
        },
        "embedder": {
          "provider": "openai",
          "apiKey": "mem0-embed-key",
          "url": "https://embed.mem0.ai/v1",
          "model": "text-embedding-3-small"
        },
        "vectorStore": {
          "provider": "qdrant",
          "apiKey": "mem0-vs-key",
          "url": "https://qdrant.mem0.ai",
          "config": {
            "collectionName": "hahobot_user_memory"
          }
        },
        "metadata": {
          "tenant": "prod"
        }
      }
    }
  }
}
```

如果你使用内置 admin 页面，现在也可以直接在可视化配置里编辑 `Mem0 用户记忆` 分区，包括 `memory.user.backend`、`shadowWriteMem0`，以及常用字段和 `headers` / `config` / `metadata` 的 JSON textarea。

### 安全

生产环境建议：

- `tools.restrictToWorkspace: true`

关键项：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `tools.restrictToWorkspace` | `false` | 把 shell、读写文件、列目录等工具限制在 workspace 内 |
| `tools.exec.enable` | `true` | 关闭后不注册 shell 工具 |
| `tools.exec.pathAppend` | `""` | 给 shell 额外追加 PATH |
| `tools.exec.allowedEnvKeys` | `[]` | 显式透传给 shell 子进程的环境变量名列表 |
| `tools.imageGen.enabled` | `false` | 开启内置 `image_gen` |
| `channels.*.allowFrom` | `[]` | 白名单，空数组默认拒绝所有 |

### Admin 页面

当前仓库内置了实例级 admin 页面，但默认关闭。必须在启动该 gateway 进程所使用的同一个
`config.json` 里显式开启：

```json
{
  "gateway": {
    "admin": {
      "enabled": true,
      "authKey": "replace-with-a-long-random-key"
    }
  }
}
```

行为规则：

- 页面路径是同一个 gateway 进程下的 `/admin`
- `gateway.admin.enabled=false` 时，`/admin` 直接返回 `404`
- 开启后必须输入 `authKey` 才能进入
- 页面默认中文，可切换英文，并且会自动跟随系统亮/暗主题
- 在 admin 配置页保存后，当前实例会立即强制重载支持热更新的运行时配置
- admin 页面处理的是当前实例自己的 `config.json` 和当前运行 workspace 下的 persona 文件
- 多实例下，每个 `--config` 启动的进程都有自己独立的 admin 开关、密钥和 workspace 作用域

当前 admin 页面支持：

- 可视化编辑并校验 `config.json`，同时保留高级 JSON 兜底编辑器
- `/admin` 和浏览器访问的 `/status` 采用 Hermes 风格的深色 dashboard 视觉，但仍复用当前 hahobot gateway 进程，不额外引入第二套前端运行时
- admin 新增只读的会话页、技能页和 Cron 页，可直接查看当前 runtime workspace 下保存的 session、已加载 skill 和 `cron/jobs.json`
- 可视化编辑 `gateway.status`，用于 Star-Office-UI 一类状态看板访问当前实例的 `GET /status`，也可直接配置 HTTP 主动推送
- 可视化编辑 `agents.defaults.providerPool`，提供按行维护 targets 的列表式界面，支持新增 / 删除 / 排序，以及故障切换 / 轮询策略
- 可视化编辑常用 `providers.*` 配置块，例如 `openrouter`、`openai`、`anthropic`、`deepseek`、`custom`、`ollama`、`vllm`，并按 provider 分组成可折叠卡片，收起时显示安全摘要
- 可视化编辑常见单实例 channel 凭据块，例如 `whatsapp`、`telegram`、`discord`、`feishu`、`dingtalk`、`slack`、`qq`、`matrix`、`weixin`、`wecom`；若某个 channel 已使用 `instances` 多实例结构，这里会只读提示，仍需在高级 JSON 中维护
- Telegram / Discord 单实例卡片同时覆盖常用附加项，例如 `channels.telegram.streamEditInterval`，以及 Discord 的 `streaming`、`readReceiptEmoji`、`workingEmoji`、`workingEmojiDelay`、`proxy`、`proxyUsername`、`proxyPassword`
- admin 内置专门的 Weixin 扫码登录页，可直接为当前实例申请并轮询个人微信登录二维码；扫码成功后，token 会保存到当前实例的 Weixin 状态文件
- 可视化编辑 `tools.exec`，用于控制 shell 命令执行、超时时间、额外 PATH、`allowedEnvKeys` 和可选 `sandbox`
- 可视化编辑渠道运行时分区，例如 `channels.sendProgress`、`channels.sendToolHints`、`channels.sendMaxRetries`、`channels.transcriptionProvider` 和 `channels.voiceReply.*`
- 可视化编辑专门的 `Memorix MCP` 分区，对应 `tools.mcpServers.memorix`
- 可视化编辑 `Mem0 用户记忆` 分区，对应 `memory.user.backend`、`shadowWriteMem0` 和 `memory.user.mem0`
- 独立的命令总览页，展示所有聊天 slash 命令、别名和用法
- 每个可视化配置项都带悬浮说明，鼠标移动到字段名即可查看详细解释
- 每个可视化配置项都会直接标注“可热重载”或“需重启”
- 编辑当前 runtime workspace 下 persona 的 `SOUL.md`、`USER.md`、可选 `PROFILE.md`、可选 `INSIGHTS.md`、`STYLE.md`、`LORE.md`
- 编辑 persona 的 `VOICE.json`
- 可视化编辑 persona 的 companion scene 字段，例如 `/scene` 的默认参考图、分场景参考图、prompt 覆盖和配文覆盖
- 在 persona 页面里直接生成 `/scene` 预览图，复用当前 runtime 的 imageGen 配置
- 在 persona 页面里把当前 `/scene` 预览直接保存成具名 scene 模板，回写 `scene_prompts` / `scene_captions`
- 编辑 persona 的 `.hahobot/st_manifest.json`
- 在 persona 页面查看 `PROFILE.md` / `INSIGHTS.md` 的 metadata 摘要，包括结构化 `confidence` / `last_verified` 统计和遗留 `(verify)` 标记数量
- 在 persona 页面提供旧版 `USER.md` 迁移预览/执行操作，先显示迁移后 `USER.md` / `PROFILE.md` / `INSIGHTS.md` 的实际内容，再把明显的用户画像内容拆到 `PROFILE.md`，把协作/工作方式提示拆到 `INSIGHTS.md`

如果你在 admin 页面里改了 `agents.defaults.workspace`，当前 gateway 实例会在保存后立即切换到
新的 runtime workspace。只有表单里明确标注“需重启”的字段，才需要重启当前进程才能生效。
`agents.defaults.providerPool` 也属于需重启项，因为它会改变 provider 路由策略。
`agents.defaults.dream` 同样需要重启，因为它会改变 gateway 内置的 Dream 系统任务。
Dream 配置放在 `agents.defaults.dream` 下，常规调度字段使用 `intervalH`；
旧配置里的 `cron` 和 `model` 输入仍然兼容。

### Star Office UI 状态接口

hahobot 可以额外暴露一个很小的 HTTP 状态接口，方便接入
[`Star-Office-UI`](https://github.com/ringhyacinth/Star-Office-UI) 这类看板。该接口默认关闭，
由同一个 `hahobot gateway` 进程提供。

```json
{
  "gateway": {
    "status": {
      "enabled": true,
      "authKey": "optional-bearer-token",
      "push": {
        "enabled": true,
        "mode": "guest",
        "officeUrl": "https://office.example.com",
        "joinKey": "replace-with-your-join-key",
        "agentName": "hahobot",
        "timeout": 10
      }
    }
  }
}
```

行为规则：

- 路径是当前 gateway 进程下的 `GET /status`
- `gateway.status.enabled=false` 时，`/status` 返回 `404`
- 如果 `gateway.status.authKey` 非空，请在请求头里带上 `Authorization: Bearer <authKey>`
- 脚本/API 请求会继续返回 JSON，包含 `state`、`detail`、`updatedAt`、`activeRuns` 等稳定字段
- 浏览器访问时会渲染内置状态页，展示 hahobot 是否正常运行、连续运行时间、最近一次处理任务的当前步骤 / 下一步 / 响应摘要、最近活跃 persona 的 `PROFILE.md` / `INSIGHTS.md` memory layer 摘要，以及当前 heartbeat / 模型检测状态
- hahobot 会根据 agent 生命周期自动刷新状态，当前会使用 `idle`、`researching`、`executing`、`syncing`、`writing`、`error` 这些状态值
- `gateway.status.push.mode=guest` 会作为访客 Agent 调用 `join-agent` / `agent-push`，此时必须填写 `joinKey`
- `gateway.status.push.mode=main` 会驱动内置主 Agent 的 `set_state`，此时不需要 `joinKey`

接 `Star-Office-UI` 时，把它本地轮询/推送脚本指向你的 gateway 即可，例如：

```bash
python office-agent-push.py --status-url http://127.0.0.1:18790/status
```

如果你配置了 `gateway.status.authKey`，记得在 Star Office 侧脚本或反向代理里同步附带对应
Bearer Token。

如果你不想轮询 `/status`，也可以直接开启 `gateway.status.push`。开启后，hahobot 会主动向
配置好的 Star-Office-UI 地址推送运行状态；既可以作为访客 Agent 注册，也可以直接驱动主办公室状态。

主 Agent 模式示例：

```json
{
  "gateway": {
    "status": {
      "push": {
        "enabled": true,
        "mode": "main",
        "officeUrl": "http://127.0.0.1:19000",
        "timeout": 10
      }
    }
  }
}
```


### 时区

默认使用 `UTC`。如果希望模型按本地时间理解运行时上下文：

```json
{
  "agents": {
    "defaults": {
      "timezone": "Asia/Shanghai"
    }
  }
}
```

## 多实例

可通过不同的 `--config` 和 `--workspace` 同时运行多个 hahobot 实例。
如果只传 `--config`，默认工作空间会跟随该配置文件目录，使用 `<config-dir>/workspace`。
如果配置里设置了 `agents.defaults.workspace`，则以配置值为准；命令行 `--workspace` 仍然最高优先级。

初始化多个实例：

```bash
hahobot onboard --config ~/.hahobot-telegram/config.json --workspace ~/.hahobot-telegram/workspace
hahobot onboard --config ~/.hahobot-discord/config.json --workspace ~/.hahobot-discord/workspace
hahobot onboard --config ~/.hahobot-feishu/config.json --workspace ~/.hahobot-feishu/workspace
```

启动：

```bash
hahobot gateway --config ~/.hahobot-telegram/config.json
hahobot gateway --config ~/.hahobot-discord/config.json
hahobot gateway --config ~/.hahobot-feishu/config.json --port 18792
```

路径解析规则：

| 组件 | 来源 |
|------|------|
| Config | `--config` |
| Workspace | `--workspace`、`agents.defaults.workspace` 或 `<config-dir>/workspace` |
| Cron Jobs | config 所在目录 |
| 媒体 / 运行时状态 | config 所在目录 |

补充：

- `gateway.admin` 也是实例级配置，因为它和当前进程使用的是同一个 `config.json`

适用场景：

- 不同渠道独立运行
- 测试 / 生产隔离
- 不同团队用不同模型或 provider
- 多租户隔离

## CLI 参考

| 命令 | 说明 |
|------|------|
| `hahobot onboard` | 初始化默认配置和工作区 |
| `hahobot onboard --wizard` | 使用交互式向导初始化 |
| `hahobot agent` | 交互式 CLI 对话 |
| `hahobot agent --continue` | 自动恢复最近一次本地 CLI 会话 |
| `hahobot agent --pick-session` | 启动前交互式选择一个最近的本地 CLI 会话 |
| `hahobot agent --multiline` | 开启多行输入模式，`Enter` 换行，`Ctrl+J` 发送 |
| `hahobot agent -m "..."` | 单轮消息 |
| `hahobot agent -w <workspace>` | 指定工作区启动 |
| `hahobot agent -w <workspace> -c <config>` | 指定工作区和配置启动 |
| `hahobot serve` | 启动 OpenAI 兼容 API |
| `hahobot gateway` | 启动网关 |
| `hahobot doctor [--json]` | 只读检查当前实例的配置、工作区、模型路由、渠道与工具准备情况 |
| `hahobot model [--json]` | 查看当前默认模型、provider 解析结果与 provider pool 路由 |
| `hahobot tools [--json]` | 查看 web / exec / imageGen / MCP 当前配置与准备情况 |
| `hahobot sessions list [--json]` | 列出当前 workspace 最近保存的会话，可配合 `hahobot agent --continue` 使用 |
| `hahobot sessions show <key> [--json]` | 查看指定会话的元数据、working checkpoint 和最近消息 |
| `hahobot sessions export <key> [--format md|json] [--output <path>]` | 导出一个已保存会话，默认写到 `workspace/out/sessions/` |
| `hahobot sessions compact <key> [--json]` | 手动对一个已保存会话执行现有的 token consolidation，并持久化更新后的游标 |
| `hahobot memory index rebuild [--json]` | 从 `memory/archive/` 的 JSON 事实来源重建可选 SQLite FTS 归档索引 |
| `hahobot repo status [--json]` | 只读查看当前 workspace 对应 Git 仓库的分支、跟踪状态和改动计数 |
| `hahobot repo diff [--staged] [--name-only] [--json]` | 只读查看当前 workspace 对应 Git 仓库的 tracked diff 摘要 |
| `hahobot review [--staged] [--base <rev>] [--path <path>] [--json]` | 用当前配置的模型只读审查当前 workspace 的 Git diff |
| `hahobot status` | 查看状态 |
| `hahobot companion init [--persona <name>]` | 初始化 companion persona 脚手架 |
| `hahobot companion doctor [--persona <name>]` | 检查 companion 工作流所需配置与资产 |
| `hahobot channels login <channel>` | 交互式登录某个渠道 |
| `hahobot channels status` | 查看渠道状态 |
| `hahobot persona import-st-card <file>` | 导入 SillyTavern 角色卡 |
| `hahobot persona import-st-preset <file> --persona <name>` | 导入 preset 到 persona |
| `hahobot persona import-st-worldinfo <file> --persona <name>` | 导入 world info 到 persona |
| `hahobot provider login openai-codex` | Codex OAuth 登录 |
| `hahobot provider login github-copilot` | GitHub Copilot OAuth 登录 |

在 `hahobot agent` 的本地交互模式里，还支持一组不会发给模型的本地命令：

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

同一组 `/session ...`、`/repo ...`、`/review ...`、`/compact ...` 现在也可以直接在 gateway
承载的聊天里使用。其中 gateway 下的 `/session use <key>` 和 `/session new [name]` 只影响当前
聊天的会话路由；如果要切回原始 session key，可用 `/session use default`。

交互式 CLI 输入还会为 slash 命令提供补全，覆盖内置命令、常见子命令，以及当前
workspace 里的 persona、scene 名称，以及内置 `/update` 与本地 `/session ...`、`/repo ...`、`/review ...`、`/compact` 候选。

其中 `/repo diff` 只看 tracked changes；如果还想确认 untracked 文件数量，用 `/repo status`。
`/review` 则会把当前 diff 交给已配置模型做 findings-first 的代码审查，不会直接改动仓库文件。
`/compact` 直接复用现有自动 token consolidation 逻辑，不会额外引入第二套 memory 流程。
`/update` 现在支持三种子命令：

- `/update`：对当前 Git checkout 执行 fast-forward 同步、运行 `uv sync --locked --all-extras`，
  如果当前启用了 WhatsApp 还会刷新本地 bridge，并在成功后自动重启
- `/update check`：只做体检，不实际修改仓库或重启
- `/update force`：跳过“工作树必须干净”的前置拒绝，再继续默认更新流程
- `/update bridge`：只刷新本地 WhatsApp bridge 并重启

默认 `/update` 仍然会在工作树不干净或当前分支没有 upstream 时直接拒绝执行。

### Persona 资产

当前仓库支持把 SillyTavern 资产导入到 `<workspace>/personas/<name>/`，而不是使用全局 `~/.hahobot/sillytavern`。

导入角色卡：

```bash
hahobot persona import-st-card /path/to/aria.json -w <workspace>
```

导入 preset：

```bash
hahobot persona import-st-preset /path/to/preset.json --persona Aria -w <workspace>
```

导入 world info：

```bash
hahobot persona import-st-worldinfo /path/to/worldinfo.json --persona Aria -w <workspace>
```

导入完角色、语音和参考图后，可以先跑一遍只读诊断：

```bash
hahobot doctor
hahobot model
hahobot tools
hahobot sessions list
hahobot companion init --persona Aria
hahobot companion init --persona Aria --reference-image ./aria.png
hahobot companion doctor --persona Aria
hahobot companion doctor --persona Aria --json
```

聊天里也有一组偏 NanoMate 风格的 companion 快捷命令：

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
/scene generate 雨天书店一起避雨
```

其中 `/scene` 会直接调用内置 `image_gen` 工具返回图片；如果当前 persona 配了参考图，
会优先复用对应 reference image 来保持角色外观一致。manifest 里额外定义的自定义 scene 名
也可以直接用 `/scene <name>` 调用。

如果你想让某个 persona 的 `/scene daily`、`/scene comfort`、`/scene date` 更贴近它自己的世界观，
可以直接在 `.hahobot/st_manifest.json` 里补 `scene_prompts`，也可以用 `scene_captions` 覆盖默认配文。
admin 的 persona 页面也可以直接生成 `/scene` 预览，并把当前预览一键保存回
`.hahobot/st_manifest.json` 作为具名 scene 模板。

生成的典型目录结构：

```text
personas/Aria/
  SOUL.md
  USER.md
  PROFILE.md  # optional
  INSIGHTS.md # optional
  STYLE.md
  LORE.md
  memory/
  .hahobot/
```

manifest 中可声明：

- `response_filter_tags`
- `reference_image`
- `reference_images`

其中可选的 `PROFILE.md` 用来保存长期用户画像，例如稳定偏好、习惯和协作模式；它和 `USER.md` 不同，`USER.md` 仍然用于描述 persona 对用户的关系定位和互动边界。可选的 `INSIGHTS.md` 则用于沉淀长期协作洞察，例如被验证有效的工作方式、策略启发和反复出现的坑点。

在记忆卫生上，`USER.md` 应继续只放关系定位；`PROFILE.md` 放稳定用户事实；`INSIGHTS.md`
放被验证有效的协作规律。若某条记忆还不完全确定，优先保留一条带结构化 metadata 的规范条目，
例如 `<!-- hahobot-meta: confidence=low -->`；若当前批次明确重新确认了某条事实或规律，则补
上 `last_verified=YYYY-MM-DD`。旧的 `(verify)` 标记仍然兼容，但新写入或被修改的条目应优先
使用结构化 metadata，而不是自由文本后缀。这两个可选文件默认不会在新 workspace 中预置，需要时再创建即可。聊天 `/status` 和浏览器访问的 `/status` 页面会沿用同一套术语，显示当前或最近活跃 persona 的 `PROFILE.md` / `INSIGHTS.md` 摘要。

### 聊天内斜杠命令

| 命令 | 说明 |
|------|------|
| `/new` | 开新会话 |
| `/lang current` | 查看当前命令语言 |
| `/lang list` | 查看可用语言 |
| `/lang set <en\|zh>` | 切换命令语言 |
| `/persona current` | 查看当前 persona |
| `/persona list` | 列出 persona |
| `/persona set <name>` | 切换 persona |
| `/stchar list` | 以 NanoMate 风格列出可用角色 |
| `/stchar show <name>` | 查看某个角色的资产摘要 |
| `/stchar load <name>` | 将该角色载入当前会话 |
| `/preset` | 查看当前 persona 的 preset 资产 |
| `/preset show [persona]` | 查看指定 persona 的 preset 资产 |
| `/scene list` | 查看当前 persona 可用的内置与自定义场景 |
| `/scene daily` | 直接生成日常陪伴场景图 |
| `/scene comfort` | 直接生成安慰陪伴场景图 |
| `/scene date` | 直接生成约会场景图 |
| `/scene <custom_scene>` | 生成 persona manifest 里定义的自定义场景图 |
| `/scene generate <brief>` | 按自定义描述生成陪伴场景图 |
| `/skill search <query>` | 搜索公共技能 |
| `/skill install <slug>` | 安装 workspace 技能 |
| `/skill uninstall <slug>` | 卸载 workspace 技能 |
| `/skill list` | 查看技能 |
| `/skill update` | 更新技能 |
| `/skill derive <name> [brief] [--force]` | 从当前会话生成或显式覆盖本地 skill 草稿 |
| `/skill supersede <newer> <older> [more...]` | 为新的 workspace skill 声明替代哪些旧 skill |
| `/skill supersede remove <newer> <older> [more...]` | 从 supersedes 里移除指定旧 skill |
| `/skill supersede clear <newer>` | 清空某个 workspace skill 的 supersedes |
| `/skill lint` | 检查本地 skill 的 supersedes 和重叠问题 |
| `/mcp [list]` | 查看 MCP 服务和工具 |
| `/stop` | 停止当前任务 |
| `/restart` | 重启进程 |
| `/update` | 同步仓库、更新依赖/bridge 并重启 |
| `/update check` | 只检查当前是否可更新 |
| `/update force` | 跳过工作树干净检查后继续更新 |
| `/update bridge` | 只刷新 WhatsApp bridge 并重启 |
| `/status` | 查看运行状态 |
| `/help` | 查看帮助 |

## 外部 Hook Bridge

如果你已经有现成的 shell / Python 自动化，不想自己实现一个 Python `AgentHook`，现在可以直接把
生命周期事件桥接到外部命令：

```python
import asyncio

from hahobot import ExternalHookBridge, Hahobot


async def main() -> None:
    bot = Hahobot.from_config()
    hook = ExternalHookBridge(
        ["python", "scripts/audit_hook.py"],
        events=["before_iteration", "before_execute_tools", "after_iteration"],
    )
    result = await bot.run("总结一下这个仓库", hooks=[hook])
    print(result.content)


asyncio.run(main())
```

外部命令会从 stdin 收到一个 JSON 对象，包含 `schema_version`、`event` 和 `context`。默认不会强制
开启 streaming；只有你显式把 `on_stream` 或 `on_stream_end` 放进 `events` 时，bridge 才会要求
逐段流式事件。

如果你想把外部命令当成策略门禁，可以在 `before_iteration` 或 `before_execute_tools` 阶段返回
`{"continue": false, "message": "..."}`，或者直接用退出码 `2` 明确阻断。其他非零退出码默认是
fail-open，只会记日志；如果你希望外部命令失败时直接让任务失败，可以改用 `fail_open=False`。

## OpenAI 兼容 API

hahobot 可以暴露一个最小化的 OpenAI 兼容接口，方便本地集成：

```bash
pip install -e ".[api]"
hahobot serve
```

默认绑定地址为 `127.0.0.1:8900`。

### 行为约束

- 固定会话：所有请求共享同一个 hahobot 会话 `api:default`
- 单消息输入：每次请求必须只包含一条 `user` 消息
- 固定模型：可以省略 `model`，或者传入 `/v1/models` 返回的同一个模型名
- 支持 `application/json` 和 `multipart/form-data`
- content array 里可以带内嵌 base64 / data URL 文件块，也可以直接走 multipart 上传文件
- 文本型附件会提取进提示词；二进制 / 图片附件在 direct API 路径上会降级成稳定占位说明
- 不支持流式：`stream=true` 当前不支持

如果走 `multipart/form-data`，请继续用 `messages` 字段传 JSON 字符串，再把一个或多个文件和它一起上传。

### 接口

- `GET /health`
- `GET /v1/models`
- `POST /v1/chat/completions`

### curl 示例

```bash
curl http://127.0.0.1:8900/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [
      {
        "role": "user",
        "content": "hi"
      }
    ]
  }'
```

### Python（`requests`）

```python
import requests

resp = requests.post(
    "http://127.0.0.1:8900/v1/chat/completions",
    json={
        "messages": [
            {"role": "user", "content": "hi"}
        ]
    },
    timeout=120,
)
resp.raise_for_status()
print(resp.json()["choices"][0]["message"]["content"])
```

### Python（`openai`）

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8900/v1",
    api_key="dummy",
)

resp = client.chat.completions.create(
    model="MiniMax-M2.7",
    messages=[{"role": "user", "content": "hi"}],
)
print(resp.choices[0].message.content)
```

## 周期任务

`HEARTBEAT.md` 用来描述周期性任务。agent 也可以自己维护它，例如让它“添加一个周期任务”，它会直接更新 `HEARTBEAT.md`。

运行中的 workspace 级 cron service 现在也会周期性重新读取自己的 `cron/jobs.json`。这意味着即使当前调度器手里只有很远之后才触发的任务，另一个进程后面新增的更早任务也能被及时发现，不需要重启 gateway。

如果需要调节这个轮询上限，可以配置：

```json
{
  "gateway": {
    "cron": {
      "maxSleepMs": 300000
    }
  }
}
```

前提：

- `hahobot gateway` 正在运行
- 你至少和 bot 对话过一次，系统知道要把结果发往哪个渠道

## Docker

仓库已经提供：

- `Dockerfile`
- `docker-compose.yml`

`docker-compose` 快速开始：

```bash
docker compose run --rm hahobot-cli onboard
vim ~/.hahobot/config.json
docker compose up -d hahobot-gateway
```

常用命令：

```bash
docker compose run --rm hahobot-cli agent -m "Hello!"
docker compose logs -f hahobot-gateway
docker compose down
```

直接使用 `docker`：

```bash
docker build -t hahobot .
docker run -v ~/.hahobot:/root/.hahobot --rm hahobot onboard
docker run -v ~/.hahobot:/root/.hahobot -p 18790:18790 hahobot gateway
docker run -v ~/.hahobot:/root/.hahobot --rm hahobot agent -m "Hello!"
```

补充说明：

- `-v ~/.hahobot:/root/.hahobot` 用于把宿主机配置和工作区挂进容器
- 如果要跑 WhatsApp，多实例通常还需要多个 bridge 进程
- 如果走代理，记得把代理环境变量传进容器或 bridge 进程

## Linux 服务

可以把网关作为 systemd 用户服务启动。

先找可执行文件：

```bash
which hahobot
```

创建 `~/.config/systemd/user/hahobot-gateway.service`：

```ini
[Unit]
Description=Hahobot Gateway
After=network.target

[Service]
Type=simple
ExecStart=%h/.local/bin/hahobot gateway
Restart=always
RestartSec=10
NoNewPrivileges=yes
ProtectSystem=strict
ReadWritePaths=%h

[Install]
WantedBy=default.target
```

启用：

```bash
systemctl --user daemon-reload
systemctl --user enable --now hahobot-gateway
```

常用操作：

```bash
systemctl --user status hahobot-gateway
systemctl --user restart hahobot-gateway
journalctl --user -u hahobot-gateway -f
```

如果希望退出登录后服务仍然运行：

```bash
loginctl enable-linger $USER
```

## 上游同步台账

[`UPSTREAM_PARITY.md`](./UPSTREAM_PARITY.md) 是当前本地 fork 的上游同步台账。

以后手工同步 `HKUDS/nanobot` 时，优先看这里：它会记录哪些能力已经本地对齐、哪些是明确的本地分叉，
以及下次同步时还需要重点复核哪些区域。

## 项目结构

```text
hahobot/
├── agent/          核心 agent 逻辑
│   ├── loop.py
│   ├── context.py
│   ├── memory.py
│   ├── skills.py
│   ├── subagent.py
│   └── tools/
├── skills/         内置技能
├── channels/       聊天渠道适配
├── bus/            消息路由
├── cron/           定时任务
├── heartbeat/      主动唤醒
├── providers/      模型与语音 provider
├── session/        会话管理
├── config/         配置模型与解析
└── cli/            CLI 命令
bridge/             WhatsApp Node.js bridge
tests/              测试
```

## 贡献与路线图

欢迎提 PR。这个项目的一个重要特点就是代码量小、结构清晰、方便继续演进。

分支策略：

| 分支 | 用途 |
|------|------|
| `main` | 稳定版本，修复 bug 与小幅增强 |
| `nightly` | 实验性功能与潜在破坏性改动 |

路线图方向：

- 多模态能力继续增强
- 长期记忆持续优化
- 多步推理与反思能力
- 更多外部集成
- 自我改进与反馈闭环

## 中文文档说明

这份 `README_ZH.md` 是面向当前工作仓库的完整中文整理版，重点保证这些内容是准确同步的：

- persona / SillyTavern 资产导入
- persona 参考图与 `image_gen`
- `channels.voiceReply` 的 `openai` / `edge` / `sovits`
- `VOICE.json` 自定义声线
- 陪伴技能与翻译技能
- WhatsApp 本地 bridge 代理支持

如果你需要逐段对照的原始英文说明、完整细节或最新补充，请直接查看：

- [README.md](./README.md)
