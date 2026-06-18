"""Tests for LLMProvider._stream_idle_timeout_s config validation.

A non-positive override (e.g. ``0``) would make httpx time out the stream
immediately; an absurdly large one defeats the idle guard. Garbage and
out-of-range input must fall back / clamp instead of being trusted. Hardened
per nanobot 846410f9.
"""

import pytest

from hahobot.providers.base import LLMProvider

ENV = "HAHOBOT_STREAM_IDLE_TIMEOUT_S"


def test_default_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV, raising=False)
    assert LLMProvider._stream_idle_timeout_s() == 90.0


def test_blank_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV, "   ")
    assert LLMProvider._stream_idle_timeout_s() == 90.0


def test_valid_value_is_used(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV, "120")
    assert LLMProvider._stream_idle_timeout_s() == 120.0


def test_non_numeric_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV, "abc")
    assert LLMProvider._stream_idle_timeout_s() == 90.0


def test_zero_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV, "0")
    assert LLMProvider._stream_idle_timeout_s() == 90.0


def test_negative_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV, "-5")
    assert LLMProvider._stream_idle_timeout_s() == 90.0


def test_oversized_is_clamped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV, "999999")
    assert LLMProvider._stream_idle_timeout_s() == 3600.0
