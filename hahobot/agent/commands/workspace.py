"""Workspace/session inspection commands exposed through AgentLoop."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from hahobot.bus.events import InboundMessage, OutboundMessage
from hahobot.cli.repo_inspector import (
    inspect_repo_diff,
    inspect_repo_status,
    render_repo_diff_text,
    render_repo_status_text,
)
from hahobot.cli.review_runner import run_review
from hahobot.cli.session_compactor import compact_session, render_session_compact_text
from hahobot.cli.session_inspector import (
    export_session_artifact,
    list_session_summaries,
    load_session_detail,
    load_session_export,
    render_session_detail_text,
    render_session_list_text,
)
from hahobot.session.manager import Session

if TYPE_CHECKING:
    from hahobot.agent.loop import AgentLoop


_SESSION_ROUTE_RESET_ALIASES = {"default", "origin", "base"}


class WorkspaceCommandHandler:
    """Expose workspace-local utility commands through the main agent router."""

    def __init__(self, loop: AgentLoop) -> None:
        self.loop = loop

    @staticmethod
    def _response(
        msg: InboundMessage,
        content: str,
        *,
        render_as: str | None = None,
    ) -> OutboundMessage:
        metadata = {"render_as": render_as} if render_as else None
        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=content,
            metadata=metadata,
        )

    @staticmethod
    def _session_usage() -> str:
        return (
            "Usage:\n"
            "/session current\n"
            "/session list\n"
            "/session show [key]\n"
            "/session export [key]\n"
            "/session use <key|default>\n"
            "/session new [name]"
        )

    @staticmethod
    def _repo_usage() -> str:
        return "Usage:\n/repo status\n/repo diff\n/repo diff staged"

    @staticmethod
    def _review_usage() -> str:
        return (
            "Usage:\n"
            "/review\n"
            "/review staged\n"
            "/review base <rev>\n"
            "/review path <repo-path>\n"
            "/review base <rev> path <repo-path>"
        )

    @staticmethod
    def _compact_usage() -> str:
        return "Usage:\n/compact\n/compact <key>"

    @staticmethod
    def _nonempty_token(value: str | None) -> str | None:
        if not isinstance(value, str):
            return None
        cleaned = value.strip()
        return cleaned or None

    def _origin_session_key(self, msg: InboundMessage) -> str:
        metadata = msg.metadata if isinstance(msg.metadata, dict) else {}
        raw = metadata.get("_origin_session_key")
        return self._nonempty_token(raw) or msg.session_key

    def _existing_session_keys(self) -> set[str]:
        return {
            str(item.get("key") or "")
            for item in self.loop.sessions.list_sessions()
            if item.get("key")
        }

    def _scoped_session_key(self, origin_key: str, value: str) -> str:
        cleaned = value.strip()
        lowered = cleaned.lower()
        if lowered in _SESSION_ROUTE_RESET_ALIASES:
            return origin_key
        if ":" in cleaned:
            return cleaned
        return f"{origin_key}:session:{cleaned}"

    def _resolve_lookup_session_key(
        self,
        origin_key: str,
        current_key: str,
        value: str | None,
    ) -> str:
        cleaned = self._nonempty_token(value)
        if cleaned is None:
            return current_key
        lowered = cleaned.lower()
        if lowered in {"current", "active"}:
            return current_key
        if lowered in _SESSION_ROUTE_RESET_ALIASES:
            return origin_key
        if ":" in cleaned:
            return cleaned

        scoped = f"{origin_key}:session:{cleaned}"
        existing = self._existing_session_keys()
        if scoped in existing:
            return scoped
        if cleaned in existing:
            return cleaned
        return scoped

    @staticmethod
    def _generated_session_name() -> str:
        return datetime.now().strftime("%Y%m%d-%H%M%S")

    async def session(self, msg: InboundMessage, session: Session, args: str) -> OutboundMessage:
        """Handle `/session ...` commands for local and gateway chats."""
        parts = args.strip().split(maxsplit=1) if args.strip() else []
        if not parts:
            return self._response(msg, self._session_usage(), render_as="text")

        action = parts[0].lower()
        value = parts[1] if len(parts) > 1 else None
        current_key = session.key
        origin_key = self._origin_session_key(msg)

        if action in {"current", "now"}:
            lines = [f"Current session: {current_key}"]
            route = self.loop.get_session_route(origin_key)
            if origin_key != current_key:
                lines.append(f"Origin chat session: {origin_key}")
            if route and route != origin_key:
                lines.append(f"Current route: {origin_key} -> {route}")
            return self._response(msg, "\n".join(lines), render_as="text")

        if action == "list":
            summaries = list_session_summaries(
                self.loop.sessions,
                include_internal=False,
                cli_only=False,
                limit=20,
            )
            return self._response(msg, render_session_list_text(summaries), render_as="text")

        if action == "show":
            target = self._resolve_lookup_session_key(origin_key, current_key, value)
            detail = load_session_detail(self.loop.sessions, target, limit=10)
            if detail is None:
                return self._response(
                    msg,
                    f"Session not found: {target}\nUse /session list to inspect saved sessions.",
                    render_as="text",
                )
            return self._response(msg, render_session_detail_text(detail), render_as="text")

        if action == "export":
            target = self._resolve_lookup_session_key(origin_key, current_key, value)
            export_data = load_session_export(self.loop.sessions, target)
            if export_data is None:
                return self._response(
                    msg,
                    f"Session not found: {target}\nUse /session list to inspect saved sessions.",
                    render_as="text",
                )
            output_path = export_session_artifact(
                export_data,
                workspace=self.loop.workspace,
                export_format="md",
            )
            return self._response(
                msg,
                f"Exported session: {target}\nPath: {output_path}",
                render_as="text",
            )

        existing = self._existing_session_keys()

        if action == "use":
            target_value = self._nonempty_token(value)
            if target_value is None:
                return self._response(msg, self._session_usage(), render_as="text")
            target = self._scoped_session_key(origin_key, target_value)
            if target == current_key:
                return self._response(msg, f"Already using session: {target}", render_as="text")
            if target != origin_key and target not in existing:
                return self._response(
                    msg,
                    f"Session not found: {target}\nUse /session list to inspect saved sessions.",
                    render_as="text",
                )
            self.loop.set_session_route(origin_key, target)
            if target == origin_key:
                return self._response(
                    msg,
                    f"Switched to default session: {origin_key}",
                    render_as="text",
                )
            return self._response(msg, f"Switched to session: {target}", render_as="text")

        if action == "new":
            if value and value.strip():
                target = self._scoped_session_key(origin_key, value)
            else:
                target = self._scoped_session_key(origin_key, self._generated_session_name())
            if target in existing:
                return self._response(
                    msg,
                    f"Session already exists: {target}\nUse /session use {target} to resume it.",
                    render_as="text",
                )
            target_session = self.loop.sessions.get_or_create(target)
            self.loop.sessions.save(target_session)
            self.loop.set_session_route(origin_key, target)
            return self._response(msg, f"Started new session: {target}", render_as="text")

        return self._response(msg, self._session_usage(), render_as="text")

    def repo(self, msg: InboundMessage, args: str) -> OutboundMessage:
        """Handle `/repo ...` commands."""
        parts = args.strip().split() if args.strip() else []
        if not parts:
            return self._response(msg, self._repo_usage(), render_as="text")

        action = parts[0].lower()
        if action == "status":
            return self._response(
                msg,
                render_repo_status_text(inspect_repo_status(self.loop.workspace)),
                render_as="text",
            )
        if action == "diff":
            staged = len(parts) >= 2 and parts[1].lower() in {"staged", "cached"}
            if len(parts) >= 2 and not staged:
                return self._response(msg, self._repo_usage(), render_as="text")
            return self._response(
                msg,
                render_repo_diff_text(inspect_repo_diff(self.loop.workspace, staged=staged)),
                render_as="text",
            )
        return self._response(msg, self._repo_usage(), render_as="text")

    @staticmethod
    def _parse_review_args(args: str) -> tuple[bool, str | None, str | None] | None:
        if not args.strip():
            return False, None, None

        staged = False
        base = None
        path_filter = None
        tokens = args.strip().split()
        index = 0
        while index < len(tokens):
            token = tokens[index].lower()
            if token == "staged":
                staged = True
                index += 1
                continue
            if token == "base" and index + 1 < len(tokens):
                base = tokens[index + 1]
                index += 2
                continue
            if token == "path" and index + 1 < len(tokens):
                path_filter = tokens[index + 1]
                index += 2
                continue
            return None

        if staged and base:
            return None
        return staged, base, path_filter

    async def review(self, msg: InboundMessage, args: str) -> OutboundMessage:
        """Handle `/review ...` commands without routing through the main agent loop."""
        parsed = self._parse_review_args(args)
        if parsed is None:
            return self._response(msg, self._review_usage(), render_as="text")

        staged, base, path_filter = parsed
        result = await run_review(
            provider=self.loop.provider,
            model=self.loop.model,
            workspace=self.loop.workspace,
            staged=staged,
            base=base,
            path_filter=path_filter,
            retry_mode=self.loop.provider_retry_mode,
        )
        if result.request.error:
            return self._response(msg, result.content, render_as="text")
        return self._response(msg, result.content)

    async def compact(
        self,
        msg: InboundMessage,
        session: Session,
        args: str,
    ) -> OutboundMessage:
        """Handle `/compact [key]` commands."""
        origin_key = self._origin_session_key(msg)
        target = self._resolve_lookup_session_key(origin_key, session.key, args or None)
        if target != session.key and target not in self._existing_session_keys():
            return self._response(
                msg,
                f"Session not found: {target}\nUse /session list to inspect saved sessions.",
                render_as="text",
            )
        target_session = session if target == session.key else self.loop.sessions.get_or_create(target)
        report = await compact_session(target_session, self.loop)
        return self._response(msg, render_session_compact_text(report), render_as="text")
