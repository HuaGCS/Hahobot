"""Tool-call id deduplication on the non-streaming _parse paths.

Ported from nanobot 3ca82ea8: some providers reuse one id for parallel tool
calls. The streaming path already deduped; the dict-mapping and SDK-object
non-stream paths must do the same so tool-result pairing stays unambiguous.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from hahobot.providers.openai_compat_provider import OpenAICompatProvider


def _provider() -> OpenAICompatProvider:
    with patch("hahobot.providers.openai_compat_provider.AsyncOpenAI"):
        return OpenAICompatProvider()


def test_dict_path_dedups_reused_tool_call_ids() -> None:
    provider = _provider()
    response = {
        "choices": [
            {
                "message": {
                    "content": None,
                    "tool_calls": [
                        {"id": "dup", "function": {"name": "a", "arguments": "{}"}},
                        {"id": "dup", "function": {"name": "b", "arguments": "{}"}},
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
    }

    result = provider._parse(response)
    ids = [tc.id for tc in result.tool_calls]
    assert len(ids) == 2
    assert len(set(ids)) == 2  # the duplicate id was reassigned


def test_sdk_object_path_dedups_reused_tool_call_ids() -> None:
    provider = _provider()
    tc1 = SimpleNamespace(id="dup", function=SimpleNamespace(name="a", arguments="{}"))
    tc2 = SimpleNamespace(id="dup", function=SimpleNamespace(name="b", arguments="{}"))
    msg = SimpleNamespace(content=None, tool_calls=[tc1, tc2], reasoning_content=None)
    choice = SimpleNamespace(message=msg, finish_reason="tool_calls")
    response = SimpleNamespace(choices=[choice], usage=None)

    result = provider._parse(response)
    ids = [tc.id for tc in result.tool_calls]
    assert len(ids) == 2
    assert len(set(ids)) == 2


def test_distinct_ids_preserved() -> None:
    provider = _provider()
    response = {
        "choices": [
            {
                "message": {
                    "content": None,
                    "tool_calls": [
                        {"id": "call_1", "function": {"name": "a", "arguments": "{}"}},
                        {"id": "call_2", "function": {"name": "b", "arguments": "{}"}},
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
    }

    result = provider._parse(response)
    assert [tc.id for tc in result.tool_calls] == ["call_1", "call_2"]
