"""Skills loader for agent capabilities."""

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any, Callable

# Default builtin skills directory (relative to this file)
BUILTIN_SKILLS_DIR = Path(__file__).parent.parent / "skills"

# Opening ---, YAML body (group 1), closing --- on its own line; supports CRLF.
_STRIP_SKILL_FRONTMATTER = re.compile(
    r"^---\s*\r?\n(.*?)\r?\n---\s*\r?\n?",
    re.DOTALL,
)


def _escape_xml(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class SkillsLoader:
    """
    Loader for agent skills.

    Skills are markdown files (SKILL.md) that teach the agent how to use
    specific tools or perform certain tasks.
    """

    _SUMMARY_SKILL_LIMIT = 8
    _TOKEN_RE = re.compile(r"[a-z0-9]{2,}")

    def __init__(
        self,
        workspace: Path,
        builtin_skills_dir: Path | None = None,
        disabled_skills: set[str] | None = None,
    ):
        self.workspace = workspace
        self.workspace_skills = workspace / "skills"
        self.builtin_skills = builtin_skills_dir or BUILTIN_SKILLS_DIR
        self.disabled_skills = disabled_skills or set()

    @staticmethod
    def _normalize_string_list(value: Any) -> list[str]:
        if not value:
            return []
        if isinstance(value, str):
            values = [part.strip() for part in re.split(r"[,\n]", value)]
        elif isinstance(value, list):
            values = [str(part).strip() for part in value]
        else:
            return []

        normalized: list[str] = []
        seen: set[str] = set()
        for item in values:
            if not item:
                continue
            lowered = item.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            normalized.append(item)
        return normalized

    @staticmethod
    def _coerce_int(value: Any, default: int = 0) -> int:
        if isinstance(value, bool):
            return default
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            try:
                return int(value.strip())
            except ValueError:
                return default
        return default

    @classmethod
    def _tokenize(cls, *parts: str) -> set[str]:
        tokens: set[str] = set()
        for part in parts:
            tokens.update(cls._TOKEN_RE.findall((part or "").lower()))
        return tokens

    def _normalize_project_metadata(self, payload: dict[str, Any]) -> dict[str, Any]:
        requires = payload.get("requires")
        if not isinstance(requires, dict):
            requires = {}
        return {
            "always": bool(payload.get("always")),
            "requires": requires,
            "triggers": self._normalize_string_list(payload.get("triggers") or payload.get("keywords")),
            "tool_tags": self._normalize_string_list(
                payload.get("tool_tags") or payload.get("toolTags") or payload.get("tools")
            ),
            "supersedes": self._normalize_string_list(payload.get("supersedes")),
            "last_used": str(payload.get("last_used") or payload.get("lastUsed") or "").strip(),
            "success_count": self._coerce_int(
                payload.get("success_count", payload.get("successCount")),
                default=0,
            ),
        }

    def _canonical_project_metadata_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized = self._normalize_project_metadata(payload)
        return {
            "always": normalized["always"],
            "requires": normalized["requires"],
            "triggers": normalized["triggers"],
            "tool_tags": normalized["tool_tags"],
            "supersedes": normalized["supersedes"],
            "last_used": normalized["last_used"],
            "success_count": normalized["success_count"],
        }

    def _skill_entries_from_dir(self, base: Path, source: str, *, skip_names: set[str] | None = None) -> list[dict[str, str]]:
        if not base.exists():
            return []
        entries: list[dict[str, str]] = []
        for skill_dir in base.iterdir():
            if not skill_dir.is_dir():
                continue
            skill_file = skill_dir / "SKILL.md"
            if not skill_file.exists():
                continue
            name = skill_dir.name
            if skip_names is not None and name in skip_names:
                continue
            entries.append({"name": name, "path": str(skill_file), "source": source})
        return entries

    def list_skills(self, filter_unavailable: bool = True) -> list[dict[str, str]]:
        """
        List all available skills.

        Args:
            filter_unavailable: If True, filter out skills with unmet requirements.

        Returns:
            List of skill info dicts with 'name', 'path', 'source'.
        """
        skills = self._skill_entries_from_dir(self.workspace_skills, "workspace")
        workspace_names = {entry["name"] for entry in skills}
        if self.builtin_skills and self.builtin_skills.exists():
            skills.extend(
                self._skill_entries_from_dir(self.builtin_skills, "builtin", skip_names=workspace_names)
            )

        if self.disabled_skills:
            skills = [entry for entry in skills if entry["name"] not in self.disabled_skills]

        if filter_unavailable:
            return [skill for skill in skills if self._check_requirements(self._get_skill_meta(skill["name"]))]
        return skills

    def load_skill(self, name: str) -> str | None:
        """
        Load a skill by name.

        Args:
            name: Skill name (directory name).

        Returns:
            Skill content or None if not found.
        """
        roots = [self.workspace_skills]
        if self.builtin_skills:
            roots.append(self.builtin_skills)
        for root in roots:
            path = root / name / "SKILL.md"
            if path.exists():
                return path.read_text(encoding="utf-8")
        return None

    def workspace_skill_name_for_path(self, path: str | Path) -> str | None:
        """Resolve `<workspace>/skills/<name>/SKILL.md` back to its skill name."""
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            candidate = self.workspace / candidate
        try:
            relative = candidate.resolve().relative_to(self.workspace_skills.resolve())
        except ValueError:
            return None
        if len(relative.parts) != 2 or relative.parts[1] != "SKILL.md":
            return None
        return relative.parts[0]

    def _update_workspace_skill_metadata(
        self,
        name: str,
        updater: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> tuple[bool, dict[str, Any]] | None:
        """Apply one metadata update to a workspace skill while preserving unknown keys."""
        skill_path = self.workspace_skills / name / "SKILL.md"
        if not skill_path.exists() or not skill_path.is_file():
            return None

        try:
            raw = skill_path.read_bytes()
            content = raw.decode("utf-8")
        except (OSError, UnicodeDecodeError):
            return None

        updated_content, changed, updated_metadata = self._updated_skill_metadata_content(
            content,
            name=name,
            updater=updater,
        )
        if not changed:
            return False, updated_metadata

        try:
            if b"\r\n" in raw:
                updated_content = updated_content.replace("\n", "\r\n")
            skill_path.write_text(updated_content, encoding="utf-8")
        except OSError:
            return None
        return True, updated_metadata

    def record_skill_usage(
        self,
        name: str,
        *,
        used_on: str | None = None,
        success: bool = False,
    ) -> bool:
        """Best-effort update workspace skill metadata after a real runtime use."""
        result = self._update_workspace_skill_metadata(
            name,
            lambda current: {
                **current,
                "last_used": used_on or current.get("last_used", ""),
                "success_count": int(current.get("success_count") or 0) + (1 if success else 0),
            },
        )
        return bool(result and result[0])

    def record_skill_usage_batch(
        self,
        names: list[str],
        *,
        used_on: str | None = None,
        success: bool = False,
    ) -> list[str]:
        """Update one or more workspace skills, deduping repeated names."""
        updated: list[str] = []
        seen: set[str] = set()
        for name in names:
            if not name or name in seen:
                continue
            seen.add(name)
            if self.record_skill_usage(name, used_on=used_on, success=success):
                updated.append(name)
        return updated

    def set_skill_supersedes(
        self,
        name: str,
        targets: list[str],
    ) -> tuple[bool, list[str]] | None:
        """Add one or more superseded targets to a workspace skill."""
        cleaned_targets = [target.strip() for target in targets if target and target.strip()]
        result = self._update_workspace_skill_metadata(
            name,
            lambda current: {
                **current,
                "supersedes": self._normalize_string_list(
                    [*(current.get("supersedes") or []), *cleaned_targets]
                ),
            },
        )
        if result is None:
            return None
        changed, updated = result
        return changed, list(updated.get("supersedes") or [])

    def remove_skill_supersedes(
        self,
        name: str,
        targets: list[str],
    ) -> tuple[bool, list[str]] | None:
        """Remove one or more superseded targets from a workspace skill."""
        lowered_targets = {target.strip().lower() for target in targets if target and target.strip()}
        result = self._update_workspace_skill_metadata(
            name,
            lambda current: {
                **current,
                "supersedes": [
                    target
                    for target in current.get("supersedes") or []
                    if target.lower() not in lowered_targets
                ],
            },
        )
        if result is None:
            return None
        changed, updated = result
        return changed, list(updated.get("supersedes") or [])

    def clear_skill_supersedes(self, name: str) -> tuple[bool, list[str]] | None:
        """Clear all superseded targets from a workspace skill."""
        result = self._update_workspace_skill_metadata(
            name,
            lambda current: {**current, "supersedes": []},
        )
        if result is None:
            return None
        changed, updated = result
        return changed, list(updated.get("supersedes") or [])

    def load_skills_for_context(self, skill_names: list[str]) -> str:
        """
        Load specific skills for inclusion in agent context.

        Args:
            skill_names: List of skill names to load.

        Returns:
            Formatted skills content.
        """
        parts = [
            f"### Skill: {name}\n\n{self._strip_frontmatter(markdown)}"
            for name in skill_names
            if (markdown := self.load_skill(name))
        ]
        return "\n\n---\n\n".join(parts)

    def build_skills_summary(
        self,
        query: str | None = None,
        *,
        limit: int | None = None,
    ) -> str:
        """
        Build a summary of all skills (name, description, path, availability).

        This is used for progressive loading - the agent can read the full
        skill content using read_file when needed.

        Returns:
            XML-formatted skills summary.
        """
        all_skills = self.list_skill_records(filter_unavailable=False)
        if not all_skills:
            return ""

        selected = self._select_summary_records(all_skills, query=query, limit=limit)
        if not selected:
            return ""

        lines: list[str] = ["<skills>"]
        for entry in selected:
            skill_name = entry["name"]
            available = bool(entry["available"])
            lines.extend(
                [
                    f'  <skill available="{str(available).lower()}">',
                    f"    <name>{_escape_xml(skill_name)}</name>",
                    f"    <description>{_escape_xml(entry['description'])}</description>",
                    f"    <location>{entry['path']}</location>",
                ]
            )
            triggers = entry["project_meta"].get("triggers") or []
            if triggers:
                lines.append(f"    <triggers>{_escape_xml(', '.join(triggers[:6]))}</triggers>")
            tool_tags = entry["project_meta"].get("tool_tags") or []
            if tool_tags:
                lines.append(f"    <tool_tags>{_escape_xml(', '.join(tool_tags[:6]))}</tool_tags>")
            if not available:
                missing = self._get_missing_requirements(entry["project_meta"])
                if missing:
                    lines.append(f"    <requires>{_escape_xml(missing)}</requires>")
            lines.append("  </skill>")
        lines.append("</skills>")
        return "\n".join(lines)

    def _get_missing_requirements(self, skill_meta: dict) -> str:
        """Get a description of missing requirements."""
        requires = skill_meta.get("requires", {})
        required_bins = requires.get("bins", [])
        required_env_vars = requires.get("env", [])
        return ", ".join(
            [f"CLI: {command_name}" for command_name in required_bins if not shutil.which(command_name)]
            + [f"ENV: {env_name}" for env_name in required_env_vars if not os.environ.get(env_name)]
        )

    def _get_skill_description(self, name: str) -> str:
        """Get the description of a skill from its frontmatter."""
        meta = self.get_skill_metadata(name)
        if meta and meta.get("description"):
            return meta["description"]
        return name  # Fallback to skill name

    def _strip_frontmatter(self, content: str) -> str:
        """Remove YAML frontmatter from markdown content."""
        if not content.startswith("---"):
            return content
        match = _STRIP_SKILL_FRONTMATTER.match(content)
        if match:
            return content[match.end():].strip()
        return content

    def _updated_skill_metadata_content(
        self,
        content: str,
        *,
        name: str,
        updater: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> tuple[str, bool, dict[str, Any]]:
        match = _STRIP_SKILL_FRONTMATTER.match(content)
        if match:
            frontmatter_lines = match.group(1).splitlines()
            body = content[match.end():]
        else:
            frontmatter_lines = [f"name: {name}"]
            body = content

        metadata_line_index: int | None = None
        metadata_payload: dict[str, Any] = {}
        for index, line in enumerate(frontmatter_lines):
            if ":" not in line:
                continue
            key, raw_value = line.split(":", 1)
            if key.strip() != "metadata":
                continue
            metadata_line_index = index
            try:
                parsed = json.loads(raw_value.strip())
            except json.JSONDecodeError:
                parsed = {}
            metadata_payload = parsed if isinstance(parsed, dict) else {}
            break

        current = self._canonical_project_metadata_payload(
            self._parse_project_metadata(json.dumps(metadata_payload, ensure_ascii=False))
        )
        updated_meta = self._canonical_project_metadata_payload(updater(dict(current)))

        updated_payload = dict(metadata_payload)
        updated_payload["hahobot"] = updated_meta
        metadata_line = (
            f"metadata: {json.dumps(updated_payload, ensure_ascii=False, separators=(',', ':'))}"
        )

        if metadata_line_index is None:
            frontmatter_lines.append(metadata_line)
        else:
            frontmatter_lines[metadata_line_index] = metadata_line

        frontmatter = "\n".join(frontmatter_lines)
        rendered = f"---\n{frontmatter}\n---\n{body}"
        return rendered, rendered != content, updated_meta

    def _parse_project_metadata(self, raw: str) -> dict:
        """Parse skill metadata JSON from frontmatter across current and legacy keys."""
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}
        if not isinstance(data, dict):
            return {}
        payload = data.get("hahobot")
        if not isinstance(payload, dict):
            payload = data.get("nanobot")
        if not isinstance(payload, dict):
            payload = data.get("openclaw", {})
        return self._normalize_project_metadata(payload if isinstance(payload, dict) else {})

    def _check_requirements(self, skill_meta: dict) -> bool:
        """Check if skill requirements are met (bins, env vars)."""
        requires = skill_meta.get("requires", {})
        required_bins = requires.get("bins", [])
        required_env_vars = requires.get("env", [])
        return all(shutil.which(cmd) for cmd in required_bins) and all(
            os.environ.get(var) for var in required_env_vars
        )

    def _get_skill_meta(self, name: str) -> dict:
        """Get hahobot metadata for a skill (cached in frontmatter)."""
        meta = self.get_skill_metadata(name) or {}
        return self._parse_project_metadata(meta.get("metadata", ""))

    def list_skill_records(self, filter_unavailable: bool = True) -> list[dict[str, Any]]:
        """Return richer skill records used for selection, linting, and summaries."""
        records: list[dict[str, Any]] = []
        for entry in self.list_skills(filter_unavailable=filter_unavailable):
            project_meta = self._get_skill_meta(entry["name"])
            available = self._check_requirements(project_meta)
            records.append({
                **entry,
                "description": self._get_skill_description(entry["name"]),
                "available": available,
                "project_meta": project_meta,
            })
        return records

    def _superseded_names(self, records: list[dict[str, Any]]) -> set[str]:
        available_names = {record["name"] for record in records if record.get("available")}
        superseded: set[str] = set()
        for record in records:
            if not record.get("available"):
                continue
            for target in record["project_meta"].get("supersedes") or []:
                if target in available_names and target != record["name"]:
                    superseded.add(target)
        return superseded

    def _score_skill_record(self, record: dict[str, Any], query: str | None) -> int:
        if not query:
            return 0
        query_tokens = self._tokenize(query)
        if not query_tokens:
            return 0

        name_tokens = self._tokenize(record["name"])
        desc_tokens = self._tokenize(record["description"])
        trigger_tokens = self._tokenize(*record["project_meta"].get("triggers", []))
        tool_tokens = self._tokenize(*record["project_meta"].get("tool_tags", []))

        score = 0
        score += 10 * len(query_tokens & name_tokens)
        score += 6 * len(query_tokens & trigger_tokens)
        score += 4 * len(query_tokens & tool_tokens)
        score += 2 * len(query_tokens & desc_tokens)
        if score > 0:
            score += min(int(record["project_meta"].get("success_count") or 0), 20)
            if record.get("source") == "workspace":
                score += 1
        return score

    def _select_summary_records(
        self,
        records: list[dict[str, Any]],
        *,
        query: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Select the small skill subset worth exposing in the current prompt summary."""
        if limit is None:
            limit = self._SUMMARY_SKILL_LIMIT
        if limit <= 0:
            return []

        visible = [
            record for record in records if record["name"] not in self._superseded_names(records)
        ]
        scored: list[tuple[int, dict[str, Any]]] = [
            (self._score_skill_record(record, query), record) for record in visible
        ]
        scored.sort(key=lambda item: (-item[0], item[1]["name"]))

        if query and any(score > 0 for score, _ in scored):
            selected = [record for score, record in scored if score > 0][:limit]
            if selected:
                return selected
        return [record for _, record in scored[:limit]]

    def lint_skills(self) -> dict[str, Any]:
        """Inspect local skills for supersedes issues and likely overlap drift."""
        records = self.list_skill_records(filter_unavailable=False)
        by_name = {record["name"]: record for record in records}
        superseded = self._superseded_names(records)

        superseded_entries: list[dict[str, Any]] = []
        missing_targets: list[dict[str, Any]] = []
        for record in sorted(records, key=lambda item: item["name"]):
            targets = record["project_meta"].get("supersedes") or []
            if not targets:
                continue
            existing = [target for target in targets if target in by_name and target != record["name"]]
            missing = [target for target in targets if target not in by_name]
            if existing:
                superseded_entries.append({"name": record["name"], "targets": existing})
            if missing:
                missing_targets.append({"name": record["name"], "targets": missing})

        overlaps: list[dict[str, Any]] = []
        visible = sorted(
            (record for record in records if record["name"] not in superseded),
            key=lambda item: item["name"],
        )
        for index, left in enumerate(visible):
            left_triggers = {item.lower() for item in left["project_meta"].get("triggers") or []}
            left_tools = {item.lower() for item in left["project_meta"].get("tool_tags") or []}
            left_tokens = self._tokenize(left["name"], left["description"], *left_triggers)
            for right in visible[index + 1:]:
                right_triggers = {item.lower() for item in right["project_meta"].get("triggers") or []}
                right_tools = {item.lower() for item in right["project_meta"].get("tool_tags") or []}
                right_tokens = self._tokenize(right["name"], right["description"], *right_triggers)
                shared_triggers = sorted(left_triggers & right_triggers)
                shared_tools = sorted(left_tools & right_tools)
                shared_tokens = sorted(left_tokens & right_tokens)
                if len(shared_triggers) >= 2 or (
                    shared_triggers and shared_tools and len(shared_tokens) >= 3
                ):
                    overlaps.append({
                        "left": left["name"],
                        "right": right["name"],
                        "shared_triggers": shared_triggers,
                        "shared_tools": shared_tools,
                    })

        return {
            "total": len(records),
            "visible": len(visible),
            "superseded": sorted(superseded),
            "superseded_by": superseded_entries,
            "missing_supersedes_targets": missing_targets,
            "overlaps": overlaps,
        }

    def get_always_skills(self) -> list[str]:
        """Get skills marked as always=true that meet requirements."""
        return [
            entry["name"]
            for entry in self.list_skills(filter_unavailable=True)
            if (meta := self.get_skill_metadata(entry["name"]) or {})
            and (self._parse_project_metadata(meta.get("metadata", "")).get("always") or meta.get("always"))
        ]

    def get_skill_metadata(self, name: str) -> dict | None:
        """
        Get metadata from a skill's frontmatter.

        Args:
            name: Skill name.

        Returns:
            Metadata dict or None.
        """
        content = self.load_skill(name)
        if not content or not content.startswith("---"):
            return None
        match = _STRIP_SKILL_FRONTMATTER.match(content)
        if not match:
            return None
        metadata: dict[str, str] = {}
        for line in match.group(1).splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            metadata[key.strip()] = value.strip().strip('"\'')
        return metadata
