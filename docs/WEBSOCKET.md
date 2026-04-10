# WebSocket Channel

`channels.websocket` 会让 `hahobot gateway` 直接作为一个本地 WebSocket server 对外提供会话入口。

## 最小配置

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

## 连接方式

- 直接连接：

```text
ws://127.0.0.1:8765/ws?client_id=my-client
```

- 如果开启静态 token：

```text
ws://127.0.0.1:8765/ws?client_id=my-client&token=your-token
```

- 如果开启短期 token：
  - 先对 `tokenIssuePath` 发起 HTTP GET
  - 再把返回的 `token` 放到 WebSocket URL 查询参数里

## 常用配置项

| 字段 | 说明 |
| --- | --- |
| `host` / `port` / `path` | WebSocket server 监听地址 |
| `allowFrom` | 允许的 `client_id` 白名单，`["*"]` 表示放行所有客户端 |
| `token` | 固定握手 token |
| `tokenIssuePath` | 短期 token 签发 HTTP 路径 |
| `tokenIssueSecret` | 访问 `tokenIssuePath` 时要求的 Bearer secret |
| `websocketRequiresToken` | 没有静态 token 时，是否仍强制要求短期 token |
| `streaming` | 是否向客户端发送 `delta` / `stream_end` 流式事件 |
| `sslCertfile` / `sslKeyfile` | 配置后使用 WSS |

## 帧协议

- 客户端发给 hahobot：
  - 纯文本：直接当成用户消息
  - JSON：支持 `content` / `text` / `message`

- hahobot 发给客户端：
  - `{"event":"ready","chat_id":"...","client_id":"..."}`：握手成功
  - `{"event":"message","text":"..."}`：普通回复
  - `{"event":"delta","text":"...","stream_id":"..."}`：流式增量
  - `{"event":"stream_end","stream_id":"..."}`：一个流式片段结束

## 注意事项

- 每个连接会分配一个独立 `chat_id`，并映射到内部 session。
- 如果返回里带 `media`，其中仍然是本地文件路径；hahobot 不会自动把这些路径变成 HTTP 可访问 URL。
- 如果需要让外部客户端访问本地输出文件，请自行提供静态文件服务，并把相关路径转换为你自己的可访问地址。
