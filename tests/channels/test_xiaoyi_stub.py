"""Tests for the Xiaoyi stub channel (no real integration yet)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from hahobot.bus.events import OutboundMessage
from hahobot.channels.base import NonRetriableSendError
from hahobot.channels.registry import discover_all
from hahobot.channels.xiaoyi import XiaoyiChannel
from hahobot.config.schema import Config, XiaoyiConfig


def test_xiaoyi_module_is_discoverable_via_registry() -> None:
    channels = discover_all()
    assert "xiaoyi" in channels
    assert channels["xiaoyi"] is XiaoyiChannel


def test_default_config_is_safe_and_disabled() -> None:
    cfg = XiaoyiConfig()
    assert cfg.enabled is False
    assert cfg.region == "cn-north-4"
    assert cfg.allow_from == []
    payload = XiaoyiChannel.default_config()
    assert payload["enabled"] is False


def test_top_level_config_carries_xiaoyi_block() -> None:
    config = Config()
    assert config.channels.xiaoyi.enabled is False


@pytest.mark.asyncio
async def test_start_is_silent_noop_when_disabled() -> None:
    channel = XiaoyiChannel(XiaoyiConfig(), MagicMock())
    await channel.start()
    assert channel.is_running is False


@pytest.mark.asyncio
async def test_start_logs_warning_but_does_not_raise_when_enabled() -> None:
    cfg = XiaoyiConfig(enabled=True, app_id="probe", app_secret="probe")
    channel = XiaoyiChannel(cfg, MagicMock())
    await channel.start()
    # The stub marks itself running so ChannelManager doesn't restart it endlessly,
    # but it doesn't pretend to actually connect.
    assert channel.is_running is True


@pytest.mark.asyncio
async def test_send_raises_non_retriable_until_implemented() -> None:
    channel = XiaoyiChannel(XiaoyiConfig(enabled=True), MagicMock())
    with pytest.raises(NonRetriableSendError, match="stub"):
        await channel.send(
            OutboundMessage(channel="xiaoyi", chat_id="dev-1", content="hello"),
        )
