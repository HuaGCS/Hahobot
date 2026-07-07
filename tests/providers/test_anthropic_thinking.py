"""Tests for Anthropic provider thinking / reasoning_effort modes."""

from __future__ import annotations

from unittest.mock import patch

from hahobot.providers.anthropic_provider import AnthropicProvider


def _make_provider(model: str = "claude-sonnet-4-6") -> AnthropicProvider:
    with patch("anthropic.AsyncAnthropic"):
        return AnthropicProvider(api_key="sk-test", default_model=model)


def _build(provider: AnthropicProvider, reasoning_effort: str | None, **overrides):
    defaults = {
        "messages": [{"role": "user", "content": "hello"}],
        "tools": None,
        "model": None,
        "max_tokens": 4096,
        "temperature": 0.7,
        "reasoning_effort": reasoning_effort,
        "tool_choice": None,
        "supports_caching": False,
    }
    defaults.update(overrides)
    return provider._build_kwargs(**defaults)


def test_adaptive_sets_type_adaptive() -> None:
    kw = _build(_make_provider(), "adaptive")
    assert kw["thinking"] == {"type": "adaptive"}


def test_adaptive_forces_temperature_one() -> None:
    kw = _build(_make_provider(), "adaptive")
    assert kw["temperature"] == 1.0


def test_adaptive_does_not_inflate_max_tokens() -> None:
    kw = _build(_make_provider(), "adaptive", max_tokens=2048)
    assert kw["max_tokens"] == 2048


def test_adaptive_no_budget_tokens() -> None:
    kw = _build(_make_provider(), "adaptive")
    assert "budget_tokens" not in kw["thinking"]


def test_high_uses_enabled_with_budget() -> None:
    kw = _build(_make_provider(), "high", max_tokens=4096)
    assert kw["thinking"]["type"] == "enabled"
    assert kw["thinking"]["budget_tokens"] == max(8192, 4096)
    assert kw["max_tokens"] >= kw["thinking"]["budget_tokens"] + 4096


def test_low_uses_small_budget() -> None:
    kw = _build(_make_provider(), "low")
    assert kw["thinking"] == {"type": "enabled", "budget_tokens": 1024}


def test_none_does_not_enable_thinking() -> None:
    kw = _build(_make_provider(), None)
    assert "thinking" not in kw
    assert kw["temperature"] == 0.7


def test_opus_4_7_omits_temperature_without_thinking() -> None:
    kw = _build(_make_provider("claude-opus-4-7"), None)
    assert "temperature" not in kw


def test_opus_4_7_omits_temperature_with_adaptive_thinking() -> None:
    kw = _build(_make_provider("claude-opus-4-7"), "adaptive")
    assert kw["thinking"] == {"type": "adaptive"}
    assert "temperature" not in kw


def test_opus_4_8_omits_temperature() -> None:
    assert "temperature" not in _build(_make_provider("claude-opus-4-8"), None)
    kw = _build(_make_provider("claude-opus-4-8"), "adaptive")
    assert "temperature" not in kw


def test_fable_omits_temperature() -> None:
    assert "temperature" not in _build(_make_provider("claude-fable-5"), None)
    assert "temperature" not in _build(_make_provider("claude-fable-5"), "high")


def test_sonnet_5_omits_temperature() -> None:
    # claude-sonnet-5 also deprecated `temperature`; cover none/adaptive/enabled paths.
    assert "temperature" not in _build(_make_provider("anthropic/claude-sonnet-5"), None)
    kw_adaptive = _build(_make_provider("claude-sonnet-5"), "adaptive")
    assert "temperature" not in kw_adaptive
    assert kw_adaptive["thinking"] == {"type": "adaptive"}
    assert "temperature" not in _build(_make_provider("claude-sonnet-5"), "high", max_tokens=4096)


def test_omit_temperature_matches_mixed_case() -> None:
    assert "temperature" not in _build(_make_provider("Claude-Opus-4-8"), None)


def test_ordinary_model_keeps_temperature() -> None:
    assert _build(_make_provider("claude-sonnet-4-6"), None)["temperature"] == 0.7


def test_tool_result_converts_image_url_blocks() -> None:
    block = AnthropicProvider._tool_result_block(
        {
            "tool_call_id": "toolu_1",
            "content": [
                {"type": "text", "text": "see image"},
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/png;base64,aGVsbG8="},
                },
            ],
        }
    )

    assert block["content"][0] == {"type": "text", "text": "see image"}
    assert block["content"][1]["type"] == "image"
    assert block["content"][1]["source"]["media_type"] == "image/png"
