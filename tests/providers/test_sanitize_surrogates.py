"""Provider-boundary regression tests for malformed UTF-16 surrogates."""

from __future__ import annotations

import json

from hahobot.providers.base import LLMProvider
from hahobot.utils.helpers import sanitize_surrogates, sanitize_surrogates_deep


def test_sanitize_surrogates_reconstructs_pairs_and_replaces_lone_values() -> None:
    assert sanitize_surrogates("robot \ud83e\udd16") == "robot 🤖"
    cleaned = sanitize_surrogates("broken \ud83e value")
    assert "\ud83e" not in cleaned
    assert "\ufffd" in cleaned
    cleaned.encode("utf-8")


def test_sanitize_surrogates_deep_preserves_clean_identity() -> None:
    payload = {"role": "user", "content": [{"type": "text", "text": "clean 🤖"}]}

    assert sanitize_surrogates_deep(payload) is payload


def test_provider_sanitizes_nested_message_payload_before_serialization() -> None:
    messages = [
        {"role": "user", "content": "paired \ud83e\udd16"},
        {
            "role": "assistant",
            "content": [{"type": "text", "text": "lone \ud83e half"}],
        },
    ]

    cleaned = LLMProvider._sanitize_empty_content(messages)

    assert cleaned[0]["content"] == "paired 🤖"
    assert "\ud83e" not in cleaned[1]["content"][0]["text"]
    json.dumps(cleaned, ensure_ascii=False).encode("utf-8")
