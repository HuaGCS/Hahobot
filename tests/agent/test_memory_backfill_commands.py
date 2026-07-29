"""Chat-command coverage for the guarded local-memory to Mem0 backfill."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hahobot.bus.events import InboundMessage
from hahobot.config.schema import MemoryConfig, SharedMemoryConfig


def _memory_config(**overrides) -> MemoryConfig:
    values = {
        "enabled": True,
        "baseUrl": "https://mem0.example.test",
        "apiKey": "must-not-leak",
        "userId": "shared-user",
        "personaEnabled": True,
        "globalWriteMode": "full",
    }
    values.update(overrides)
    return MemoryConfig(
        shared=SharedMemoryConfig.model_validate(values),
    )


def _make_loop(workspace: Path, *, memory_config: MemoryConfig | None = None):
    from hahobot.agent.loop import AgentLoop
    from hahobot.bus.queue import MessageBus

    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    config_path = workspace.parent / "instance" / "config.json"
    with patch("hahobot.agent.loop.SubagentManager"):
        return AgentLoop(
            bus=MessageBus(),
            provider=provider,
            workspace=workspace,
            config_path=config_path,
            memory_config=memory_config or MemoryConfig(),
        )


def _message(content: str, *, sender: str = "owner") -> InboundMessage:
    return InboundMessage(
        channel="cli",
        sender_id=sender,
        chat_id="direct",
        content=content,
    )


def _confirmation_token(content: str) -> str:
    match = re.search(r"/memory backfill confirm ([A-Za-z0-9_-]+)", content)
    assert match is not None, content
    return match.group(1)


@pytest.mark.asyncio
async def test_memory_command_is_hidden_and_rejected_when_shared_writes_are_disabled(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    loop = _make_loop(workspace)

    help_response = await loop._process_message(_message("/help"))
    command_response = await loop._process_message(_message("/memory backfill preview"))

    assert help_response is not None
    assert "/memory backfill" not in help_response.content
    assert command_response is not None
    assert "unavailable" in command_response.content.lower()


@pytest.mark.asyncio
async def test_preview_is_sanitized_read_only_and_persists_only_a_hashed_token(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "PROFILE.md").write_text(
        "public-memory-needle\n\n<private>ephemeral-secret-needle</private>",
        encoding="utf-8",
    )
    loop = _make_loop(workspace, memory_config=_memory_config())

    help_response = await loop._process_message(_message("/help"))
    with patch(
        "hahobot.agent.commands.memory.execute_shared_memory_backfill",
        new_callable=AsyncMock,
    ) as execute:
        response = await loop._process_message(_message("/memory backfill preview"))

    assert response is not None
    assert help_response is not None
    assert "/memory backfill" in help_response.content
    token = _confirmation_token(response.content)
    assert "public-memory-needle" not in response.content
    assert "ephemeral-secret-needle" not in response.content
    assert "must-not-leak" not in response.content
    assert "cannot be reconstructed" in response.content
    execute.assert_not_awaited()

    session = loop.sessions.get_or_create("cli:direct")
    record = session.metadata["_shared_memory_backfill_confirmation"]
    assert record["token_sha256"] != token
    assert token not in repr(record)
    assert record["personas"] == ["default"]
    assert record["session_key"] == "cli:direct"
    assert record["sender_id"] == "owner"
    assert "public-memory-needle" not in repr(record)
    assert "ephemeral-secret-needle" not in repr(record)
    assert "must-not-leak" not in repr(record)
    assert not (loop._shared_memory_state_root()).exists()


@pytest.mark.asyncio
async def test_confirm_rebuilds_exact_plan_consumes_token_and_executes_once(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "PROFILE.md").write_text("stable public fact", encoding="utf-8")
    loop = _make_loop(workspace, memory_config=_memory_config())

    preview = await loop._process_message(_message("/memory backfill preview"))
    assert preview is not None
    token = _confirmation_token(preview.content)
    seen = {}

    async def fake_execute(plan, config, *, state_root, force):
        seen.update(
            plan=plan,
            config=config,
            state_root=state_root,
            force=force,
        )
        return "complete", {item.event_id: "delivered" for item in plan.items}

    with patch(
        "hahobot.agent.commands.memory.execute_shared_memory_backfill",
        side_effect=fake_execute,
    ) as execute:
        confirmed = await loop._process_message(_message(f"/memory backfill confirm {token}"))
        replayed = await loop._process_message(_message(f"/memory backfill confirm {token}"))

    assert confirmed is not None
    assert "complete" in confirmed.content.lower()
    assert replayed is not None
    assert "invalid" in replayed.content.lower()
    assert execute.await_count == 1
    assert seen["config"] is loop.memory_config.shared
    assert seen["state_root"] == loop._shared_memory_state_root()
    assert seen["force"] is False
    assert (
        "_shared_memory_backfill_confirmation"
        not in loop.sessions.get_or_create("cli:direct").metadata
    )


@pytest.mark.asyncio
async def test_concurrent_confirms_execute_one_preview_only(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "PROFILE.md").write_text("stable public fact", encoding="utf-8")
    loop = _make_loop(workspace, memory_config=_memory_config())

    preview = await loop._process_message(_message("/memory backfill preview"))
    assert preview is not None
    token = _confirmation_token(preview.content)

    async def fake_execute(plan, config, *, state_root, force):
        await asyncio.sleep(0)
        return "complete", {item.event_id: "delivered" for item in plan.items}

    with patch(
        "hahobot.agent.commands.memory.execute_shared_memory_backfill",
        side_effect=fake_execute,
    ) as execute:
        responses = await asyncio.gather(
            loop._process_message(_message(f"/memory backfill confirm {token}")),
            loop._process_message(_message(f"/memory backfill confirm {token}")),
        )

    assert execute.await_count == 1
    assert sum("complete" in response.content.lower() for response in responses) == 1
    assert sum("invalid" in response.content.lower() for response in responses) == 1


@pytest.mark.asyncio
async def test_wrong_sender_does_not_consume_owner_confirmation(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "PROFILE.md").write_text("stable public fact", encoding="utf-8")
    loop = _make_loop(workspace, memory_config=_memory_config())

    preview = await loop._process_message(_message("/memory backfill preview"))
    assert preview is not None
    token = _confirmation_token(preview.content)

    async def fake_execute(plan, config, *, state_root, force):
        return "complete", {item.event_id: "delivered" for item in plan.items}

    with patch(
        "hahobot.agent.commands.memory.execute_shared_memory_backfill",
        side_effect=fake_execute,
    ) as execute:
        denied = await loop._process_message(
            _message(f"/memory backfill confirm {token}", sender="someone-else")
        )
        confirmed = await loop._process_message(
            _message(f"/memory backfill confirm {token}", sender="owner")
        )

    assert denied is not None
    assert "invalid" in denied.content.lower()
    assert confirmed is not None
    assert "complete" in confirmed.content.lower()
    execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_confirmation_rejects_other_sender_expiry_and_changed_plan(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    profile = workspace / "PROFILE.md"
    profile.write_text("first stable fact", encoding="utf-8")
    loop = _make_loop(workspace, memory_config=_memory_config())
    execute = AsyncMock()
    monkeypatch.setattr(
        "hahobot.agent.commands.memory.execute_shared_memory_backfill",
        execute,
    )

    preview = await loop._process_message(_message("/memory backfill preview"))
    assert preview is not None
    token = _confirmation_token(preview.content)
    wrong_sender = await loop._process_message(
        _message(f"/memory backfill confirm {token}", sender="someone-else")
    )
    assert wrong_sender is not None
    assert "invalid" in wrong_sender.content.lower()

    preview = await loop._process_message(_message("/memory backfill preview"))
    assert preview is not None
    token = _confirmation_token(preview.content)
    session = loop.sessions.get_or_create("cli:direct")
    session.metadata["_shared_memory_backfill_confirmation"]["expires_at"] = 0
    expired = await loop._process_message(_message(f"/memory backfill confirm {token}"))
    assert expired is not None
    assert "expired" in expired.content.lower()

    preview = await loop._process_message(_message("/memory backfill preview"))
    assert preview is not None
    token = _confirmation_token(preview.content)
    profile.write_text("changed after preview", encoding="utf-8")
    changed = await loop._process_message(_message(f"/memory backfill confirm {token}"))
    assert changed is not None
    assert "changed" in changed.content.lower()
    execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_preview_can_scope_to_one_persona(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    coder = workspace / "personas" / "Coder"
    coder.mkdir(parents=True)
    (workspace / "PROFILE.md").write_text("root fact", encoding="utf-8")
    (coder / "PROFILE.md").write_text("coder fact", encoding="utf-8")
    loop = _make_loop(workspace, memory_config=_memory_config())

    response = await loop._process_message(_message("/memory backfill preview coder"))

    assert response is not None
    assert "Coder" in response.content
    record = loop.sessions.get_or_create("cli:direct").metadata[
        "_shared_memory_backfill_confirmation"
    ]
    assert record["personas"] == ["Coder"]
