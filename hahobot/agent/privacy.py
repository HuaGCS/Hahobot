"""Privacy filtering helpers for memory/session persistence."""

from __future__ import annotations

import copy
import re
from typing import Any

_PRIVATE_BLOCK_RE = re.compile(r"<private\b[^>]*>.*?</private>", re.IGNORECASE | re.DOTALL)
_PRIVATE_LINE_RE = re.compile(r"^\s*(?:<!--\s*)?private\s*:\s*.*?(?:-->)?\s*$", re.IGNORECASE)
_REDACTION = "[private redacted]"


def strip_private_text(text: str) -> str:
    """Remove user-marked private content before local/remote memory persistence."""
    if not text:
        return text
    redacted = _PRIVATE_BLOCK_RE.sub(_REDACTION, text)
    lines = [line for line in redacted.splitlines() if not _PRIVATE_LINE_RE.match(line)]
    return "\n".join(lines).strip()


def strip_private_content(value: Any) -> Any:
    """Strip private tags from string or text-block content while preserving shape."""
    if isinstance(value, str):
        return strip_private_text(value)
    if isinstance(value, list):
        blocks: list[Any] = []
        for block in value:
            if isinstance(block, dict):
                copied = dict(block)
                if copied.get("type") == "text" and isinstance(copied.get("text"), str):
                    copied["text"] = strip_private_text(copied["text"])
                    if not copied["text"]:
                        continue
                blocks.append(copied)
            elif isinstance(block, str):
                cleaned = strip_private_text(block)
                if cleaned:
                    blocks.append(cleaned)
            else:
                blocks.append(block)
        return blocks
    return value


def strip_private_message(message: dict[str, Any]) -> dict[str, Any]:
    """Return a deep-copied message safe for memory/session persistence."""
    copied = copy.deepcopy(message)
    if "content" in copied:
        copied["content"] = strip_private_content(copied.get("content"))
    return copied


def strip_private_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Strip private content from a list of chat messages."""
    return [strip_private_message(message) for message in messages]
