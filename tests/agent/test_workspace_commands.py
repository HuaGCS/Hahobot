from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from hahobot.bus.events import InboundMessage
from hahobot.bus.queue import MessageBus
from hahobot.providers.base import LLMResponse, ToolCallRequest
from hahobot.session.manager import SessionManager


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def _init_repo(workspace: Path) -> None:
    workspace.mkdir(parents=True, exist_ok=True)
    _git(workspace, "init", "-b", "main")
    _git(workspace, "config", "user.name", "Test User")
    _git(workspace, "config", "user.email", "test@example.com")
    (workspace / "tracked.txt").write_text("one\n", encoding="utf-8")
    _git(workspace, "add", "tracked.txt")
    _git(workspace, "commit", "-m", "init")


class _FakeWorkspaceProvider:
    def __init__(self) -> None:
        self.generation = SimpleNamespace(max_tokens=256, temperature=0.7, reasoning_effort=None)
        self.review_calls: list[dict[str, object]] = []
        self.compaction_calls = 0

    def get_default_model(self) -> str:
        return "openai/gpt-4.1-mini"

    def estimate_prompt_tokens(self, messages, _tools, _model):
        return len(messages) * 1200, "fake-counter"

    async def chat_with_retry(self, **kwargs):
        tools = kwargs.get("tools")
        if tools:
            self.compaction_calls += 1
            return LLMResponse(
                content=None,
                finish_reason="tool_calls",
                tool_calls=[
                    ToolCallRequest(
                        id=f"save-memory-{self.compaction_calls}",
                        name="save_memory",
                        arguments={
                            "history_entry": f"summary {self.compaction_calls}",
                            "memory_update": "",
                        },
                    )
                ],
            )
        self.review_calls.append(kwargs)
        return LLMResponse(content="Findings:\n- [medium] `tracked.txt`: missing regression test.")


def _make_loop(workspace: Path):
    from hahobot.agent.loop import AgentLoop

    provider = _FakeWorkspaceProvider()
    manager = SessionManager(workspace)
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=workspace,
        model="openai/gpt-4.1-mini",
        context_window_tokens=4096,
        session_manager=manager,
    )
    return loop, manager, provider


def _save_session(workspace: Path, key: str, *messages: tuple[str, str]) -> None:
    manager = SessionManager(workspace)
    session = manager.get_or_create(key)
    for role, content in messages:
        session.add_message(role, content)
    manager.save(session)


@pytest.mark.asyncio
async def test_gateway_session_commands_can_route_chat_and_reset(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _save_session(workspace, "telegram:42", ("user", "hello base"))
    loop, manager, _provider = _make_loop(workspace)

    try:
        response = await loop._process_message(
            InboundMessage(
                channel="telegram",
                sender_id="u1",
                chat_id="42",
                content="/session new alpha",
            )
        )
        assert response is not None
        assert "Started new session: telegram:42:session:alpha" in response.content

        routed = loop._normalize_session_message(
            InboundMessage(
                channel="telegram",
                sender_id="u1",
                chat_id="42",
                content="hello again",
            )
        )
        assert routed.session_key == "telegram:42:session:alpha"

        current = await loop._process_message(
            InboundMessage(
                channel="telegram",
                sender_id="u1",
                chat_id="42",
                content="/session current",
            )
        )
        assert current is not None
        assert "Current session: telegram:42:session:alpha" in current.content
        assert "Origin chat session: telegram:42" in current.content

        exported = await loop._process_message(
            InboundMessage(
                channel="telegram",
                sender_id="u1",
                chat_id="42",
                content="/session export alpha",
            )
        )
        assert exported is not None
        assert "Exported session: telegram:42:session:alpha" in exported.content
        assert (workspace / "out" / "sessions" / "telegram_42_session_alpha.md").exists()

        reset = await loop._process_message(
            InboundMessage(
                channel="telegram",
                sender_id="u1",
                chat_id="42",
                content="/session use default",
            )
        )
        assert reset is not None
        assert "Switched to default session: telegram:42" in reset.content
        assert loop._normalize_session_message(
            InboundMessage(
                channel="telegram",
                sender_id="u1",
                chat_id="42",
                content="back to default",
            )
        ).session_key == "telegram:42"
    finally:
        await loop.close_mcp()


@pytest.mark.asyncio
async def test_gateway_repo_review_and_compact_commands(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _init_repo(workspace)
    (workspace / "tracked.txt").write_text("one\ntwo\n", encoding="utf-8")
    _git(workspace, "add", "tracked.txt")
    _save_session(
        workspace,
        "telegram:42",
        ("user", "A" * 200),
        ("assistant", "B" * 200),
        ("user", "C" * 200),
        ("assistant", "D" * 200),
        ("user", "E" * 200),
        ("assistant", "F" * 200),
    )

    loop, manager, provider = _make_loop(workspace)

    try:
        repo_status = await loop._process_message(
            InboundMessage(
                channel="telegram",
                sender_id="u1",
                chat_id="42",
                content="/repo status",
            )
        )
        assert repo_status is not None
        assert "Git repo: yes" in repo_status.content

        review = await loop._process_message(
            InboundMessage(
                channel="telegram",
                sender_id="u1",
                chat_id="42",
                content="/review staged",
            )
        )
        assert review is not None
        assert "Findings:" in review.content
        assert provider.review_calls

        compact = await loop._process_message(
            InboundMessage(
                channel="telegram",
                sender_id="u1",
                chat_id="42",
                content="/compact",
            )
        )
        assert compact is not None
        assert "Compaction completed." in compact.content
        assert manager.get_or_create("telegram:42").last_consolidated > 0
        history_file = workspace / "memory" / "history.jsonl"
        assert "summary" in history_file.read_text(encoding="utf-8")
    finally:
        await loop.close_mcp()
