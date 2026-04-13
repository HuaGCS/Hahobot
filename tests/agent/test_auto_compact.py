from __future__ import annotations

import copy
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from hahobot.agent.autocompact import AutoCompact
from hahobot.agent.loop import AgentLoop
from hahobot.bus.queue import MessageBus
from hahobot.config.schema import AgentDefaults
from hahobot.providers.base import GenerationSettings, LLMResponse
from hahobot.session.manager import SessionManager


class _FakeConsolidator:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str], str]] = []

    async def archive_messages(
        self,
        session,
        messages,
        *,
        source: str = "session_archive",
        on_archive=None,
    ) -> bool:
        self.calls.append((session.key, [message["content"] for message in messages], source))
        if on_archive is not None:
            on_archive(
                {
                    "history_entry": "summary one",
                    "memory_update": "",
                    "raw_archive": False,
                }
            )
        return True


def _make_loop(tmp_path) -> AgentLoop:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.generation = GenerationSettings(max_tokens=0)
    provider.estimate_prompt_tokens.return_value = (64, "test-counter")
    response = LLMResponse(content="ok", tool_calls=[])
    provider.chat_with_retry = AsyncMock(return_value=response)
    provider.chat_stream_with_retry = AsyncMock(return_value=response)
    return AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="test-model",
        context_window_tokens=4096,
        session_ttl_minutes=15,
    )


def test_agent_defaults_accepts_idle_compact_aliases() -> None:
    defaults = AgentDefaults.model_validate({"idleCompactAfterMinutes": 30})
    assert defaults.session_ttl_minutes == 30

    legacy = AgentDefaults.model_validate({"sessionTtlMinutes": 12})
    assert legacy.session_ttl_minutes == 12

    dumped = AgentDefaults(session_ttl_minutes=45).model_dump(by_alias=True)
    assert dumped["idleCompactAfterMinutes"] == 45


@pytest.mark.asyncio
async def test_auto_compact_archives_idle_prefix_and_exposes_resume_summary(tmp_path) -> None:
    manager = SessionManager(tmp_path)
    session = manager.get_or_create("cli:test")
    for idx in range(1, 7):
        session.add_message("user", f"u{idx}")
        session.add_message("assistant", f"a{idx}")
    session.updated_at = datetime.now() - timedelta(minutes=20)
    manager.save(session)

    consolidator = _FakeConsolidator()
    auto = AutoCompact(manager, consolidator, session_ttl_minutes=15)

    await auto._archive("cli:test")

    reloaded = manager.get_or_create("cli:test")
    assert consolidator.calls == [
        ("cli:test", ["u1", "a1", "u2", "a2"], "idle_auto_compact")
    ]
    assert [message["content"] for message in reloaded.messages] == [
        "u3",
        "a3",
        "u4",
        "a4",
        "u5",
        "a5",
        "u6",
        "a6",
    ]
    assert reloaded.last_consolidated == 0

    prepared, summary = auto.prepare_session(reloaded, "cli:test")
    assert prepared is reloaded
    assert summary is not None
    assert "Previous conversation summary: summary one" in summary
    assert "_last_summary" not in prepared.metadata


@pytest.mark.asyncio
async def test_process_direct_injects_resume_summary_from_auto_compact_metadata(tmp_path) -> None:
    loop = _make_loop(tmp_path)
    try:
        captured: dict[str, object] = {}

        async def _chat_with_retry(**kwargs):
            captured["messages"] = copy.deepcopy(kwargs["messages"])
            return LLMResponse(content="ok", tool_calls=[])

        loop.provider.chat_with_retry = AsyncMock(side_effect=_chat_with_retry)
        loop.provider.chat_stream_with_retry = AsyncMock(side_effect=_chat_with_retry)
        session = loop.sessions.get_or_create("cli:test")
        session.metadata["_last_summary"] = {
            "text": "summary one",
            "last_active": (datetime.now() - timedelta(minutes=20)).isoformat(),
        }
        loop.sessions.save(session)

        await loop.process_direct("hello", session_key="cli:test")

        messages = captured["messages"]
        assert isinstance(messages, list)
        user_message = messages[-1]["content"]
        assert "[Resumed Session]" in user_message
        assert "Previous conversation summary: summary one" in user_message
    finally:
        await loop.close_mcp()
