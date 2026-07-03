"""Tests for the server-rendered chat WebUI (nanobot-style, admin-shared auth)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from aiohttp import WSServerHandshakeError

from hahobot.config.loader import save_config
from hahobot.config.schema import Config
from hahobot.gateway.admin.base import _build_session_cookie
from hahobot.gateway.http import create_http_app
from hahobot.session.manager import SessionManager

try:
    from aiohttp.test_utils import TestClient, TestServer

    HAS_AIOHTTP = True
except ImportError:  # pragma: no cover
    HAS_AIOHTTP = False


AUTH_KEY = "secret-key"


class _StreamingAgent:
    """Minimal AgentLoop stand-in that drives on_progress + on_stream then returns."""

    def __init__(self, reply: str = "Hello", media: list[str] | None = None) -> None:
        self.reply = reply
        self.media = media or []
        self.calls: list[tuple[str, str]] = []

    async def process_direct(
        self,
        content: str,
        *,
        session_key: str,
        channel: str,
        chat_id: str,
        on_progress=None,
        on_stream=None,
        on_stream_end=None,
    ):
        self.calls.append((content, session_key))
        if on_progress is not None:
            await on_progress("thinking", tool_hint=False)
        if on_stream is not None:
            for chunk in ("Hel", "lo"):
                await on_stream(chunk)
        return SimpleNamespace(content=self.reply, media=list(self.media))


class _FakeCron:
    """Records add_job calls for the schedule-form tests."""

    def __init__(self) -> None:
        self.jobs: list[dict] = []

    def add_job(self, **kwargs):
        self.jobs.append(kwargs)
        return SimpleNamespace(id="job1")


def _make_app(
    tmp_path: Path,
    *,
    webui_enabled: bool,
    admin_enabled: bool = True,
    agent=None,
    broadcaster=None,
    cron_service=None,
):
    config_path = tmp_path / "config.json"
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    config = Config()
    config.gateway.admin.enabled = admin_enabled
    config.gateway.admin.auth_key = AUTH_KEY if admin_enabled else ""
    config.gateway.webui.enabled = webui_enabled
    save_config(config, config_path)
    return create_http_app(
        config_path=config_path,
        workspace=workspace,
        agent=agent,
        session_manager=SessionManager(workspace),
        webui_broadcaster=broadcaster,
        webui_cron_service=cron_service,
    )


def _auth_cookies() -> dict[str, str]:
    return {"hahobot_admin_session": _build_session_cookie(AUTH_KEY), "hahobot_admin_lang": "en"}


def _set_checkpoint(tmp_path: Path, session_key: str, cp: dict) -> None:
    sm = SessionManager(tmp_path / "workspace")
    session = sm.get_or_create(session_key)
    session.metadata["working_checkpoint"] = cp
    sm.save(session)


def _make_app_with_groq(tmp_path: Path):
    config_path = tmp_path / "config.json"
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    config = Config()
    config.gateway.admin.enabled = True
    config.gateway.admin.auth_key = AUTH_KEY
    config.gateway.webui.enabled = True
    config.providers.groq.api_key = "groq-key"
    save_config(config, config_path)
    return create_http_app(
        config_path=config_path,
        workspace=workspace,
        agent=_StreamingAgent(),
        session_manager=SessionManager(workspace),
    )


@pytest_asyncio.fixture
async def client_factory():
    clients: list[TestClient] = []

    async def _make(app):
        client = TestClient(TestServer(app))
        await client.start_server()
        clients.append(client)
        return client

    try:
        yield _make
    finally:
        for client in clients:
            await client.close()


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_webui_disabled_returns_404(tmp_path: Path, client_factory) -> None:
    app = _make_app(tmp_path, webui_enabled=False)
    client = await client_factory(app)
    resp = await client.get("/app", cookies=_auth_cookies())
    assert resp.status == 404


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_webui_requires_admin_enabled(tmp_path: Path, client_factory) -> None:
    # WebUI on but admin off → not reachable (auth + settings live in admin).
    app = _make_app(tmp_path, webui_enabled=True, admin_enabled=False)
    client = await client_factory(app)
    resp = await client.get("/app")
    assert resp.status == 404


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_webui_unauthenticated_redirects_to_login(tmp_path: Path, client_factory) -> None:
    app = _make_app(tmp_path, webui_enabled=True)
    client = await client_factory(app)
    resp = await client.get("/app", allow_redirects=False)
    assert resp.status == 302
    assert resp.headers["Location"].startswith("/admin/login")
    assert "next=/app" in resp.headers["Location"]


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_webui_authenticated_renders_shell(tmp_path: Path, client_factory) -> None:
    app = _make_app(tmp_path, webui_enabled=True)
    client = await client_factory(app)
    resp = await client.get("/app", cookies=_auth_cookies())
    assert resp.status == 200
    body = await resp.text()
    assert 'data-session="webui:default"' in body
    assert "/app/ws" in body
    assert "Send" in body  # composer send label (en locale)


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_webui_settings_requires_auth(tmp_path: Path, client_factory) -> None:
    app = _make_app(tmp_path, webui_enabled=True)
    client = await client_factory(app)
    resp = await client.get("/app/settings", allow_redirects=False)
    assert resp.status == 302
    assert resp.headers["Location"].startswith("/admin/login")


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_webui_settings_disabled_returns_404(tmp_path: Path, client_factory) -> None:
    app = _make_app(tmp_path, webui_enabled=False)
    client = await client_factory(app)
    resp = await client.get("/app/settings", cookies=_auth_cookies())
    assert resp.status == 404


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_webui_settings_renders_panels_and_links(tmp_path: Path, client_factory) -> None:
    agent = _StreamingAgent()
    agent.model = "openai/gpt-test"
    agent._last_usage = {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20}
    app = _make_app(tmp_path, webui_enabled=True, agent=agent)
    client = await client_factory(app)
    resp = await client.get("/app/settings", cookies=_auth_cookies())
    assert resp.status == 200
    body = await resp.text()
    # runtime + token usage panels
    assert "openai/gpt-test" in body
    assert "20" in body  # total tokens
    # admin section cards are linked (folded into Settings)
    for href in ("/admin/config", "/admin/personas", "/admin/skills", "/admin/cron"):
        assert f'href="{href}"' in body
    # memory-layer panel labels
    assert "PROFILE.md" in body and "INSIGHTS.md" in body


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_webui_settings_usage_empty(tmp_path: Path, client_factory) -> None:
    app = _make_app(tmp_path, webui_enabled=True, agent=_StreamingAgent())
    client = await client_factory(app)
    resp = await client.get("/app/settings", cookies=_auth_cookies())
    assert resp.status == 200
    body = await resp.text()
    assert "No usage recorded yet." in body


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_webui_new_session_redirects_with_webui_key(tmp_path: Path, client_factory) -> None:
    app = _make_app(tmp_path, webui_enabled=True)
    client = await client_factory(app)
    resp = await client.post(
        "/app/session/new",
        data={"name": "My Chat"},
        cookies=_auth_cookies(),
        allow_redirects=False,
    )
    assert resp.status == 302
    assert resp.headers["Location"].startswith("/app?session=webui")
    assert "My-Chat" in resp.headers["Location"]


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_webui_chat_ws_streams(tmp_path: Path, client_factory) -> None:
    agent = _StreamingAgent(reply="Hello")
    app = _make_app(tmp_path, webui_enabled=True, agent=agent)
    client = await client_factory(app)

    ws = await client.ws_connect("/app/ws", headers={"Cookie": _cookie_header()})
    ready = await ws.receive_json()
    assert ready["event"] == "ready"

    await ws.send_json({"event": "message", "text": "hi", "session": "webui:default"})

    events: list[dict] = []
    while True:
        frame = await ws.receive_json()
        events.append(frame)
        if frame["event"] == "stream_end":
            break

    await ws.close()

    kinds = [e["event"] for e in events]
    assert "delta" in kinds
    assert kinds[-1] == "stream_end"
    streamed = "".join(e.get("text", "") for e in events if e["event"] == "delta")
    assert streamed == "Hello"
    assert events[-1]["text"] == "Hello"
    assert agent.calls == [("hi", "webui:default")]


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_webui_chat_ws_rejects_unauthenticated(tmp_path: Path, client_factory) -> None:
    agent = _StreamingAgent()
    app = _make_app(tmp_path, webui_enabled=True, agent=agent)
    client = await client_factory(app)
    with pytest.raises(WSServerHandshakeError) as exc:
        await client.ws_connect("/app/ws")
    assert exc.value.status == 401


def _cookie_header() -> str:
    return "; ".join(f"{k}={v}" for k, v in _auth_cookies().items())


# --- Phase 4a: working-checkpoint panel -----------------------------------


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_webui_checkpoint_panel_renders(tmp_path: Path, client_factory) -> None:
    app = _make_app(tmp_path, webui_enabled=True, agent=_StreamingAgent())
    _set_checkpoint(
        tmp_path,
        "webui:default",
        {"goal": "Ship the feature", "current_step": "Writing tests", "next_step": "Run CI"},
    )
    client = await client_factory(app)
    resp = await client.get("/app", cookies=_auth_cookies())
    body = await resp.text()
    assert 'id="checkpoint-panel"' in body
    assert "Ship the feature" in body
    assert "Writing tests" in body
    assert "Run CI" in body


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_webui_checkpoint_panel_hidden_without_checkpoint(
    tmp_path: Path, client_factory
) -> None:
    app = _make_app(tmp_path, webui_enabled=True, agent=_StreamingAgent())
    client = await client_factory(app)
    resp = await client.get("/app", cookies=_auth_cookies())
    body = await resp.text()
    # panel element exists but is hidden when there is no checkpoint
    assert 'id="checkpoint-panel"' in body
    assert "hidden" in body.split('id="checkpoint-panel"', 1)[1][:80]


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_webui_ws_stream_end_includes_checkpoint(tmp_path: Path, client_factory) -> None:
    app = _make_app(tmp_path, webui_enabled=True, agent=_StreamingAgent())
    _set_checkpoint(tmp_path, "webui:default", {"goal": "G", "next_step": "N"})
    client = await client_factory(app)
    ws = await client.ws_connect("/app/ws", headers={"Cookie": _cookie_header()})
    await ws.receive_json()  # ready
    await ws.send_json({"event": "message", "text": "hi", "session": "webui:default"})
    end = None
    while True:
        frame = await ws.receive_json()
        if frame["event"] == "stream_end":
            end = frame
            break
    await ws.close()
    assert end["checkpoint"]["goal"] == "G"
    assert end["checkpoint"]["next_step"] == "N"


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_webui_ws_emits_live_checkpoint_during_turn(tmp_path: Path, client_factory) -> None:
    # The panel must refresh mid-turn (on progress) rather than only at stream_end.
    app = _make_app(tmp_path, webui_enabled=True, agent=_StreamingAgent())
    _set_checkpoint(tmp_path, "webui:default", {"goal": "G", "current_step": "working"})
    client = await client_factory(app)
    ws = await client.ws_connect("/app/ws", headers={"Cookie": _cookie_header()})
    await ws.receive_json()  # ready
    await ws.send_json({"event": "message", "text": "hi", "session": "webui:default"})
    events = []
    while True:
        frame = await ws.receive_json()
        events.append(frame)
        if frame["event"] == "stream_end":
            break
    await ws.close()
    kinds = [e["event"] for e in events]
    # a live checkpoint frame arrived before the turn ended
    assert "checkpoint" in kinds
    assert kinds.index("checkpoint") < kinds.index("stream_end")
    live = next(e for e in events if e["event"] == "checkpoint")
    assert live["checkpoint"]["goal"] == "G"


# --- Phase 4c: session forking --------------------------------------------


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_webui_fork_copies_conversation(tmp_path: Path, client_factory) -> None:
    # seed a source session with messages + persona metadata
    sm = SessionManager(tmp_path / "workspace")
    src = sm.get_or_create("webui:default")
    src.add_message("user", "hello")
    src.add_message("assistant", "hi there")
    src.metadata["persona"] = "Aria"
    sm.save(src)

    app = _make_app(tmp_path, webui_enabled=True, agent=_StreamingAgent())
    client = await client_factory(app)
    resp = await client.post(
        "/app/session/fork",
        data={"session": "webui:default"},
        cookies=_auth_cookies(),
        allow_redirects=False,
    )
    assert resp.status == 302
    location = resp.headers["Location"]
    assert location.startswith(
        ("/app?session=webui%3Adefault-fork-", "/app?session=webui:default-fork-")
    )

    # the forked session on disk carries a copy of the messages + persona
    fork_key = location.split("session=", 1)[1].replace("%3A", ":")
    reader = SessionManager(tmp_path / "workspace")
    fork = reader.get_or_create(fork_key)
    roles = [m["role"] for m in fork.messages]
    assert roles == ["user", "assistant"]
    assert fork.metadata.get("persona") == "Aria"
    # source is untouched
    assert len(reader.get_or_create("webui:default").messages) == 2


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_webui_chat_renders_fork_button(tmp_path: Path, client_factory) -> None:
    app = _make_app(tmp_path, webui_enabled=True, agent=_StreamingAgent())
    client = await client_factory(app)
    resp = await client.get("/app", cookies=_auth_cookies())
    body = await resp.text()
    assert 'action="/app/session/fork"' in body
    assert 'id="scroll-latest"' in body


# --- Phase 4b: voice input (transcription) --------------------------------


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_webui_transcribe_not_configured(tmp_path: Path, client_factory) -> None:
    app = _make_app(tmp_path, webui_enabled=True, agent=_StreamingAgent())
    client = await client_factory(app)
    from aiohttp import FormData

    form = FormData()
    form.add_field("audio", b"fakeaudio", filename="v.webm", content_type="audio/webm")
    resp = await client.post("/app/transcribe", data=form, cookies=_auth_cookies())
    assert resp.status == 503
    body = await resp.json()
    assert body["error"] == "transcription_not_configured"


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_webui_transcribe_success(tmp_path: Path, client_factory, monkeypatch) -> None:
    async def _fake_transcribe(self, file_path):
        return "hello from voice"

    monkeypatch.setattr(
        "hahobot.providers.transcription.GroqTranscriptionProvider.transcribe",
        _fake_transcribe,
    )
    app = _make_app_with_groq(tmp_path)
    client = await client_factory(app)
    from aiohttp import FormData

    form = FormData()
    form.add_field("audio", b"fakeaudio", filename="v.webm", content_type="audio/webm")
    resp = await client.post("/app/transcribe", data=form, cookies=_auth_cookies())
    assert resp.status == 200
    body = await resp.json()
    assert body["text"] == "hello from voice"


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_webui_transcribe_requires_auth(tmp_path: Path, client_factory) -> None:
    app = _make_app(tmp_path, webui_enabled=True, agent=_StreamingAgent())
    client = await client_factory(app)
    resp = await client.post("/app/transcribe", data={"x": "1"}, allow_redirects=False)
    assert resp.status == 302
    assert resp.headers["Location"].startswith("/admin/login")


# --- admin chrome unified with the WebUI topbar ---------------------------


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_admin_shows_webui_topbar_when_enabled(tmp_path: Path, client_factory) -> None:
    app = _make_app(tmp_path, webui_enabled=True)
    client = await client_factory(app)
    resp = await client.get("/admin", cookies=_auth_cookies())
    assert resp.status == 200
    body = await resp.text()
    assert '<div class="webui-topbar">' in body
    assert 'href="/app"' in body
    assert 'href="/app/settings"' in body


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_admin_no_topbar_when_webui_disabled(tmp_path: Path, client_factory) -> None:
    app = _make_app(tmp_path, webui_enabled=False, admin_enabled=True)
    client = await client_factory(app)
    resp = await client.get("/admin", cookies=_auth_cookies())
    assert resp.status == 200
    body = await resp.text()
    assert '<div class="webui-topbar">' not in body


# --- Phase 3: persona selector + inline media -----------------------------


def test_media_url_for_maps_and_rejects(tmp_path: Path) -> None:
    from hahobot.gateway.webui.app import _media_url_for

    root = tmp_path / "workspace" / "out"
    root.mkdir(parents=True)
    inside = root / "image_gen" / "pic.png"
    inside.parent.mkdir(parents=True)
    inside.write_bytes(b"x")

    assert _media_url_for(str(inside), root) == "/app/media/image_gen/pic.png"
    # remote URLs pass through
    assert _media_url_for("https://cdn.example.com/x.png", root) == "https://cdn.example.com/x.png"
    # files outside workspace/out are rejected
    outside = tmp_path / "workspace" / "secret.txt"
    outside.write_text("nope")
    assert _media_url_for(str(outside), root) is None
    assert _media_url_for("", root) is None


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_webui_chat_renders_persona_selector(tmp_path: Path, client_factory) -> None:
    app = _make_app(tmp_path, webui_enabled=True, agent=_StreamingAgent())
    client = await client_factory(app)
    resp = await client.get("/app", cookies=_auth_cookies())
    body = await resp.text()
    assert 'id="persona-select"' in body
    assert "<option" in body


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_webui_ws_stream_end_includes_media(tmp_path: Path, client_factory) -> None:
    workspace = tmp_path / "workspace"
    media_file = workspace / "out" / "image_gen" / "pic.png"
    media_file.parent.mkdir(parents=True)
    media_file.write_bytes(b"img")
    agent = _StreamingAgent(reply="here", media=[str(media_file)])
    app = _make_app(tmp_path, webui_enabled=True, agent=agent)
    client = await client_factory(app)

    ws = await client.ws_connect("/app/ws", headers={"Cookie": _cookie_header()})
    await ws.receive_json()  # ready
    await ws.send_json({"event": "message", "text": "draw", "session": "webui:default"})
    end = None
    while True:
        frame = await ws.receive_json()
        if frame["event"] == "stream_end":
            end = frame
            break
    await ws.close()
    assert end["media"] == ["/app/media/image_gen/pic.png"]


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_webui_media_serves_and_guards(tmp_path: Path, client_factory) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "out").mkdir(parents=True)
    (workspace / "out" / "pic.png").write_bytes(b"imgdata")
    (workspace / "secret.txt").write_text("top secret")
    app = _make_app(tmp_path, webui_enabled=True, agent=_StreamingAgent())
    client = await client_factory(app)

    ok = await client.get("/app/media/pic.png", cookies=_auth_cookies())
    assert ok.status == 200
    assert await ok.read() == b"imgdata"

    missing = await client.get("/app/media/nope.png", cookies=_auth_cookies())
    assert missing.status == 404

    # path traversal outside workspace/out is rejected
    traversal = await client.get("/app/media/../secret.txt", cookies=_auth_cookies())
    assert traversal.status == 404

    unauth = await client.get("/app/media/pic.png")
    assert unauth.status == 401


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_webui_media_disabled_returns_404(tmp_path: Path, client_factory) -> None:
    app = _make_app(tmp_path, webui_enabled=False, agent=_StreamingAgent())
    client = await client_factory(app)
    resp = await client.get("/app/media/pic.png", cookies=_auth_cookies())
    assert resp.status == 404


# --- WebUI proactive push (broadcaster + webui pseudo-channel) -------------


@pytest.mark.asyncio
async def test_broadcaster_register_and_scope() -> None:
    from hahobot.gateway.webui.broadcast import WebUIBroadcaster, WebUIConnection

    b = WebUIBroadcaster()
    a1 = WebUIConnection("webui:default")
    a2 = WebUIConnection("webui:default")
    other = WebUIConnection("webui:other")
    b.register(a1)
    b.register(a2)
    b.register(other)
    assert b.connection_count("webui:default") == 2

    n = await b.broadcast("webui:default", {"event": "push", "text": "hi"})
    assert n == 2
    assert a1.queue.get_nowait()["text"] == "hi"
    assert a2.queue.get_nowait()["text"] == "hi"
    assert other.queue.empty()  # scoped to the target session only

    b.unregister(a1)
    assert b.connection_count("webui:default") == 1
    # broadcasting to a session with no connections is a no-op
    assert await b.broadcast("webui:nobody", {"event": "push"}) == 0


@pytest.mark.asyncio
async def test_webui_channel_send_maps_frame(tmp_path: Path) -> None:
    from hahobot.bus.events import OutboundMessage
    from hahobot.gateway.webui.broadcast import WebUIBroadcaster, WebUIConnection
    from hahobot.gateway.webui.channel import WebUIChannel

    workspace = tmp_path / "workspace"
    (workspace / "out").mkdir(parents=True)
    media_file = workspace / "out" / "pic.png"
    media_file.write_bytes(b"x")

    b = WebUIBroadcaster()
    conn = WebUIConnection("webui:default")
    b.register(conn)
    ch = WebUIChannel(b, bus=None, workspace=workspace)
    await ch.send(
        OutboundMessage(
            channel="webui", chat_id="default", content="reminder", media=[str(media_file)]
        )
    )
    frame = conn.queue.get_nowait()
    assert frame["event"] == "push"
    assert frame["text"] == "reminder"
    assert frame["media"] == ["/app/media/pic.png"]
    # No session manager wired → no checkpoint attached (must not raise).
    assert frame["checkpoint"] is None


@pytest.mark.asyncio
async def test_webui_channel_push_carries_checkpoint(tmp_path: Path) -> None:
    from hahobot.bus.events import OutboundMessage
    from hahobot.gateway.webui.broadcast import WebUIBroadcaster, WebUIConnection
    from hahobot.gateway.webui.channel import WebUIChannel

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    sm = SessionManager(workspace)
    session = sm.get_or_create("webui:default")
    session.metadata["working_checkpoint"] = {"goal": "cron goal", "current_step": "reminding"}
    sm.save(session)

    b = WebUIBroadcaster()
    conn = WebUIConnection("webui:default")
    b.register(conn)
    ch = WebUIChannel(b, bus=None, workspace=workspace, session_manager=sm)
    await ch.send(OutboundMessage(channel="webui", chat_id="default", content="ping"))
    frame = conn.queue.get_nowait()
    assert frame["event"] == "push"
    assert frame["checkpoint"]["goal"] == "cron goal"


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_webui_ws_receives_proactive_push(tmp_path: Path, client_factory) -> None:
    from hahobot.bus.events import OutboundMessage
    from hahobot.gateway.webui.broadcast import WebUIBroadcaster
    from hahobot.gateway.webui.channel import WebUIChannel

    broadcaster = WebUIBroadcaster()
    app = _make_app(tmp_path, webui_enabled=True, agent=_StreamingAgent(), broadcaster=broadcaster)
    client = await client_factory(app)

    ws = await client.ws_connect(
        "/app/ws?session=webui:default", headers={"Cookie": _cookie_header()}
    )
    assert (await ws.receive_json())["event"] == "ready"

    # a proactive delivery routed through the webui pseudo-channel reaches the client
    ch = WebUIChannel(broadcaster, bus=None, workspace=tmp_path / "workspace")
    await ch.send(OutboundMessage(channel="webui", chat_id="default", content="scheduled ping"))

    frame = await ws.receive_json()
    assert frame["event"] == "push"
    assert frame["text"] == "scheduled ping"

    # a push to a different session is not delivered to this client
    await ch.send(OutboundMessage(channel="webui", chat_id="other", content="not for you"))
    await ws.send_json({"event": "message", "text": "hi", "session": "webui:default"})
    seen = []
    while True:
        f = await ws.receive_json()
        seen.append(f)
        if f["event"] == "stream_end":
            break
    await ws.close()
    assert all(f.get("text") != "not for you" for f in seen)


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_webui_push_offline_no_client(tmp_path: Path) -> None:
    # No connected client: broadcast is a no-op and must not raise.
    from hahobot.bus.events import OutboundMessage
    from hahobot.gateway.webui.broadcast import WebUIBroadcaster
    from hahobot.gateway.webui.channel import WebUIChannel

    b = WebUIBroadcaster()
    ch = WebUIChannel(b, bus=None, workspace=tmp_path / "workspace")
    await ch.send(OutboundMessage(channel="webui", chat_id="default", content="ping"))
    assert b.connection_count("webui:default") == 0


@pytest.mark.asyncio
async def test_proactive_push_routes_through_channel_manager(tmp_path: Path) -> None:
    """End-to-end: bus -> ChannelManager._dispatch_outbound -> WebUIChannel -> client."""
    import asyncio
    import contextlib

    from hahobot.bus.events import OutboundMessage
    from hahobot.bus.queue import MessageBus
    from hahobot.channels.manager import ChannelManager
    from hahobot.gateway.webui.broadcast import WebUIBroadcaster, WebUIConnection
    from hahobot.gateway.webui.channel import WebUIChannel

    bus = MessageBus()
    manager = ChannelManager(Config(), bus)
    broadcaster = WebUIBroadcaster()
    conn = WebUIConnection("webui:default")
    broadcaster.register(conn)
    manager.channels["webui"] = WebUIChannel(broadcaster, bus, tmp_path / "workspace")

    task = asyncio.create_task(manager._dispatch_outbound())
    try:
        await bus.publish_outbound(
            OutboundMessage(channel="webui", chat_id="default", content="scheduled ping")
        )
        frame = await asyncio.wait_for(conn.queue.get(), timeout=2.0)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert frame["event"] == "push"
    assert frame["text"] == "scheduled ping"


# --- schedule form (reminder → cron → webui push) -------------------------


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_webui_schedule_form_shown_only_with_cron(tmp_path: Path, client_factory) -> None:
    # no cron service → no form
    app = _make_app(tmp_path, webui_enabled=True, agent=_StreamingAgent())
    client = await client_factory(app)
    body = await (await client.get("/app", cookies=_auth_cookies())).text()
    assert 'action="/app/schedule"' not in body


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_webui_schedule_creates_bound_reminder(tmp_path: Path, client_factory) -> None:
    cron = _FakeCron()
    app = _make_app(tmp_path, webui_enabled=True, agent=_StreamingAgent(), cron_service=cron)
    client = await client_factory(app)

    body = await (await client.get("/app", cookies=_auth_cookies())).text()
    assert 'action="/app/schedule"' in body

    resp = await client.post(
        "/app/schedule",
        data={"session": "webui:default", "delay": "5", "message": "stand up"},
        cookies=_auth_cookies(),
        allow_redirects=False,
    )
    assert resp.status == 302
    assert len(cron.jobs) == 1
    job = cron.jobs[0]
    assert job["channel"] == "webui"
    assert job["to"] == "default"
    assert job["deliver"] is True
    assert job["message"] == "stand up"
    assert job["delete_after_run"] is True
    assert job["schedule"].kind == "at"
    assert job["schedule"].at_ms > 0


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_webui_schedule_ignores_bad_input(tmp_path: Path, client_factory) -> None:
    cron = _FakeCron()
    app = _make_app(tmp_path, webui_enabled=True, agent=_StreamingAgent(), cron_service=cron)
    client = await client_factory(app)
    # empty message / non-positive delay → no job created, still redirects
    for data in (
        {"session": "webui:default", "delay": "5", "message": ""},
        {"session": "webui:default", "delay": "0", "message": "x"},
    ):
        resp = await client.post(
            "/app/schedule", data=data, cookies=_auth_cookies(), allow_redirects=False
        )
        assert resp.status == 302
    assert cron.jobs == []


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_webui_session_delete_removes_and_stays_on_current(
    tmp_path: Path, client_factory
) -> None:
    sm = SessionManager(tmp_path / "workspace")
    for key in ("webui:default", "webui:other"):
        s = sm.get_or_create(key)
        s.add_message("user", "hi")
        sm.save(s)

    app = _make_app(tmp_path, webui_enabled=True, agent=_StreamingAgent())
    client = await client_factory(app)
    resp = await client.post(
        "/app/session/delete",
        data={"session": "webui:other", "current": "webui:default"},
        cookies=_auth_cookies(),
        allow_redirects=False,
    )
    assert resp.status == 302
    # Deleting a non-active conversation keeps you on the current one.
    assert resp.headers["Location"] in (
        "/app?session=webui%3Adefault",
        "/app?session=webui:default",
    )
    reader = SessionManager(tmp_path / "workspace")
    keys = {s["key"] for s in reader.list_sessions()}
    assert "webui:other" not in keys
    assert "webui:default" in keys


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_webui_session_delete_current_falls_back_to_default(
    tmp_path: Path, client_factory
) -> None:
    sm = SessionManager(tmp_path / "workspace")
    s = sm.get_or_create("webui:foo")
    s.add_message("user", "hi")
    sm.save(s)

    app = _make_app(tmp_path, webui_enabled=True, agent=_StreamingAgent())
    client = await client_factory(app)
    resp = await client.post(
        "/app/session/delete",
        data={"session": "webui:foo", "current": "webui:foo"},
        cookies=_auth_cookies(),
        allow_redirects=False,
    )
    assert resp.status == 302
    # Deleting the conversation you are viewing drops back to the default one.
    assert resp.headers["Location"] in (
        "/app?session=webui%3Adefault",
        "/app?session=webui:default",
    )


@pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")
@pytest.mark.asyncio
async def test_webui_shell_renders_delete_and_processing_indicator(
    tmp_path: Path, client_factory
) -> None:
    sm = SessionManager(tmp_path / "workspace")
    seeded = sm.get_or_create("webui:default")
    seeded.add_message("user", "hi")
    sm.save(seeded)

    app = _make_app(tmp_path, webui_enabled=True, agent=_StreamingAgent())
    client = await client_factory(app)
    resp = await client.get("/app", cookies=_auth_cookies())
    assert resp.status == 200
    body = await resp.text()
    # per-conversation delete control
    assert 'action="/app/session/delete"' in body
    assert "session-delete-btn" in body
    assert "Delete conversation" in body
    # processing / thinking indicator wiring
    assert "showThinking" in body
    assert 'class="typing"' in body
    # tojson unicode-escapes the ellipsis, so match the ASCII prefix.
    assert "Processing" in body
