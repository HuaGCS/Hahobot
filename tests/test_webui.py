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


def _make_app(tmp_path: Path, *, webui_enabled: bool, admin_enabled: bool = True, agent=None):
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
