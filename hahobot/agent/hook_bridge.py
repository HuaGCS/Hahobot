"""Bridge agent lifecycle hooks to external commands."""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from loguru import logger

from hahobot.agent.hook import AgentHook, AgentHookContext
from hahobot.providers.base import LLMResponse, ToolCallRequest

_ASYNC_EVENTS = frozenset({
    "before_iteration",
    "on_stream",
    "on_stream_end",
    "before_execute_tools",
    "after_iteration",
})
_BLOCKABLE_EVENTS = frozenset({"before_iteration", "before_execute_tools"})
_DEFAULT_EVENTS = ("before_iteration", "before_execute_tools", "after_iteration")
_STREAM_EVENTS = frozenset({"on_stream", "on_stream_end"})


class ExternalHookBridgeError(RuntimeError):
    """Base error for external hook bridge failures."""


class ExternalHookBridgeBlockedError(ExternalHookBridgeError):
    """Raised when an external hook explicitly blocks execution."""


ExternalHookBridgeBlocked = ExternalHookBridgeBlockedError


def _json_safe(value: Any) -> Any:
    """Convert hook payload values into JSON-safe structures."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "model_dump") and callable(value.model_dump):
        return _json_safe(value.model_dump())
    return str(value)


def _serialize_tool_call(tool_call: ToolCallRequest) -> dict[str, Any]:
    """Serialize a tool call for external hook payloads."""
    return {
        "id": tool_call.id,
        "name": tool_call.name,
        "arguments": _json_safe(tool_call.arguments),
        "extra_content": _json_safe(tool_call.extra_content),
        "provider_specific_fields": _json_safe(tool_call.provider_specific_fields),
        "function_provider_specific_fields": _json_safe(
            tool_call.function_provider_specific_fields
        ),
    }


def _serialize_response(response: LLMResponse | None) -> dict[str, Any] | None:
    """Serialize the current LLM response snapshot."""
    if response is None:
        return None
    return {
        "content": response.content,
        "finish_reason": response.finish_reason,
        "usage": _json_safe(response.usage),
        "retry_after": response.retry_after,
        "error_should_retry": response.error_should_retry,
        "error_status_code": response.error_status_code,
        "error_kind": response.error_kind,
        "error_type": response.error_type,
        "error_code": response.error_code,
        "error_retry_after_s": response.error_retry_after_s,
        "reasoning_content": response.reasoning_content,
        "thinking_blocks": _json_safe(response.thinking_blocks),
        "tool_calls": [_serialize_tool_call(call) for call in response.tool_calls],
    }


class ExternalHookBridge(AgentHook):
    """Run external commands for selected lifecycle hook events.

    The command receives one JSON object on stdin with this shape:

    ``{"schema_version": 1, "event": "...", "context": {...}}``

    Default events are non-streaming to avoid forcing provider streaming. To opt into
    streamed deltas, include ``on_stream`` and/or ``on_stream_end`` in ``events``.

    The command may explicitly block ``before_iteration`` or ``before_execute_tools`` by:

    - exiting with code ``2``
    - or printing JSON with ``{"continue": false, "message": "..."}``

    Unexpected command failures are fail-open by default and only logged.
    """

    def __init__(
        self,
        command: str | Sequence[str],
        *,
        cwd: str | Path | None = None,
        env: Mapping[str, str] | None = None,
        events: Iterable[str] | None = None,
        timeout_s: float = 15.0,
        fail_open: bool = True,
    ) -> None:
        super().__init__(reraise=True)
        self._command, self._shell = self._normalize_command(command)
        self._cwd = str(Path(cwd).expanduser().resolve()) if cwd is not None else None
        self._env = {str(key): str(value) for key, value in (env or {}).items()}
        self._events = self._normalize_events(events)
        self._timeout_s = timeout_s
        self._fail_open = fail_open

    @staticmethod
    def _normalize_command(command: str | Sequence[str]) -> tuple[str | tuple[str, ...], bool]:
        if isinstance(command, str):
            normalized = command.strip()
            if not normalized:
                raise ValueError("External hook command cannot be empty.")
            return normalized, True

        normalized = tuple(str(part) for part in command if str(part))
        if not normalized:
            raise ValueError("External hook command cannot be empty.")
        return normalized, False

    @staticmethod
    def _normalize_events(events: Iterable[str] | None) -> frozenset[str]:
        names = tuple(dict.fromkeys(events or _DEFAULT_EVENTS))
        unknown = sorted(set(names) - _ASYNC_EVENTS)
        if unknown:
            joined = ", ".join(unknown)
            raise ValueError(f"Unsupported external hook event(s): {joined}")
        return frozenset(names)

    def wants_streaming(self) -> bool:
        return bool(self._events & _STREAM_EVENTS)

    async def before_iteration(self, context: AgentHookContext) -> None:
        await self._dispatch("before_iteration", context)

    async def on_stream(self, context: AgentHookContext, delta: str) -> None:
        await self._dispatch("on_stream", context, delta=delta)

    async def on_stream_end(self, context: AgentHookContext, *, resuming: bool) -> None:
        await self._dispatch("on_stream_end", context, resuming=resuming)

    async def before_execute_tools(self, context: AgentHookContext) -> None:
        await self._dispatch("before_execute_tools", context)

    async def after_iteration(self, context: AgentHookContext) -> None:
        await self._dispatch("after_iteration", context)

    async def _dispatch(
        self,
        event: str,
        context: AgentHookContext,
        **extra: Any,
    ) -> None:
        if event not in self._events:
            return
        try:
            result, stderr = await self._invoke(event, context, **extra)
            block_message = self._block_message(result, stderr)
            if block_message is None:
                return
            if event in _BLOCKABLE_EVENTS:
                raise ExternalHookBridgeBlocked(block_message)
            logger.warning(
                "Ignoring unsupported block request from external hook on {}: {}",
                event,
                block_message,
            )
        except ExternalHookBridgeBlocked:
            raise
        except Exception as exc:
            if self._fail_open:
                logger.warning(
                    "External hook bridge ignored {} failure: {}",
                    event,
                    exc,
                )
                return
            raise

    async def _invoke(
        self,
        event: str,
        context: AgentHookContext,
        **extra: Any,
    ) -> tuple[dict[str, Any] | None, str]:
        payload = self._payload(event, context, **extra)
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        try:
            completed = self._run_subprocess(event, context, encoded)
        except subprocess.TimeoutExpired as exc:
            raise ExternalHookBridgeError(
                f"External hook timed out after {self._timeout_s:g}s."
            ) from exc
        except OSError as exc:
            raise ExternalHookBridgeError(f"External hook failed to start: {exc}") from exc

        stdout = completed.stdout or b""
        stderr = completed.stderr or b""
        stdout_text = stdout.decode("utf-8", errors="replace").strip()
        stderr_text = stderr.decode("utf-8", errors="replace").strip()
        if completed.returncode == 2:
            return {"continue": False, "message": stdout_text or stderr_text}, stderr_text
        if completed.returncode != 0:
            detail = stderr_text or stdout_text or f"exit code {completed.returncode}"
            raise ExternalHookBridgeError(f"External hook failed with {detail}")
        if not stdout_text:
            return None, stderr_text
        try:
            result = json.loads(stdout_text)
        except json.JSONDecodeError as exc:
            raise ExternalHookBridgeError(
                "External hook stdout must be valid JSON when it is not empty."
            ) from exc
        if not isinstance(result, dict):
            raise ExternalHookBridgeError("External hook stdout JSON must be an object.")
        return result, stderr_text

    def _run_subprocess(
        self,
        event: str,
        context: AgentHookContext,
        encoded: bytes,
    ) -> subprocess.CompletedProcess[bytes]:
        env = os.environ.copy()
        env.update(self._env)
        env["HAHOBOT_HOOK_PROTOCOL"] = "1"
        env["HAHOBOT_HOOK_EVENT"] = event
        if context.session_key:
            env["HAHOBOT_HOOK_SESSION_KEY"] = str(context.session_key)
        if context.workspace is not None:
            env["HAHOBOT_HOOK_WORKSPACE"] = str(context.workspace)
        if context.model:
            env["HAHOBOT_HOOK_MODEL"] = str(context.model)

        return subprocess.run(
            self._command,
            input=encoded,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=self._cwd,
            env=env,
            shell=self._shell,
            timeout=self._timeout_s,
            check=False,
        )

    @staticmethod
    def _block_message(result: dict[str, Any] | None, stderr_text: str) -> str | None:
        if not result:
            return None
        allowed = result.get("continue")
        if allowed is not False:
            return None
        message = result.get("message") or result.get("reason") or stderr_text
        return str(message or "Blocked by external hook.")

    def _payload(
        self,
        event: str,
        context: AgentHookContext,
        **extra: Any,
    ) -> dict[str, Any]:
        payload = {
            "schema_version": 1,
            "event": event,
            "context": {
                "iteration": context.iteration,
                "workspace": str(context.workspace) if context.workspace is not None else None,
                "session_key": (
                    str(context.session_key) if context.session_key is not None else None
                ),
                "model": str(context.model) if context.model is not None else None,
                "messages": _json_safe(context.messages),
                "request_messages": _json_safe(context.request_messages),
                "response": _serialize_response(context.response),
                "usage": _json_safe(context.usage),
                "tool_calls": [_serialize_tool_call(call) for call in context.tool_calls],
                "tool_results": _json_safe(context.tool_results),
                "tool_events": _json_safe(context.tool_events),
                "final_content": context.final_content,
                "stop_reason": context.stop_reason,
                "error": context.error,
            },
        }
        if extra:
            payload["data"] = _json_safe(extra)
        return payload
