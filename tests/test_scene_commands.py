"""Tests for companion scene slash commands."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from hahobot.agent.tools.base import Tool
from hahobot.bus.events import InboundMessage


class _RecordingImageTool(Tool):
    def __init__(self, result: str) -> None:
        self._result = result
        self.calls: list[dict] = []

    @property
    def name(self) -> str:
        return "image_gen"

    @property
    def description(self) -> str:
        return "image_gen"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "prompt": {"type": "string"},
                "reference_image": {"type": "string"},
            },
            "required": ["prompt"],
        }

    async def execute(self, **kwargs) -> str:
        self.calls.append(kwargs)
        return self._result


def _make_loop(workspace: Path):
    from hahobot.agent.loop import AgentLoop
    from hahobot.bus.queue import MessageBus

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"

    with patch("hahobot.agent.loop.SubagentManager"):
        loop = AgentLoop(bus=bus, provider=provider, workspace=workspace)
    return loop


@pytest.mark.asyncio
async def test_scene_daily_returns_generated_media_with_scene_reference(tmp_path: Path) -> None:
    persona_dir = tmp_path / "personas" / "Aria"
    assets_dir = persona_dir / "assets"
    metadata_dir = persona_dir / ".hahobot"
    assets_dir.mkdir(parents=True)
    metadata_dir.mkdir()
    (assets_dir / "daily.png").write_bytes(b"png")
    (metadata_dir / "st_manifest.json").write_text(
        json.dumps({"reference_images": {"daily": "assets/daily.png"}}),
        encoding="utf-8",
    )

    output_path = tmp_path / "out" / "image_gen" / "scene_daily.png"
    output_path.parent.mkdir(parents=True)
    output_path.write_bytes(b"png")

    loop = _make_loop(tmp_path)
    tool = _RecordingImageTool(
        "Image generated successfully.\n"
        f"File path: {output_path}\n\n"
        "Next step: call the 'message' tool with media=['scene_daily.png'] to send it to the user."
    )
    loop.tools.register(tool)
    session = loop.sessions.get_or_create("cli:direct")
    session.metadata["persona"] = "Aria"
    loop.sessions.save(session)

    response = await loop._process_message(
        InboundMessage(channel="cli", sender_id="user", chat_id="direct", content="/scene daily")
    )

    assert response is not None
    assert response.content == "Aria sent over a quiet daily snapshot."
    assert response.media == [str(output_path)]
    assert len(tool.calls) == 1
    assert tool.calls[0]["reference_image"] == "__default__:daily"
    assert "shared-life snapshot" in tool.calls[0]["prompt"]


@pytest.mark.asyncio
async def test_scene_generate_omits_reference_when_none_is_configured(tmp_path: Path) -> None:
    output_path = tmp_path / "out" / "image_gen" / "scene_custom.png"
    output_path.parent.mkdir(parents=True)
    output_path.write_bytes(b"png")

    loop = _make_loop(tmp_path)
    tool = _RecordingImageTool(
        "Image generated successfully.\n"
        f"File path: {output_path}\n\n"
        "Next step: call the 'message' tool with media=['scene_custom.png'] to send it to the user."
    )
    loop.tools.register(tool)

    response = await loop._process_message(
        InboundMessage(
            channel="cli",
            sender_id="user",
            chat_id="direct",
            content="/scene generate rainy bookstore evening together",
        )
    )

    assert response is not None
    assert response.content == "your companion turned your idea into a companion scene."
    assert response.media == [str(output_path)]
    assert "reference_image" not in tool.calls[0]
    assert "rainy bookstore evening together" in tool.calls[0]["prompt"]


@pytest.mark.asyncio
async def test_scene_uses_persona_manifest_prompt_and_caption_overrides(tmp_path: Path) -> None:
    persona_dir = tmp_path / "personas" / "Aria"
    metadata_dir = persona_dir / ".hahobot"
    metadata_dir.mkdir(parents=True)
    (metadata_dir / "st_manifest.json").write_text(
        json.dumps(
            {
                "scene_prompts": {"comfort": "Prefer a quiet sofa corner, blanket, and gentle posture."},
                "scene_captions": {"comfort": "{persona} stayed with you on the sofa."},
            }
        ),
        encoding="utf-8",
    )

    output_path = tmp_path / "out" / "image_gen" / "scene_comfort.png"
    output_path.parent.mkdir(parents=True)
    output_path.write_bytes(b"png")

    loop = _make_loop(tmp_path)
    tool = _RecordingImageTool(
        "Image generated successfully.\n"
        f"File path: {output_path}\n\n"
        "Next step: call the 'message' tool with media=['scene_comfort.png'] to send it to the user."
    )
    loop.tools.register(tool)
    session = loop.sessions.get_or_create("cli:direct")
    session.metadata["persona"] = "Aria"
    loop.sessions.save(session)

    response = await loop._process_message(
        InboundMessage(channel="cli", sender_id="user", chat_id="direct", content="/scene comfort")
    )

    assert response is not None
    assert response.content == "Aria stayed with you on the sofa."
    assert response.media == [str(output_path)]
    assert "quiet sofa corner" in tool.calls[0]["prompt"]


@pytest.mark.asyncio
async def test_scene_list_includes_builtin_and_custom_scene_names(tmp_path: Path) -> None:
    persona_dir = tmp_path / "personas" / "Aria"
    metadata_dir = persona_dir / ".hahobot"
    metadata_dir.mkdir(parents=True)
    (metadata_dir / "st_manifest.json").write_text(
        json.dumps(
            {
                "scene_prompts": {"rainy_walk": "Umbrella, wet street reflections, close walk."},
                "reference_images": {"festival": "assets/festival.png"},
            }
        ),
        encoding="utf-8",
    )

    loop = _make_loop(tmp_path)
    session = loop.sessions.get_or_create("cli:direct")
    session.metadata["persona"] = "Aria"
    loop.sessions.save(session)

    response = await loop._process_message(
        InboundMessage(channel="cli", sender_id="user", chat_id="direct", content="/scene list")
    )

    assert response is not None
    assert "Available scenes for Aria:" in response.content
    assert "- daily" in response.content
    assert "- comfort" in response.content
    assert "- date" in response.content
    assert "- rainy_walk" in response.content
    assert "- festival" in response.content


@pytest.mark.asyncio
async def test_scene_custom_scene_name_uses_manifest_guidance(tmp_path: Path) -> None:
    persona_dir = tmp_path / "personas" / "Aria"
    assets_dir = persona_dir / "assets"
    metadata_dir = persona_dir / ".hahobot"
    assets_dir.mkdir(parents=True)
    metadata_dir.mkdir()
    (assets_dir / "rainy_walk.png").write_bytes(b"png")
    (metadata_dir / "st_manifest.json").write_text(
        json.dumps(
            {
                "scene_prompts": {"rainy_walk": "Umbrella, wet street reflections, close walk."},
                "reference_images": {"rainy_walk": "assets/rainy_walk.png"},
            }
        ),
        encoding="utf-8",
    )

    output_path = tmp_path / "out" / "image_gen" / "scene_rainy_walk.png"
    output_path.parent.mkdir(parents=True)
    output_path.write_bytes(b"png")

    loop = _make_loop(tmp_path)
    tool = _RecordingImageTool(
        "Image generated successfully.\n"
        f"File path: {output_path}\n\n"
        "Next step: call the 'message' tool with media=['scene_rainy_walk.png'] to send it to the user."
    )
    loop.tools.register(tool)
    session = loop.sessions.get_or_create("cli:direct")
    session.metadata["persona"] = "Aria"
    loop.sessions.save(session)

    response = await loop._process_message(
        InboundMessage(channel="cli", sender_id="user", chat_id="direct", content="/scene rainy_walk")
    )

    assert response is not None
    assert response.content == "Aria made a rainy walk scene for you."
    assert response.media == [str(output_path)]
    assert tool.calls[0]["reference_image"] == "__default__:rainy_walk"
    assert "Scene theme: rainy_walk" in tool.calls[0]["prompt"]
    assert "Umbrella, wet street reflections" in tool.calls[0]["prompt"]


@pytest.mark.asyncio
async def test_scene_unknown_scene_reports_available_scenes(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)

    response = await loop._process_message(
        InboundMessage(channel="cli", sender_id="user", chat_id="direct", content="/scene missing_scene")
    )

    assert response is not None
    assert "Unknown scene: missing_scene" in response.content
    assert "- daily" in response.content


@pytest.mark.asyncio
async def test_scene_reports_disabled_image_generation(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)

    response = await loop._process_message(
        InboundMessage(channel="cli", sender_id="user", chat_id="direct", content="/scene comfort")
    )

    assert response is not None
    assert response.content == "Image generation is not enabled. Turn on tools.imageGen.enabled first."
