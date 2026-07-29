"""Tests for the one-time local-memory to Mem0 backfill."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx
import pytest

from hahobot.agent.memory_shared_backfill import (
    SharedMemoryBackfillItem,
    build_shared_memory_backfill_plan,
    execute_shared_memory_backfill,
    shared_memory_backfill_plan_fingerprint,
)
from hahobot.config.schema import SharedMemoryConfig


def _config(**overrides: Any) -> SharedMemoryConfig:
    values: dict[str, Any] = {
        "enabled": True,
        "baseUrl": "https://mem0.internal:8888",
        "apiKey": "api-ultra-secret",
        "userId": "shared-human",
        "agentId": "hahobot-tests",
        "projectId": "HuaGCS/Hahobot",
        "deviceId": "pytest",
        "personaEnabled": True,
        "globalWriteMode": "full",
        "snapshotRefreshSeconds": 0,
    }
    values.update(overrides)
    return SharedMemoryConfig.model_validate(values)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _contents(items: list[SharedMemoryBackfillItem], *, layer: str | None = None) -> str:
    selected = items if layer is None else [item for item in items if item.layer == layer]
    return "\n".join(item.content for item in selected)


def test_plan_fingerprint_tracks_content_and_routing_but_not_api_key(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _write(workspace / "PROFILE.md", "stable-profile-fact")
    config = _config()
    plan = build_shared_memory_backfill_plan(workspace, config)
    baseline = shared_memory_backfill_plan_fingerprint(plan, config)

    rotated_key = _config(apiKey="rotated-secret")
    assert shared_memory_backfill_plan_fingerprint(plan, rotated_key) == baseline

    different_service = _config(baseUrl="https://other-mem0.internal:8888")
    assert shared_memory_backfill_plan_fingerprint(plan, different_service) != baseline

    _write(workspace / "PROFILE.md", "changed-profile-fact")
    changed_plan = build_shared_memory_backfill_plan(workspace, config)
    assert shared_memory_backfill_plan_fingerprint(changed_plan, config) != baseline


def test_plan_fingerprint_tracks_outgoing_fragment_metadata(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    memory_file = workspace / "memory" / "MEMORY.md"
    _write(memory_file, "legacy fragment without a structured header")
    config = _config()
    os.utime(memory_file, (1_700_000_000, 1_700_000_000))
    first = build_shared_memory_backfill_plan(workspace, config)

    os.utime(memory_file, (1_700_003_600, 1_700_003_600))
    second = build_shared_memory_backfill_plan(workspace, config)

    assert first.items[0].event_id == second.items[0].event_id
    assert first.items[0].metadata != second.items[0].metadata
    assert shared_memory_backfill_plan_fingerprint(
        first, config
    ) != shared_memory_backfill_plan_fingerprint(second, config)


def test_plan_routes_root_and_custom_memory_to_conservative_namespaces(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    _write(
        workspace / "PROFILE.md",
        """# Stable profile

- public-profile-fact

<persona-private>
- default-profile-private
</persona-private>
""",
    )
    _write(
        workspace / "INSIGHTS.md",
        """# Shared collaboration

- default-insight-private

<persona-private>
- explicitly-default-insight-private
</persona-private>
""",
    )
    _write(
        workspace / "memory" / "MEMORY.md",
        "<!-- ts:2026-07-20 tag:decision src:user -->\ndefault-long-term-memory",
    )
    coder = workspace / "personas" / "Coder"
    _write(coder / "PROFILE.md", "# Coder profile\n\n- coder-profile-private")
    _write(coder / "INSIGHTS.md", "# Coder insights\n\n- coder-insight-private")
    _write(coder / "memory" / "MEMORY.md", "coder-long-term-memory")

    plan = build_shared_memory_backfill_plan(workspace, _config())

    assert plan.selected_personas == ["default", "Coder"]
    public = [item for item in plan.items if item.layer == "public"]
    default_private = [
        item for item in plan.items if item.layer == "persona_private" and item.persona == "default"
    ]
    coder_private = [
        item for item in plan.items if item.layer == "persona_private" and item.persona == "Coder"
    ]

    assert {item.source_file for item in public} == {"PROFILE.md"}
    assert {item.user_id for item in public} == {"shared-human"}
    assert "public-profile-fact" in _contents(public)
    assert "default-insight-private" not in _contents(public)
    assert "explicitly-default-insight-private" not in _contents(public)
    assert "default-profile-private" not in _contents(public)

    assert {item.user_id for item in default_private} == {"shared-human::hahobot-persona::default"}
    assert {item.source_file for item in default_private} == {
        "PROFILE.md",
        "INSIGHTS.md",
        "memory/MEMORY.md",
    }
    assert "default-profile-private" in _contents(default_private)
    assert "default-insight-private" in _contents(default_private)
    assert "explicitly-default-insight-private" in _contents(default_private)
    assert "default-long-term-memory" in _contents(default_private)

    assert {item.user_id for item in coder_private} == {"shared-human::hahobot-persona::coder"}
    assert {item.source_file for item in coder_private} == {
        "personas/Coder/PROFILE.md",
        "personas/Coder/INSIGHTS.md",
        "personas/Coder/memory/MEMORY.md",
    }
    assert "coder-profile-private" in _contents(coder_private)
    assert "coder-insight-private" in _contents(coder_private)
    assert "coder-long-term-memory" in _contents(coder_private)
    assert all(item.layer == "persona_private" for item in coder_private)


def test_plan_privacy_markers_fail_closed_across_blank_lines(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _write(
        workspace / "PROFILE.md",
        """# Profile

public-before

<private>
ephemeral-closed-one

ephemeral-closed-two
</private>

public-after-private

<persona-private>
persona-closed-one

<private>
nested-ephemeral-secret
</private>

persona-closed-two
</persona-private>

public-before-unclosed-persona

<persona-private>
persona-unclosed-one

persona-unclosed-two
""",
    )
    _write(
        workspace / "INSIGHTS.md",
        """public-insight-before

<private>
ephemeral-unclosed-one

ephemeral-unclosed-two
""",
    )

    plan = build_shared_memory_backfill_plan(workspace, _config())

    all_content = _contents(plan.items)
    public_content = _contents(plan.items, layer="public")
    private_content = _contents(plan.items, layer="persona_private")
    for forbidden in (
        "ephemeral-closed-one",
        "ephemeral-closed-two",
        "nested-ephemeral-secret",
        "ephemeral-unclosed-one",
        "ephemeral-unclosed-two",
        "<private>",
        "<persona-private>",
    ):
        assert forbidden not in all_content

    assert "public-before" in public_content
    assert "public-after-private" in public_content
    assert "public-before-unclosed-persona" in public_content
    assert "public-insight-before" not in public_content
    assert "public-insight-before" in private_content
    assert "persona-closed-one" not in public_content
    assert "persona-unclosed-one" not in public_content

    assert "persona-closed-one" in private_content
    assert "persona-closed-two" in private_content
    assert "persona-unclosed-one" in private_content
    assert "persona-unclosed-two" in private_content
    assert sum("Unclosed <private>" in warning for warning in plan.warnings) == 1
    assert sum("Unclosed <persona-private>" in warning for warning in plan.warnings) == 1


def test_custom_missing_overlays_do_not_duplicate_root_fallbacks(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _write(workspace / "PROFILE.md", "root-profile-only")
    _write(workspace / "INSIGHTS.md", "root-insight-only")
    _write(workspace / "personas" / "Coder" / "SOUL.md", "custom persona")
    _write(
        workspace / "personas" / "Coder" / "memory" / "MEMORY.md",
        "coder-memory-only",
    )

    plan = build_shared_memory_backfill_plan(workspace, _config())

    combined = _contents(plan.items)
    assert combined.count("root-profile-only") == 1
    assert combined.count("root-insight-only") == 1
    assert combined.count("coder-memory-only") == 1
    assert not any(
        item.source_file in {"personas/Coder/PROFILE.md", "personas/Coder/INSIGHTS.md"}
        for item in plan.items
    )
    inherited = {
        skipped.source_file for skipped in plan.skipped if skipped.reason == "inherited_default"
    }
    assert inherited == {
        "personas/Coder/PROFILE.md",
        "personas/Coder/INSIGHTS.md",
    }


def test_plan_rejects_memory_source_symlink_that_escapes_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside_profile = tmp_path / "outside-profile.md"
    outside_profile.write_text("must-stay-local", encoding="utf-8")
    (workspace / "PROFILE.md").symlink_to(outside_profile)

    with pytest.raises(ValueError, match="memory source escapes its persona workspace"):
        build_shared_memory_backfill_plan(workspace, _config())

    assert outside_profile.read_text(encoding="utf-8") == "must-stay-local"
    assert list(tmp_path.rglob("shared.sqlite")) == []


def test_persona_disabled_never_downgrades_private_candidates(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _write(
        workspace / "PROFILE.md",
        "public-fact\n\n<persona-private>default-private-fact</persona-private>",
    )
    _write(workspace / "INSIGHTS.md", "default-private-insight")
    _write(workspace / "memory" / "MEMORY.md", "default-private-memory")
    _write(workspace / "personas" / "Coder" / "PROFILE.md", "coder-private-profile")

    plan = build_shared_memory_backfill_plan(
        workspace,
        _config(personaEnabled=False),
    )

    assert _contents(plan.items, layer="public") == "public-fact"
    assert all(item.layer == "public" for item in plan.items)
    assert "default-private-fact" not in _contents(plan.items)
    assert "default-private-insight" not in _contents(plan.items)
    assert "default-private-memory" not in _contents(plan.items)
    assert "coder-private-profile" not in _contents(plan.items)
    skipped_sources = {
        skipped.source_file for skipped in plan.skipped if skipped.reason == "persona_disabled"
    }
    assert skipped_sources == {
        "PROFILE.md",
        "INSIGHTS.md",
        "memory/MEMORY.md",
        "personas/Coder/PROFILE.md",
    }


def test_global_write_off_skips_public_but_keeps_private_candidates(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _write(
        workspace / "PROFILE.md",
        "public-fact\n\n<persona-private>default-private-fact</persona-private>",
    )
    _write(workspace / "memory" / "MEMORY.md", "default-private-memory")

    plan = build_shared_memory_backfill_plan(
        workspace,
        _config(globalWriteMode="off"),
    )

    assert not any(item.layer == "public" for item in plan.items)
    assert "public-fact" not in _contents(plan.items)
    assert "default-private-fact" in _contents(plan.items)
    assert "default-private-memory" in _contents(plan.items)
    assert any(
        skipped.source_file == "PROFILE.md" and skipped.reason == "global_write_off"
        for skipped in plan.skipped
    )


def test_templates_and_non_memory_sources_are_not_imported(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    repository_root = Path(__file__).parents[2]
    _write(
        workspace / "PROFILE.md",
        "# User profile\n\n(Important facts about the user)",
    )
    _write(
        workspace / "INSIGHTS.md",
        "# Insights\n\n(User preferences learned over time)",
    )
    _write(
        workspace / "memory" / "MEMORY.md",
        (repository_root / "hahobot" / "templates" / "memory" / "MEMORY.md").read_text(
            encoding="utf-8"
        ),
    )
    _write(workspace / "SOUL.md", "never-import-soul")
    _write(workspace / "USER.md", "never-import-user")
    _write(workspace / "history" / "2026-07-28.md", "never-import-history")
    _write(workspace / "memory" / "archive.jsonl", "never-import-archive")
    _write(workspace / "templates" / "PROFILE.md", "never-import-template-tree")

    plan = build_shared_memory_backfill_plan(workspace, _config())

    assert plan.items == []
    rendered = json.dumps(plan.to_dict(), ensure_ascii=False)
    for forbidden in (
        "never-import-soul",
        "never-import-user",
        "never-import-history",
        "never-import-archive",
        "never-import-template-tree",
        "Important facts about the user",
        "User preferences learned over time",
    ):
        assert forbidden not in rendered


def test_plan_chunks_deterministically_and_deduplicates_same_destination(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    long_fact = "".join(f"{index:04d}" for index in range(2_500))
    assert len(long_fact) == 10_000
    coder = workspace / "personas" / "Coder"
    _write(coder / "PROFILE.md", long_fact)
    _write(coder / "INSIGHTS.md", long_fact)

    first = build_shared_memory_backfill_plan(workspace, _config())
    os.utime(coder / "PROFILE.md", None)
    second = build_shared_memory_backfill_plan(workspace, _config())

    assert len(first.items) == 3
    assert [len(item.content) for item in first.items] == [4_000, 4_000, 2_000]
    assert "".join(item.content for item in first.items) == long_fact
    assert [item.event_id for item in first.items] == [item.event_id for item in second.items]
    assert len({item.event_id for item in first.items}) == 3
    assert all(item.event_id.startswith("backfill-") for item in first.items)
    assert {item.source_file for item in first.items} == {"personas/Coder/PROFILE.md"}


def test_plan_json_never_contains_memory_body_or_api_key(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _write(workspace / "PROFILE.md", "ordinary-memory-body-needle")
    _write(
        workspace / "INSIGHTS.md",
        "<private>ephemeral-memory-needle</private>",
    )

    plan = build_shared_memory_backfill_plan(workspace, _config())
    rendered = json.dumps(plan.to_dict(mode="dry_run"), ensure_ascii=False)

    assert "ordinary-memory-body-needle" not in rendered
    assert "ephemeral-memory-needle" not in rendered
    assert "api-ultra-secret" not in rendered
    assert plan.items[0].content_sha256 in rendered


@pytest.mark.asyncio
async def test_execute_delivers_then_uses_durable_import_receipt(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _write(workspace / "PROFILE.md", "delivered-memory-body")
    config = _config()
    plan = build_shared_memory_backfill_plan(workspace, config)
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(handler)
    state_root = tmp_path / "shared-memory"
    first_mode, first_statuses = await execute_shared_memory_backfill(
        plan,
        config,
        state_root=state_root,
        transport=transport,
    )

    assert first_mode == "complete"
    assert set(first_statuses.values()) == {"delivered"}
    assert len(requests) == len(plan.items) == 1
    payload = json.loads(requests[0].content)
    assert payload["user_id"] == "shared-human"
    assert payload["messages"] == [{"role": "user", "content": "delivered-memory-body"}]
    assert payload["metadata"]["event_kind"] == "memory_backfill"
    assert payload["metadata"]["memory_namespace"] == "global"
    assert payload["metadata"]["hahobot_backfill_id"] == plan.items[0].event_id

    second_mode, second_statuses = await execute_shared_memory_backfill(
        plan,
        config,
        state_root=state_root,
        transport=transport,
    )

    assert second_mode == "complete"
    assert set(second_statuses.values()) == {"already_imported"}
    assert len(requests) == 1
    assert len(list(state_root.rglob("shared.sqlite"))) == 1


@pytest.mark.asyncio
async def test_execute_keeps_failed_delivery_pending_in_durable_outbox(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    _write(workspace / "PROFILE.md", "offline-memory-body")
    config = _config()
    plan = build_shared_memory_backfill_plan(workspace, config)
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(503, json={"error": "offline"})

    state_root = tmp_path / "shared-memory"
    mode, statuses = await execute_shared_memory_backfill(
        plan,
        config,
        state_root=state_root,
        transport=httpx.MockTransport(handler),
    )

    assert mode == "queued"
    assert set(statuses.values()) == {"pending"}
    assert len(requests) == len(plan.items) == 1
    assert len(list(state_root.rglob("shared.sqlite"))) == 1
