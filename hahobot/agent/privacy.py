"""Privacy filtering helpers for memory/session persistence."""

from __future__ import annotations

import copy
import re
from typing import Any

_PRIVATE_BLOCK_RE = re.compile(r"<private\b[^>]*>.*?</private>", re.IGNORECASE | re.DOTALL)
_PRIVATE_UNCLOSED_RE = re.compile(r"<private\b[^>]*>.*\Z", re.IGNORECASE | re.DOTALL)
_PRIVATE_LINE_RE = re.compile(r"^\s*(?:<!--\s*)?private\s*:\s*.*?(?:-->)?\s*$", re.IGNORECASE)
_PERSONA_PRIVATE_BLOCK_RE = re.compile(
    r"<persona-private\b[^>]*>(.*?)</persona-private>",
    re.IGNORECASE | re.DOTALL,
)
_PERSONA_PRIVATE_UNCLOSED_RE = re.compile(
    r"<persona-private\b[^>]*>(.*)\Z",
    re.IGNORECASE | re.DOTALL,
)
_REDACTION = "[private redacted]"


def strip_private_text(text: str, *, replacement: str = _REDACTION) -> str:
    """Remove ephemeral secrets and unwrap content intended for persona persistence.

    ``replacement`` defaults to the visible persistence marker used by live turns.
    Export-style callers may pass an empty string when even the marker would be
    meaningless outside the local transcript.
    """
    if not text:
        return text
    redacted = _PRIVATE_BLOCK_RE.sub(replacement, text)
    redacted = _PRIVATE_UNCLOSED_RE.sub(replacement, redacted)
    redacted = _PERSONA_PRIVATE_BLOCK_RE.sub(lambda match: match.group(1), redacted)
    redacted = _PERSONA_PRIVATE_UNCLOSED_RE.sub(lambda match: match.group(1), redacted)
    lines = [line for line in redacted.splitlines() if not _PRIVATE_LINE_RE.match(line)]
    return "\n".join(lines).strip()


def strip_persona_private_text(text: str) -> str:
    """Remove persona-private blocks before writing to a public shared namespace."""
    if not text:
        return text
    public = _PERSONA_PRIVATE_BLOCK_RE.sub("", text)
    return _PERSONA_PRIVATE_UNCLOSED_RE.sub("", public).strip()


def extract_persona_private_text(text: str) -> str:
    """Return only persona-private block bodies, failing closed through EOF.

    Ephemeral ``<private>`` content inside a persona-private block is removed as
    part of extraction. This helper is intended for explicit namespace routing;
    it cannot reconstruct wrappers already removed by earlier persistence.
    """
    if not text:
        return ""
    blocks = [match.group(1) for match in _PERSONA_PRIVATE_BLOCK_RE.finditer(text)]
    closed_removed = _PERSONA_PRIVATE_BLOCK_RE.sub("", text)
    if match := _PERSONA_PRIVATE_UNCLOSED_RE.search(closed_removed):
        blocks.append(match.group(1))
    return strip_private_text("\n\n".join(blocks), replacement="")


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
