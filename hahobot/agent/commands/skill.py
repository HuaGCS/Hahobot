"""Skill command helpers for AgentLoop."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
from loguru import logger

from hahobot.agent.i18n import text
from hahobot.agent.working_checkpoint import (
    latest_user_goal,
    normalize_working_checkpoint,
    tool_names,
)
from hahobot.bus.events import InboundMessage, OutboundMessage
from hahobot.utils.helpers import ensure_dir

if TYPE_CHECKING:
    from hahobot.agent.loop import AgentLoop
    from hahobot.session.manager import Session


class SkillCommandHandler:
    """Encapsulates `/skill` subcommand behavior for AgentLoop."""

    _SKILL_NAME_LIMIT = 64
    _SUMMARY_LIMIT = 240

    def __init__(self, loop: AgentLoop) -> None:
        self.loop = loop

    @staticmethod
    def _response(msg: InboundMessage, content: str) -> OutboundMessage:
        return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content=content)

    @staticmethod
    def _decode_subprocess_output(data: bytes) -> str:
        """Decode subprocess output conservatively for CLI surfacing."""
        return data.decode("utf-8", errors="replace").strip()

    def _is_clawhub_network_error(self, output: str) -> bool:
        lowered = output.lower()
        return any(marker in lowered for marker in self.loop._CLAWHUB_NETWORK_ERROR_MARKERS)

    def _format_clawhub_error(self, language: str, code: int, output: str) -> str:
        if output and self._is_clawhub_network_error(output):
            return "\n\n".join([text(language, "skill_command_network_failed"), output])
        return output or text(language, "skill_command_failed", code=code)

    @staticmethod
    def _clawhub_search_headers(language: str) -> dict[str, str]:
        accept_language = "zh-CN,zh;q=0.9,en;q=0.8" if language.startswith("zh") else "en-US,en;q=0.9"
        return {
            "accept": "*/*",
            "accept-language": accept_language,
            "origin": "https://skillhub.tencent.com",
            "referer": "https://skillhub.tencent.com/",
        }

    def _format_clawhub_search_results(
        self,
        language: str,
        query: str,
        skills: list[dict[str, Any]],
        total: int,
    ) -> str:
        blocks = [
            text(
                language,
                "skill_search_results_header",
                query=query,
                total=total,
                count=len(skills),
            )
        ]
        description_key = "description_zh" if language.startswith("zh") else "description"
        for index, skill in enumerate(skills, start=1):
            name = str(skill.get("name") or skill.get("slug") or f"skill-{index}")
            slug = str(skill.get("slug") or "-")
            owner = str(skill.get("ownerName") or "-")
            installs = str(skill.get("installs") or 0)
            stars = str(skill.get("stars") or 0)
            version = str(skill.get("version") or "-")
            description = str(
                skill.get(description_key) or skill.get("description") or skill.get("description_zh") or ""
            ).strip()
            homepage = str(skill.get("homepage") or "").strip()
            lines = [
                f"{index}. {name}",
                text(
                    language,
                    "skill_search_result_meta",
                    slug=slug,
                    owner=owner,
                    installs=installs,
                    stars=stars,
                    version=version,
                ),
            ]
            if description:
                lines.append(description)
            if homepage:
                lines.append(homepage)
            blocks.append("\n".join(lines))
        return "\n\n".join(blocks)

    async def _search_clawhub(
        self,
        language: str,
        query: str,
    ) -> tuple[int, str]:
        params = {
            "page": "1",
            "pageSize": str(self.loop._CLAWHUB_SEARCH_LIMIT),
            "sortBy": "score",
            "order": "desc",
            "keyword": query,
        }
        try:
            async with httpx.AsyncClient(
                proxy=self.loop.web_proxy,
                follow_redirects=True,
                timeout=self.loop._CLAWHUB_SEARCH_TIMEOUT_SECONDS,
            ) as client:
                response = await client.get(
                    self.loop._CLAWHUB_SEARCH_API_URL,
                    params=params,
                    headers=self._clawhub_search_headers(language),
                )
                response.raise_for_status()
        except httpx.TimeoutException:
            return 124, text(language, "skill_search_timeout")
        except httpx.HTTPStatusError as exc:
            details = exc.response.text.strip()
            message = text(language, "skill_search_failed_status", status=exc.response.status_code)
            return exc.response.status_code, "\n\n".join(part for part in [message, details] if part)
        except httpx.RequestError as exc:
            return 1, "\n\n".join([text(language, "skill_search_request_failed"), str(exc)])

        try:
            payload = response.json()
        except ValueError:
            return 1, text(language, "skill_search_invalid_response")

        if not isinstance(payload, dict):
            return 1, text(language, "skill_search_invalid_response")

        if payload.get("code") != 0:
            details = str(payload.get("message") or "").strip()
            return 1, "\n\n".join(
                part for part in [text(language, "skill_search_failed"), details] if part
            )

        data = payload.get("data")
        if not isinstance(data, dict):
            return 1, text(language, "skill_search_invalid_response")

        skills = data.get("skills")
        if not isinstance(skills, list):
            return 1, text(language, "skill_search_invalid_response")

        total = data.get("total")
        if not isinstance(total, int):
            total = len(skills)

        if not skills:
            return 0, ""

        return 0, self._format_clawhub_search_results(language, query, skills, total)

    def _clawhub_env(self) -> dict[str, str]:
        """Configure npm so ClawHub fails fast and uses a writable cache directory."""
        env = os.environ.copy()
        env.setdefault("NO_COLOR", "1")
        env.setdefault("FORCE_COLOR", "0")
        env.setdefault("npm_config_cache", str(ensure_dir(self.loop._clawhub_npm_cache_dir)))
        env.setdefault("npm_config_update_notifier", "false")
        env.setdefault("npm_config_audit", "false")
        env.setdefault("npm_config_fund", "false")
        env.setdefault("npm_config_fetch_retries", "0")
        env.setdefault("npm_config_fetch_timeout", "5000")
        env.setdefault("npm_config_fetch_retry_mintimeout", "1000")
        env.setdefault("npm_config_fetch_retry_maxtimeout", "5000")
        return env

    def _is_clawhub_cache_error(self, output: str) -> bool:
        lowered = output.lower()
        return any(marker in lowered for marker in self.loop._CLAWHUB_CACHE_ERROR_MARKERS) and (
            "_npx/" in lowered or "_npx\\" in lowered
        )

    @staticmethod
    def _clear_clawhub_exec_cache(env: dict[str, str]) -> None:
        """Clear npm's temporary exec installs without wiping the shared tarball cache."""
        cache_root = env.get("npm_config_cache")
        if not cache_root:
            return
        shutil.rmtree(Path(cache_root) / "_npx", ignore_errors=True)

    async def _run_clawhub_once(
        self,
        npx: str,
        env: dict[str, str],
        *args: str,
        timeout_seconds: int | None = None,
    ) -> tuple[int, str]:
        """Run one ClawHub subprocess attempt and return (exit_code, combined_output)."""
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                npx,
                "--yes",
                "clawhub@latest",
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout_seconds or self.loop._CLAWHUB_TIMEOUT_SECONDS,
            )
        except FileNotFoundError:
            raise
        except asyncio.TimeoutError:
            if proc is not None and proc.returncode is None:
                proc.kill()
                await proc.communicate()
            raise
        except asyncio.CancelledError:
            if proc is not None and proc.returncode is None:
                proc.kill()
                await proc.communicate()
            raise

        output_parts = [
            self._decode_subprocess_output(stdout),
            self._decode_subprocess_output(stderr),
        ]
        output = "\n".join(part for part in output_parts if part).strip()
        return proc.returncode or 0, output

    @staticmethod
    def _clawhub_args(workspace: str, *args: str) -> tuple[str, ...]:
        """Build ClawHub CLI args with global options first for consistent parsing."""
        return ("--workdir", workspace, "--no-input", *args)

    @staticmethod
    def _is_valid_skill_slug(slug: str) -> bool:
        """Validate a workspace skill slug for local install/remove operations."""
        return bool(slug) and slug not in {".", ".."} and "/" not in slug and "\\" not in slug

    def _prune_clawhub_lockfile(self, slug: str) -> bool:
        """Best-effort removal of a skill entry from the local ClawHub lockfile."""
        lock_path = self.loop.workspace / ".clawhub" / "lock.json"
        if not lock_path.exists():
            return False

        data = json.loads(lock_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return False

        changed = False
        skills = data.get("skills")
        if isinstance(skills, dict) and slug in skills:
            del skills[slug]
            changed = True
        elif isinstance(skills, list):
            filtered = [
                item
                for item in skills
                if not (
                    item == slug
                    or (isinstance(item, dict) and (item.get("slug") == slug or item.get("name") == slug))
                )
            ]
            if len(filtered) != len(skills):
                data["skills"] = filtered
                changed = True

        if changed:
            lock_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return changed

    async def _run_clawhub(
        self, language: str, *args: str, timeout_seconds: int | None = None,
    ) -> tuple[int, str]:
        """Run the ClawHub CLI and return (exit_code, combined_output)."""
        npx = shutil.which("npx")
        if not npx:
            return 127, text(language, "skill_npx_missing")

        env = self._clawhub_env()

        try:
            async with self.loop._clawhub_lock:
                code, output = await self._run_clawhub_once(
                    npx,
                    env,
                    *args,
                    timeout_seconds=timeout_seconds,
                )
                if code != 0 and self._is_clawhub_cache_error(output):
                    logger.warning(
                        "Retrying ClawHub command after clearing npm exec cache at {}",
                        env["npm_config_cache"],
                    )
                    self._clear_clawhub_exec_cache(env)
                    code, output = await self._run_clawhub_once(
                        npx,
                        env,
                        *args,
                        timeout_seconds=timeout_seconds,
                    )
        except FileNotFoundError:
            return 127, text(language, "skill_npx_missing")
        except asyncio.TimeoutError:
            return 124, text(language, "skill_command_timeout")
        except asyncio.CancelledError:
            raise
        return code, output

    def _format_skill_command_success(
        self,
        language: str,
        subcommand: str,
        output: str,
        *,
        include_workspace_note: bool = False,
    ) -> str:
        notes: list[str] = []
        if output:
            notes.append(output)
        if include_workspace_note:
            notes.append(text(language, "skill_applied_to_workspace", workspace=self.loop.workspace))
        return "\n\n".join(notes) if notes else text(language, "skill_command_completed", command=subcommand)

    @classmethod
    def _normalize_skill_slug(cls, raw: str) -> str:
        normalized = re.sub(r"[^a-z0-9]+", "-", (raw or "").strip().lower())
        normalized = re.sub(r"-{2,}", "-", normalized).strip("-")
        return normalized[: cls._SKILL_NAME_LIMIT].strip("-")

    @staticmethod
    def _skill_title(slug: str) -> str:
        return " ".join(part.capitalize() for part in slug.split("-") if part)

    @classmethod
    def _trim_preview(cls, value: str | None) -> str:
        normalized = " ".join((value or "").split())
        if len(normalized) <= cls._SUMMARY_LIMIT:
            return normalized
        return normalized[: cls._SUMMARY_LIMIT - 3].rstrip() + "..."

    def _latest_role_preview(self, session: Session, role: str) -> str:
        for message in reversed(session.messages):
            if message.get("role") != role:
                continue
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                return self._trim_preview(content)
        return ""

    def _recent_tool_names(self, session: Session) -> list[str]:
        collected: list[str] = []
        seen: set[str] = set()
        for message in reversed(session.messages[-20:]):
            content_name = str(message.get("name") or "").strip()
            if message.get("role") == "tool" and content_name and content_name not in seen:
                seen.add(content_name)
                collected.append(content_name)
            tool_calls = message.get("tool_calls")
            if isinstance(tool_calls, list):
                for name in tool_names(tool_calls):
                    if name and name not in seen:
                        seen.add(name)
                        collected.append(name)
            if len(collected) >= 6:
                break
        collected.reverse()
        return collected[:6]

    def _derive_skill_description(
        self,
        *,
        goal: str,
        brief: str,
    ) -> str:
        subject = self._trim_preview(brief or goal or "repeat the recent workspace workflow")
        return (
            f"Use when you need to {subject}. Gather current workspace evidence first, "
            "follow the saved workflow, and validate before finishing."
        )

    def _derive_skill_markdown(
        self,
        *,
        slug: str,
        session: Session,
        brief: str,
    ) -> str:
        checkpoint = normalize_working_checkpoint(session.metadata.get("working_checkpoint")) or {}
        goal = str(checkpoint.get("goal") or "") or latest_user_goal(session.messages)
        goal = self._trim_preview(goal or brief or "repeat the recent workspace workflow")
        current_step = self._trim_preview(str(checkpoint.get("current_step") or "")) or (
            "Inspect the current workspace state before making changes."
        )
        next_step = self._trim_preview(str(checkpoint.get("next_step") or "")) or (
            "Run the narrowest verification available and confirm the user-visible result."
        )
        response_preview = self._trim_preview(str(checkpoint.get("response_preview") or ""))
        assistant_preview = self._latest_role_preview(session, "assistant")
        tool_list = list(checkpoint.get("recent_tools") or []) or self._recent_tool_names(session)
        tool_line = ", ".join(tool_list) if tool_list else "TODO: capture the key tools or commands for this flow"
        title = self._skill_title(slug) or slug
        today = datetime.now(tz=UTC).date().isoformat()
        description = self._derive_skill_description(goal=goal, brief=brief)
        summary_line = response_preview or assistant_preview or (
            "TODO: replace this with a tighter summary after one more successful run."
        )
        focus_line = self._trim_preview(brief)
        lines = [
            "---",
            f"name: {slug}",
            f"description: {json.dumps(description, ensure_ascii=False)}",
            "---",
            "",
            f"# {title}",
            "",
            "## When to Use This Skill",
            f"- Repeat this workspace-local workflow for: {goal}",
            "- Use it when the current task matches this pattern and you want a concrete checklist.",
            "- Do not use it blindly if the repo structure, tooling, or runtime assumptions have changed.",
        ]
        if focus_line:
            lines.append(f"- Requested draft focus: {focus_line}")
        lines.extend(
            [
                "",
                "## First Checks",
                "- Restate the goal in current repo terms before touching files.",
                "- Inspect the decisive files, config, logs, routes, or diffs first instead of relying on stale chat context.",
                "- Confirm the current workspace still matches the assumptions captured below.",
                "",
                "## Workflow",
                "1. Gather the narrowest current evidence for the task before changing anything.",
                f"2. Start with these tools or command families when relevant: {tool_line}.",
                f"3. Keep the active step explicit: {current_step}",
                "4. After each meaningful change, re-read the touched files or outputs instead of assuming the edit landed correctly.",
                f"5. Finish by validating the likely next step: {next_step}",
                "",
                "## Validation",
                "- Run the narrowest available verification for the touched scope.",
                "- Confirm the final result against the original goal, not just command success.",
                "- If evidence diverges from the saved pattern, stop and update this skill instead of forcing the old flow.",
                "",
                "## Known Pitfalls",
                "- Stale chat context can be wrong; trust current workspace state first.",
                "- Avoid broad edits before the decisive file, route, config, or log source is proven.",
                "- Keep this skill narrow; split unrelated variations into separate skills.",
                "",
                "## Draft Notes",
                f"- Derived from session: {session.key}",
                f"- Source goal: {goal}",
                f"- Recent tool pattern: {tool_line}",
                f"- Recent completion summary: {summary_line}",
                f"- Last review date: {today}",
            ]
        )
        return "\n".join(lines) + "\n"

    async def _run_skill_clawhub_command(
        self,
        msg: InboundMessage,
        language: str,
        subcommand: str,
        *args: str,
        timeout_seconds: int | None = None,
        include_workspace_note: bool = False,
    ) -> OutboundMessage:
        code, output = await self._run_clawhub(
            language,
            *self._clawhub_args(str(self.loop.workspace), *args),
            timeout_seconds=timeout_seconds,
        )
        if code != 0:
            return self._response(msg, self._format_clawhub_error(language, code, output))
        return self._response(
            msg,
            self._format_skill_command_success(
                language,
                subcommand,
                output,
                include_workspace_note=include_workspace_note,
            ),
        )

    async def search(self, msg: InboundMessage, language: str, query: str) -> OutboundMessage:
        code, output = await self._search_clawhub(language, query)
        if code != 0:
            return self._response(msg, output or text(language, "skill_search_failed"))
        if not output:
            return self._response(msg, text(language, "skill_search_no_results", query=query))
        return self._response(msg, output)

    async def install(self, msg: InboundMessage, language: str, slug: str) -> OutboundMessage:
        return await self._run_skill_clawhub_command(
            msg,
            language,
            "install",
            "install",
            slug,
            timeout_seconds=self.loop._CLAWHUB_INSTALL_TIMEOUT_SECONDS,
            include_workspace_note=True,
        )

    async def uninstall(self, msg: InboundMessage, language: str, slug: str) -> OutboundMessage:
        if not self._is_valid_skill_slug(slug):
            return self._response(msg, text(language, "skill_invalid_slug", slug=slug))

        skill_dir = self.loop.workspace / "skills" / slug
        if not skill_dir.is_dir():
            return self._response(
                msg,
                text(language, "skill_uninstall_not_found", slug=slug, path=skill_dir),
            )

        try:
            shutil.rmtree(skill_dir)
        except OSError:
            logger.exception("Failed to remove workspace skill {}", skill_dir)
            return self._response(
                msg,
                text(language, "skill_uninstall_failed", slug=slug, path=skill_dir),
            )

        notes = [text(language, "skill_uninstalled_local", slug=slug, path=skill_dir)]
        try:
            if self._prune_clawhub_lockfile(slug):
                notes.append(
                    text(
                        language,
                        "skill_lockfile_pruned",
                        path=self.loop.workspace / ".clawhub" / "lock.json",
                    )
                )
        except (OSError, ValueError, TypeError):
            logger.exception("Failed to prune ClawHub lockfile for {}", slug)
            notes.append(
                text(
                    language,
                    "skill_lockfile_cleanup_failed",
                    path=self.loop.workspace / ".clawhub" / "lock.json",
                )
            )

        return self._response(msg, "\n\n".join(notes))

    async def derive(
        self,
        msg: InboundMessage,
        session: Session,
        language: str,
        raw_name: str,
        brief: str = "",
        *,
        force: bool = False,
    ) -> OutboundMessage:
        slug = self._normalize_skill_slug(raw_name)
        if not slug:
            return self._response(msg, text(language, "skill_derive_invalid_name", name=raw_name))

        skill_dir = self.loop.workspace / "skills" / slug
        skill_path = skill_dir / "SKILL.md"
        existed_before = skill_path.exists()
        if existed_before:
            if not force:
                return self._response(
                    msg,
                    text(language, "skill_derive_exists", slug=slug, path=skill_path),
                )

        markdown = self._derive_skill_markdown(
            slug=slug,
            session=session,
            brief=brief,
        )
        try:
            skill_dir.mkdir(parents=True, exist_ok=True)
            skill_path.write_text(markdown, encoding="utf-8")
        except OSError:
            logger.exception("Failed to write derived skill draft {}", skill_path)
            return self._response(
                msg,
                text(language, "skill_derive_failed", slug=slug, path=skill_path),
            )

        return self._response(
            msg,
            "\n\n".join(
                [
                    text(
                        language,
                        "skill_derive_overwritten" if force and existed_before else "skill_derive_created",
                        slug=slug,
                        path=skill_path,
                    ),
                    text(language, "skill_derive_session_note", session=session.key),
                    text(language, "skill_derive_review_hint"),
                ]
            ),
        )

    async def list(self, msg: InboundMessage, language: str) -> OutboundMessage:
        return await self._run_skill_clawhub_command(msg, language, "list", "list")

    async def update(self, msg: InboundMessage, language: str) -> OutboundMessage:
        return await self._run_skill_clawhub_command(
            msg,
            language,
            "update",
            "update",
            "--all",
            timeout_seconds=self.loop._CLAWHUB_INSTALL_TIMEOUT_SECONDS,
            include_workspace_note=True,
        )
