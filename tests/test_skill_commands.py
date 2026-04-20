"""Tests for /skill slash command integration."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from hahobot.bus.events import InboundMessage


def _make_loop(workspace: Path):
    """Create an AgentLoop with a real workspace and lightweight mocks."""
    from hahobot.agent.loop import AgentLoop
    from hahobot.bus.queue import MessageBus

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"

    with patch("hahobot.agent.loop.SubagentManager"):
        loop = AgentLoop(bus=bus, provider=provider, workspace=workspace)
    return loop


class _FakeProcess:
    def __init__(self, *, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self._stdout = stdout.encode("utf-8")
        self._stderr = stderr.encode("utf-8")
        self.killed = False

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, self._stderr

    def kill(self) -> None:
        self.killed = True


class _FakeAsyncClient:
    def __init__(self, *, response: httpx.Response | None = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls: list[dict[str, object]] = []

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False

    async def get(self, url: str, *, params: dict[str, str] | None = None, headers: dict[str, str] | None = None):
        self.calls.append({"url": url, "params": params, "headers": headers})
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


@pytest.mark.asyncio
async def test_skill_search_uses_registry_api(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)
    request = httpx.Request("GET", "https://lightmake.site/api/skills")
    response = httpx.Response(
        200,
        request=request,
        json={
            "code": 0,
            "data": {
                "skills": [
                    {
                        "name": "News Aggregator Skill",
                        "slug": "news-aggregator-skill",
                        "ownerName": "cclank",
                        "installs": 667,
                        "stars": 19,
                        "version": "0.1.0",
                        "description": "Fetches and analyzes real-time news.",
                        "description_zh": "抓取并分析实时新闻。",
                        "homepage": "https://clawhub.ai/cclank/news-aggregator-skill",
                    }
                ],
                "total": 42,
            },
            "message": "success",
        },
    )
    client = _FakeAsyncClient(response=response)
    create_proc = AsyncMock()

    with patch("hahobot.agent.commands.skill.httpx.AsyncClient", return_value=client), \
         patch("hahobot.agent.commands.skill.asyncio.create_subprocess_exec", create_proc):
        response = await loop._process_message(
            InboundMessage(channel="cli", sender_id="user", chat_id="direct", content="/skill search web scraping")
        )

    assert response is not None
    assert 'Found 42 skills for "web scraping"' in response.content
    assert "slug: news-aggregator-skill | owner: cclank | installs: 667 | stars: 19 | version: 0.1.0" in response.content
    assert "https://clawhub.ai/cclank/news-aggregator-skill" in response.content
    assert create_proc.await_count == 0
    assert client.calls == [
        {
            "url": "https://lightmake.site/api/skills",
            "params": {
                "page": "1",
                "pageSize": "5",
                "sortBy": "score",
                "order": "desc",
                "keyword": "web scraping",
            },
            "headers": {
                "accept": "*/*",
                "accept-language": "en-US,en;q=0.9",
                "origin": "https://skillhub.tencent.com",
                "referer": "https://skillhub.tencent.com/",
            },
        }
    ]


@pytest.mark.asyncio
async def test_skill_retries_after_clearing_corrupted_npx_cache(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)
    broken_proc = _FakeProcess(
        returncode=1,
        stderr=(
            "node:internal/modules/esm/resolve:201\n"
            "Error: Cannot find package "
            "'/tmp/hahobot-npm-cache/_npx/a92a6dbcf543fba6/node_modules/log-symbols/index.js' "
            "imported from "
            "'/tmp/hahobot-npm-cache/_npx/a92a6dbcf543fba6/node_modules/ora/index.js'\n"
            "code: 'ERR_MODULE_NOT_FOUND'"
        ),
    )
    recovered_proc = _FakeProcess(stdout="demo-skill")
    create_proc = AsyncMock(side_effect=[broken_proc, recovered_proc])

    with patch("hahobot.agent.commands.skill.shutil.which", return_value="/usr/bin/npx"), \
         patch("hahobot.agent.commands.skill.asyncio.create_subprocess_exec", create_proc), \
         patch("hahobot.agent.commands.skill.shutil.rmtree") as remove_tree:
        response = await loop._process_message(
            InboundMessage(channel="cli", sender_id="user", chat_id="direct", content="/skill list")
        )

    assert response is not None
    assert response.content == "demo-skill"
    assert create_proc.await_count == 2
    env = create_proc.await_args_list[0].kwargs["env"]
    remove_tree.assert_called_once_with(Path(env["npm_config_cache"]) / "_npx", ignore_errors=True)


@pytest.mark.asyncio
async def test_skill_search_surfaces_registry_request_errors(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)
    request = httpx.Request("GET", "https://lightmake.site/api/skills")
    client = _FakeAsyncClient(
        error=httpx.ConnectError(
            "temporary failure in name resolution",
            request=request,
        )
    )
    create_proc = AsyncMock()

    with patch("hahobot.agent.commands.skill.httpx.AsyncClient", return_value=client), \
         patch("hahobot.agent.commands.skill.asyncio.create_subprocess_exec", create_proc):
        response = await loop._process_message(
            InboundMessage(channel="cli", sender_id="user", chat_id="direct", content="/skill search test")
        )

    assert response is not None
    assert "ClawHub search request failed" in response.content
    assert "temporary failure in name resolution" in response.content
    assert create_proc.await_count == 0


@pytest.mark.asyncio
async def test_skill_search_empty_output_returns_no_results(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)
    request = httpx.Request("GET", "https://lightmake.site/api/skills")
    response = httpx.Response(
        200,
        request=request,
        json={"code": 0, "data": {"skills": [], "total": 0}, "message": "success"},
    )
    client = _FakeAsyncClient(response=response)
    create_proc = AsyncMock()

    with patch("hahobot.agent.commands.skill.httpx.AsyncClient", return_value=client), \
         patch("hahobot.agent.commands.skill.asyncio.create_subprocess_exec", create_proc):
        response = await loop._process_message(
            InboundMessage(
                channel="cli",
                sender_id="user",
                chat_id="direct",
                content="/skill search selfimprovingagent",
            )
        )

    assert response is not None
    assert 'No skills found for "selfimprovingagent"' in response.content
    assert create_proc.await_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("command", "expected_args", "expected_output"),
    [
        (
            "/skill install demo-skill",
            ("install", "demo-skill"),
            "Installed demo-skill",
        ),
        (
            "/skill list",
            ("list",),
            "demo-skill",
        ),
        (
            "/skill update",
            ("update", "--all"),
            "Updated 1 skill",
        ),
    ],
)
async def test_skill_commands_use_active_workspace(
    tmp_path: Path, command: str, expected_args: tuple[str, ...], expected_output: str,
) -> None:
    loop = _make_loop(tmp_path)
    proc = _FakeProcess(stdout=expected_output)
    create_proc = AsyncMock(return_value=proc)

    with patch("hahobot.agent.commands.skill.shutil.which", return_value="/usr/bin/npx"), \
         patch("hahobot.agent.commands.skill.asyncio.create_subprocess_exec", create_proc):
        response = await loop._process_message(
            InboundMessage(channel="cli", sender_id="user", chat_id="direct", content=command)
        )

    assert response is not None
    assert expected_output in response.content
    args = create_proc.await_args.args
    assert args[:3] == ("/usr/bin/npx", "--yes", "clawhub@latest")
    assert args[3:] == ("--workdir", str(tmp_path), "--no-input", *expected_args)
    if command != "/skill list":
        assert f"Applied to workspace: {tmp_path}" in response.content


@pytest.mark.asyncio
async def test_skill_uninstall_removes_local_workspace_skill_and_prunes_lockfile(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)
    skill_dir = tmp_path / "skills" / "demo-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# demo", encoding="utf-8")
    lock_dir = tmp_path / ".clawhub"
    lock_dir.mkdir()
    lock_path = lock_dir / "lock.json"
    lock_path.write_text(
        '{"skills":{"demo-skill":{"version":"1.0.0"},"other-skill":{"version":"2.0.0"}}}',
        encoding="utf-8",
    )

    response = await loop._process_message(
        InboundMessage(channel="cli", sender_id="user", chat_id="direct", content="/skill uninstall demo-skill")
    )

    assert response is not None
    assert "Removed local skill demo-skill" in response.content
    assert "Updated ClawHub lockfile" in response.content
    assert not skill_dir.exists()
    assert '"demo-skill"' not in lock_path.read_text(encoding="utf-8")
    assert '"other-skill"' in lock_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_skill_help_includes_skill_command(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)

    response = await loop._process_message(
        InboundMessage(channel="cli", sender_id="user", chat_id="direct", content="/help")
    )

    assert response is not None
    assert "/skill <search|install|uninstall|list|update|derive|lint>" in response.content


@pytest.mark.asyncio
async def test_skill_derive_creates_workspace_skill_draft_from_session_context(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)
    session = loop.sessions.get_or_create("cli:direct")
    session.add_message("user", "Trace the browser status page regression")
    session.add_message("assistant", "The recent fix came from narrowing the task and verifying the status page.")
    session.metadata["working_checkpoint"] = {
        "status": "completed",
        "goal": "Trace the browser status page regression",
        "current_step": "Final response delivered",
        "next_step": "",
        "recent_tools": ["grep", "read_file", "exec"],
        "response_preview": "Status page now shows current and next step correctly.",
        "updated_at": "2026-04-16T00:00:00Z",
    }
    loop.sessions.save(session)

    response = await loop._process_message(
        InboundMessage(
            channel="cli",
            sender_id="user",
            chat_id="direct",
            content="/skill derive status-page-fix browser status workflow",
        )
    )

    assert response is not None
    assert "Created workspace skill draft status-page-fix" in response.content
    assert "Derived from session: cli:direct" in response.content

    skill_path = tmp_path / "skills" / "status-page-fix" / "SKILL.md"
    assert skill_path.exists()
    content = skill_path.read_text(encoding="utf-8")
    assert "name: status-page-fix" in content
    assert "Use when you need to browser status workflow." in content
    assert 'metadata: {"hahobot":{"triggers":[' in content
    assert '"tool_tags":["grep","read_file","exec"]' in content
    assert "Repeat this workspace-local workflow for: Trace the browser status page regression" in content
    assert "Start with these tools or command families when relevant: grep, read_file, exec." in content
    assert "Trigger hints: browser, trace, page, regression, fix" in content
    assert "Derived from session: cli:direct" in content
    assert "Recent completion summary: Status page now shows current and next step correctly." in content


@pytest.mark.asyncio
async def test_skill_derive_refuses_to_overwrite_existing_draft_without_force(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)
    skill_path = tmp_path / "skills" / "status-page-fix" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("original draft\n", encoding="utf-8")

    response = await loop._process_message(
        InboundMessage(
            channel="cli",
            sender_id="user",
            chat_id="direct",
            content="/skill derive status-page-fix browser status workflow",
        )
    )

    assert response is not None
    assert "already exists" in response.content
    assert "--force" in response.content
    assert skill_path.read_text(encoding="utf-8") == "original draft\n"


@pytest.mark.asyncio
async def test_skill_derive_force_overwrites_existing_draft(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)
    session = loop.sessions.get_or_create("cli:direct")
    session.add_message("user", "Trace the browser status page regression")
    session.add_message("assistant", "The recent fix came from narrowing the task and verifying the status page.")
    session.metadata["working_checkpoint"] = {
        "status": "completed",
        "goal": "Trace the browser status page regression",
        "current_step": "Final response delivered",
        "next_step": "",
        "recent_tools": ["grep", "read_file", "exec"],
        "response_preview": "Status page now shows current and next step correctly.",
        "updated_at": "2026-04-16T00:00:00Z",
    }
    loop.sessions.save(session)
    skill_path = tmp_path / "skills" / "status-page-fix" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("original draft\n", encoding="utf-8")

    response = await loop._process_message(
        InboundMessage(
            channel="cli",
            sender_id="user",
            chat_id="direct",
            content="/skill derive --force status-page-fix browser status workflow",
        )
    )

    assert response is not None
    assert "Overwrote workspace skill draft status-page-fix" in response.content
    content = skill_path.read_text(encoding="utf-8")
    assert content != "original draft\n"
    assert "name: status-page-fix" in content
    assert "Use when you need to browser status workflow." in content


@pytest.mark.asyncio
async def test_skill_missing_npx_returns_guidance(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)

    with patch("hahobot.agent.commands.skill.shutil.which", return_value=None):
        response = await loop._process_message(
            InboundMessage(channel="cli", sender_id="user", chat_id="direct", content="/skill list")
        )

    assert response is not None
    assert "npx is not installed" in response.content


@pytest.mark.asyncio
async def test_skill_usage_errors_are_user_facing(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)

    usage = await loop._process_message(
        InboundMessage(channel="cli", sender_id="user", chat_id="direct", content="/skill")
    )
    missing_slug = await loop._process_message(
        InboundMessage(channel="cli", sender_id="user", chat_id="direct", content="/skill install")
    )
    missing_uninstall_slug = await loop._process_message(
        InboundMessage(channel="cli", sender_id="user", chat_id="direct", content="/skill uninstall")
    )
    missing_derive_name = await loop._process_message(
        InboundMessage(channel="cli", sender_id="user", chat_id="direct", content="/skill derive")
    )

    assert usage is not None
    assert "/skill search <query>" in usage.content
    assert "/skill derive <name> [brief] [--force]" in usage.content
    assert "/skill lint" in usage.content
    assert missing_slug is not None
    assert "Missing skill slug" in missing_slug.content
    assert missing_uninstall_slug is not None
    assert "/skill uninstall <slug>" in missing_uninstall_slug.content
    assert missing_derive_name is not None
    assert "/skill derive <name> [brief] [--force]" in missing_derive_name.content


@pytest.mark.asyncio
async def test_skill_lint_reports_supersedes_and_overlap(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)
    skills_root = tmp_path / "skills"
    skills_root.mkdir(parents=True)
    for name, payload in (
        (
            "status-alpha",
            {"triggers": ["browser", "status"], "tool_tags": ["grep", "read_file"]},
        ),
        (
            "status-beta",
            {"triggers": ["browser", "status"], "tool_tags": ["grep", "read_file"]},
        ),
        (
            "cleanup-v2",
            {"supersedes": ["cleanup-v1", "ghost-skill"]},
        ),
    ):
        skill_dir = skills_root / name
        skill_dir.mkdir()
        payload_json = json.dumps({"hahobot": payload}, separators=(",", ":"))
        (skill_dir / "SKILL.md").write_text(
            "\n".join(["---", f"metadata: {payload_json}", "---", "", f"# {name}"]),
            encoding="utf-8",
        )
    cleanup_v1 = skills_root / "cleanup-v1"
    cleanup_v1.mkdir()
    (cleanup_v1 / "SKILL.md").write_text("# cleanup-v1\n", encoding="utf-8")

    response = await loop._process_message(
        InboundMessage(channel="cli", sender_id="user", chat_id="direct", content="/skill lint")
    )

    assert response is not None
    assert "Skill lint summary" in response.content
    assert "cleanup-v1" in response.content
    assert "ghost-skill" in response.content
    assert "status-alpha <-> status-beta" in response.content
