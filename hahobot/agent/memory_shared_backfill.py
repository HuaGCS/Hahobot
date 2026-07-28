"""One-time, privacy-aware import of local memory files into shared Mem0."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from hahobot.agent.memory_backends.mem0_backend import (
    Mem0SharedMemoryBackend,
    persona_mem0_user_id,
)
from hahobot.agent.memory_facts_sqlite import parse_memory_fragments
from hahobot.agent.memory_metadata import parse_memory_fact_metadata
from hahobot.agent.personas import (
    DEFAULT_PERSONA,
    list_personas,
    persona_workspace,
    resolve_persona_name,
)
from hahobot.agent.privacy import (
    extract_persona_private_text,
    strip_persona_private_text,
    strip_private_text,
)

if TYPE_CHECKING:
    from hahobot.config.schema import SharedMemoryConfig


_BACKFILL_SCHEMA = 1
_MAX_ITEM_CHARS = 4_000
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(?P<title>.*?)\s*#*\s*$")
_BULLET_RE = re.compile(r"^\s*[-*+]\s+")
_META_RE = re.compile(r"<!--\s*hahobot-meta:\s*.*?\s*-->", re.IGNORECASE)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_RULE_RE = re.compile(r"^\s*(?:[-*_]\s*){3,}$")
_PRIVATE_OPEN_RE = re.compile(r"<private\b[^>]*>", re.IGNORECASE)
_PRIVATE_CLOSE_RE = re.compile(r"</private>", re.IGNORECASE)
_PERSONA_OPEN_RE = re.compile(r"<persona-private\b[^>]*>", re.IGNORECASE)
_PERSONA_CLOSE_RE = re.compile(r"</persona-private>", re.IGNORECASE)
_PLACEHOLDERS = {
    "this file stores important information that should persist across sessions.",
    "(important facts about the user)",
    "(user preferences learned over time)",
    "(information about ongoing projects)",
    "(things to remember)",
    "*this file is automatically updated by hahobot when important information should be remembered.*",
}


@dataclass(slots=True, frozen=True)
class SharedMemoryBackfillItem:
    """One sanitized and deterministically addressed Mem0 write."""

    event_id: str
    persona: str
    source_file: str
    layer: str
    user_id: str
    content: str = field(repr=False)
    content_sha256: str
    metadata: dict[str, Any] = field(repr=False)

    def to_dict(self, *, status: str = "candidate") -> dict[str, Any]:
        return {
            "eventId": self.event_id,
            "persona": self.persona,
            "sourceFile": self.source_file,
            "layer": self.layer,
            "userId": self.user_id,
            "chars": len(self.content),
            "contentSha256": self.content_sha256,
            "status": status,
        }


@dataclass(slots=True, frozen=True)
class SharedMemoryBackfillSkip:
    persona: str
    source_file: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {
            "persona": self.persona,
            "sourceFile": self.source_file,
            "reason": self.reason,
        }


@dataclass(slots=True)
class SharedMemoryBackfillPlan:
    workspace: Path
    public_user_id: str
    persona_enabled: bool
    selected_personas: list[str]
    items: list[SharedMemoryBackfillItem] = field(default_factory=list)
    skipped: list[SharedMemoryBackfillSkip] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    files_scanned: int = 0
    files_missing: int = 0

    def to_dict(
        self,
        *,
        mode: str = "dry_run",
        statuses: dict[str, str] | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        statuses = statuses or {}
        status_counts: dict[str, int] = {}
        rendered_items: list[dict[str, Any]] = []
        for item in self.items:
            item_status = statuses.get(item.event_id, "candidate")
            status_counts[item_status] = status_counts.get(item_status, 0) + 1
            rendered_items.append(item.to_dict(status=item_status))
        public_items = sum(item.layer == "public" for item in self.items)
        private_items = len(self.items) - public_items
        return {
            "status": mode,
            "dryRun": mode == "dry_run",
            "force": force,
            "workspace": str(self.workspace),
            "provider": "mem0",
            "publicUserId": self.public_user_id,
            "personaEnabled": self.persona_enabled,
            "selectedPersonas": self.selected_personas,
            "totals": {
                "filesScanned": self.files_scanned,
                "filesMissing": self.files_missing,
                "candidateWrites": len(self.items),
                "candidateChars": sum(len(item.content) for item in self.items),
                "publicWrites": public_items,
                "personaPrivateWrites": private_items,
                "skipped": len(self.skipped),
                "statuses": status_counts,
            },
            "items": rendered_items,
            "skipped": [item.to_dict() for item in self.skipped],
            "warnings": list(self.warnings),
        }


def validate_backfill_config(config: SharedMemoryConfig) -> None:
    """Reject configurations that cannot safely accept a backfill."""
    if not config.enabled:
        raise ValueError("memory.shared.enabled must be true before backfill")
    if config.provider != "mem0":
        raise ValueError("memory.shared.provider must be mem0")
    if not config.base_url.strip():
        raise ValueError("memory.shared.baseUrl is required before backfill")
    if not config.user_id.strip():
        raise ValueError("memory.shared.userId is required before backfill")
    if not config.write_enabled:
        raise ValueError("memory.shared.writeEnabled must be true before backfill")


def build_shared_memory_backfill_plan(
    workspace: Path,
    config: SharedMemoryConfig,
    *,
    personas: list[str] | None = None,
) -> SharedMemoryBackfillPlan:
    """Build a read-only import plan from physical persona memory files."""
    workspace = workspace.expanduser().resolve(strict=False)
    requested = list(personas) if personas is not None else list_personas(workspace)
    selected: list[str] = []
    selected_keys: set[str] = set()
    for persona in requested:
        resolved = resolve_persona_name(workspace, persona)
        if resolved is None:
            raise ValueError(f"unknown persona: {persona}")
        if resolved.casefold() in selected_keys:
            continue
        selected_keys.add(resolved.casefold())
        selected.append(resolved)
    plan = SharedMemoryBackfillPlan(
        workspace=workspace,
        public_user_id=config.user_id.strip(),
        persona_enabled=config.persona_enabled,
        selected_personas=selected,
    )
    seen_ids: set[str] = set()

    private_user_ids: dict[str, str] = {}
    if config.persona_enabled:
        for persona in selected:
            private_user_ids[persona] = persona_mem0_user_id(config, persona)

    for persona in selected:
        root = persona_workspace(workspace, persona)
        resolved_root = root.resolve(strict=False)
        if not resolved_root.is_relative_to(workspace):
            raise ValueError(f"persona workspace escapes the active workspace: {persona}")
        for filename in ("PROFILE.md", "INSIGHTS.md", "memory/MEMORY.md"):
            path = root / filename
            source_file = _relative_source(path, workspace)
            if not path.is_file():
                plan.files_missing += 1
                reason = (
                    "inherited_default"
                    if persona != DEFAULT_PERSONA and filename in {"PROFILE.md", "INSIGHTS.md"}
                    else "missing"
                )
                plan.skipped.append(SharedMemoryBackfillSkip(persona, source_file, reason))
                continue
            if not path.resolve(strict=True).is_relative_to(resolved_root):
                raise ValueError(f"memory source escapes its persona workspace: {source_file}")
            plan.files_scanned += 1
            try:
                raw = path.read_text(encoding="utf-8")
                modified = datetime.fromtimestamp(path.stat().st_mtime).isoformat(
                    timespec="minutes"
                )
            except OSError:
                plan.skipped.append(SharedMemoryBackfillSkip(persona, source_file, "read_error"))
                continue
            _record_privacy_warnings(plan, raw, source_file)

            if persona == DEFAULT_PERSONA and filename == "PROFILE.md":
                public_text = strip_private_text(strip_persona_private_text(raw), replacement="")
                if config.global_write_mode == "off":
                    if public_text.strip():
                        plan.skipped.append(
                            SharedMemoryBackfillSkip(persona, source_file, "global_write_off")
                        )
                else:
                    _append_file_items(
                        plan,
                        seen_ids,
                        text=public_text,
                        filename=filename,
                        persona=persona,
                        source_file=source_file,
                        layer="public",
                        user_id=config.user_id.strip(),
                        modified=modified,
                    )

                explicit_private = extract_persona_private_text(raw)
                if explicit_private:
                    if config.persona_enabled:
                        _append_file_items(
                            plan,
                            seen_ids,
                            text=explicit_private,
                            filename=filename,
                            persona=persona,
                            source_file=source_file,
                            layer="persona_private",
                            user_id=private_user_ids[persona],
                            modified=modified,
                            explicit_private=True,
                        )
                    else:
                        plan.skipped.append(
                            SharedMemoryBackfillSkip(persona, source_file, "persona_disabled")
                        )
                continue

            private_text = strip_private_text(raw, replacement="")
            if not config.persona_enabled:
                if private_text.strip():
                    plan.skipped.append(
                        SharedMemoryBackfillSkip(persona, source_file, "persona_disabled")
                    )
                continue
            _append_file_items(
                plan,
                seen_ids,
                text=private_text,
                filename=filename,
                persona=persona,
                source_file=source_file,
                layer="persona_private",
                user_id=private_user_ids[persona],
                modified=modified,
            )

    if any(item.layer == "public" for item in plan.items):
        plan.warnings.append(
            "Older local persistence may already have removed <persona-private> wrappers; "
            "review public candidates before applying because that original intent cannot "
            "be reconstructed automatically."
        )
    return plan


async def execute_shared_memory_backfill(
    plan: SharedMemoryBackfillPlan,
    config: SharedMemoryConfig,
    *,
    state_root: Path,
    force: bool = False,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[str, dict[str, str]]:
    """Durably queue every candidate, then attempt only this backfill's writes."""
    if not plan.items:
        return "no_op", {}

    statuses: dict[str, str] = {}
    by_user: dict[tuple[str, str], list[SharedMemoryBackfillItem]] = {}
    for item in plan.items:
        by_user.setdefault((item.user_id, item.layer), []).append(item)

    targets: list[tuple[Mem0SharedMemoryBackend, set[str]]] = []
    backends: list[Mem0SharedMemoryBackend] = []
    try:
        # Persist the complete plan before making the first network request. A
        # cancelled/offline CLI therefore never leaves later namespaces absent
        # merely because an earlier namespace was slow.
        for (user_id, layer), items in by_user.items():
            target_config = config.model_copy(update={"user_id": user_id})
            backend = Mem0SharedMemoryBackend(
                target_config,
                state_root=state_root,
                transport=transport,
                namespace="global" if layer == "public" else "persona",
                write_mode="full",
            )
            backends.append(backend)
            queued_ids: set[str] = set()
            for item in items:
                queued = await backend.enqueue_backfill(
                    event_id=item.event_id,
                    content=item.content,
                    metadata=item.metadata,
                    force=force,
                )
                if queued == "delivered":
                    statuses[item.event_id] = "already_imported"
                else:
                    statuses[item.event_id] = "already_queued" if queued == "pending" else "queued"
                    queued_ids.add(item.event_id)
            targets.append((backend, queued_ids))

        for backend, queued_ids in targets:
            if queued_ids:
                drained = await backend.drain_backfill(queued_ids)
                for event_id, status in drained.items():
                    statuses[event_id] = status
    finally:
        for backend in backends:
            await backend.close()

    values = list(statuses.values())
    accepted = sum(value in {"delivered", "already_imported"} for value in values)
    pending = sum(value in {"queued", "pending", "already_queued"} for value in values)
    if accepted == len(plan.items):
        mode = "complete"
    elif pending == len(plan.items):
        mode = "queued"
    else:
        mode = "partial"
    return mode, statuses


def _append_file_items(
    plan: SharedMemoryBackfillPlan,
    seen_ids: set[str],
    *,
    text: str,
    filename: str,
    persona: str,
    source_file: str,
    layer: str,
    user_id: str,
    modified: str,
    explicit_private: bool = False,
) -> None:
    fragments = (
        _memory_fragments(text, default_ts=modified)
        if filename == "memory/MEMORY.md"
        else _markdown_fragments(text)
    )
    added = 0
    for content, fragment_metadata in fragments:
        normalized = _normalize_content(content)
        if _is_boilerplate(normalized):
            continue
        for part in _split_long_item(normalized):
            digest = hashlib.sha256(part.encode("utf-8")).hexdigest()
            event_id = hashlib.sha256(
                (
                    f"hahobot-memory-backfill-v{_BACKFILL_SCHEMA}\0{user_id.casefold()}\0{digest}"
                ).encode()
            ).hexdigest()
            event_id = f"backfill-{event_id}"
            if event_id in seen_ids:
                continue
            seen_ids.add(event_id)
            metadata: dict[str, Any] = {
                "backfill_schema": _BACKFILL_SCHEMA,
                "content_sha256": digest,
                "memory_layer": _memory_layer(filename),
                "persona": persona,
                "source_persona": persona,
                "source_file": source_file,
            }
            metadata.update(fragment_metadata)
            if explicit_private:
                metadata["section"] = str(metadata.get("section") or "explicit-private")
            plan.items.append(
                SharedMemoryBackfillItem(
                    event_id=event_id,
                    persona=persona,
                    source_file=source_file,
                    layer=layer,
                    user_id=user_id,
                    content=part,
                    content_sha256=digest,
                    metadata=metadata,
                )
            )
            added += 1
    if added == 0 and text.strip():
        plan.skipped.append(SharedMemoryBackfillSkip(persona, source_file, "empty_or_boilerplate"))


def _markdown_fragments(text: str) -> list[tuple[str, dict[str, Any]]]:
    section = ""
    current: list[str] = []
    fragments: list[tuple[str, dict[str, Any]]] = []

    def flush() -> None:
        if not current:
            return
        raw = "\n".join(current).strip()
        first_line = current[0] if current else ""
        parsed = parse_memory_fact_metadata(first_line)
        cleaned = _META_RE.sub("", raw).strip()
        metadata: dict[str, Any] = {}
        if section:
            metadata["section"] = section
        if parsed is not None:
            if parsed.confidence:
                metadata["confidence"] = parsed.confidence
            if parsed.last_verified:
                metadata["last_verified"] = parsed.last_verified
        if cleaned:
            fragments.append((cleaned, metadata))
        current.clear()

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    for line in normalized.splitlines():
        if match := _HEADING_RE.match(line):
            flush()
            section = match.group("title").strip()
            continue
        if not line.strip():
            flush()
            continue
        if _BULLET_RE.match(line):
            flush()
        current.append(line)
    flush()
    return fragments


def _memory_fragments(text: str, *, default_ts: str) -> list[tuple[str, dict[str, Any]]]:
    result: list[tuple[str, dict[str, Any]]] = []
    for fragment in parse_memory_fragments(text, default_ts=default_ts):
        result.append(
            (
                str(fragment["fragment"]),
                {
                    "fragment_ts": str(fragment["ts"]),
                    "fragment_tag": str(fragment["tag"]),
                    "fragment_src": str(fragment["src"]),
                },
            )
        )
    return result


def _split_long_item(text: str) -> list[str]:
    parts: list[str] = []
    remaining = text
    while len(remaining) > _MAX_ITEM_CHARS:
        window = remaining[: _MAX_ITEM_CHARS + 1]
        cut = max(window.rfind("\n"), window.rfind("。"), window.rfind(". "))
        if cut < _MAX_ITEM_CHARS // 2:
            cut = _MAX_ITEM_CHARS
        elif window[cut : cut + 2] == ". ":
            cut += 1
        else:
            cut += 1
        part = remaining[:cut].strip()
        if part:
            parts.append(part)
        remaining = remaining[cut:].strip()
    if remaining:
        parts.append(remaining)
    return parts


def _normalize_content(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in normalized.splitlines()).strip()


def _is_boilerplate(text: str) -> bool:
    if not text:
        return True
    without_comments = _COMMENT_RE.sub("", text).strip()
    if (
        not without_comments
        or _RULE_RE.match(without_comments)
        or _HEADING_RE.fullmatch(without_comments)
    ):
        return True
    return without_comments.casefold() in _PLACEHOLDERS


def _memory_layer(filename: str) -> str:
    if filename == "PROFILE.md":
        return "profile"
    if filename == "INSIGHTS.md":
        return "insights"
    return "long_term"


def _relative_source(path: Path, workspace: Path) -> str:
    try:
        return path.relative_to(workspace).as_posix()
    except ValueError:
        return path.name


def _record_privacy_warnings(
    plan: SharedMemoryBackfillPlan,
    text: str,
    source_file: str,
) -> None:
    pairs = (
        ("private", _PRIVATE_OPEN_RE, _PRIVATE_CLOSE_RE),
        ("persona-private", _PERSONA_OPEN_RE, _PERSONA_CLOSE_RE),
    )
    for label, opening, closing in pairs:
        opens = list(opening.finditer(text))
        closes = list(closing.finditer(text))
        if len(opens) <= len(closes):
            continue
        line = text.count("\n", 0, opens[-1].start()) + 1
        plan.warnings.append(
            f"Unclosed <{label}> marker in {source_file} at line {line}; "
            "content through EOF was excluded from the unsafe target."
        )
