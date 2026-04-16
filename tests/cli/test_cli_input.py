import json
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import HTML

from hahobot.cli import commands
from hahobot.cli import stream as stream_mod


@pytest.fixture(autouse=True)
def reset_interactive_completion_context():
    commands._clear_interactive_completion_context()
    yield
    commands._clear_interactive_completion_context()


@pytest.fixture
def mock_prompt_session():
    """Mock the global prompt session."""
    mock_session = MagicMock()
    mock_session.prompt_async = AsyncMock()
    with patch("hahobot.cli.commands._PROMPT_SESSION", mock_session), \
         patch("hahobot.cli.commands.patch_stdout"):
        yield mock_session


@pytest.mark.asyncio
async def test_read_interactive_input_async_returns_input(mock_prompt_session):
    """Test that _read_interactive_input_async returns the user input from prompt_session."""
    mock_prompt_session.prompt_async.return_value = "hello world"

    result = await commands._read_interactive_input_async()

    assert result == "hello world"
    mock_prompt_session.prompt_async.assert_called_once()
    args, kwargs = mock_prompt_session.prompt_async.call_args
    assert isinstance(args[0], HTML)  # Verify HTML prompt is used
    assert kwargs["completer"] is commands._INTERACTIVE_SLASH_COMPLETER


@pytest.mark.asyncio
async def test_read_interactive_input_async_multiline_sets_prompt_options(mock_prompt_session):
    """Multiline mode should enable continuation UI and custom submit bindings."""
    mock_prompt_session.prompt_async.return_value = "line1\nline2"

    result = await commands._read_interactive_input_async(multiline=True)

    assert result == "line1\nline2"
    _, kwargs = mock_prompt_session.prompt_async.call_args
    assert kwargs["multiline"] is True
    assert kwargs["completer"] is commands._INTERACTIVE_SLASH_COMPLETER
    assert kwargs["key_bindings"] is not None
    assert kwargs["bottom_toolbar"] is not None
    assert isinstance(kwargs["prompt_continuation"], HTML)


def test_interactive_slash_completer_matches_top_level_prefixes():
    completions = list(
        commands._INTERACTIVE_SLASH_COMPLETER.get_completions(
            Document(text="/s", cursor_position=2),
            None,
        )
    )

    assert [completion.text for completion in completions] == [
        "/status",
        "/stchar",
        "/scene",
        "/skill",
        "/stop",
        "/session",
    ]


def test_interactive_slash_completer_matches_session_subcommands():
    completions = list(
        commands._INTERACTIVE_SLASH_COMPLETER.get_completions(
            Document(text="/session s", cursor_position=len("/session s")),
            None,
        )
    )

    assert [completion.text for completion in completions] == ["show"]


def test_interactive_slash_completer_matches_session_export_subcommand():
    completions = list(
        commands._INTERACTIVE_SLASH_COMPLETER.get_completions(
            Document(text="/session e", cursor_position=len("/session e")),
            None,
        )
    )

    assert [completion.text for completion in completions] == ["export"]


def test_interactive_slash_completer_matches_repo_prefixes():
    completions = list(
        commands._INTERACTIVE_SLASH_COMPLETER.get_completions(
            Document(text="/r", cursor_position=2),
            None,
        )
    )

    assert [completion.text for completion in completions] == ["/restart", "/repo", "/review"]


def test_interactive_slash_completer_matches_compact_prefix():
    completions = list(
        commands._INTERACTIVE_SLASH_COMPLETER.get_completions(
            Document(text="/c", cursor_position=2),
            None,
        )
    )

    assert [completion.text for completion in completions] == ["/compact"]


def test_interactive_slash_completer_matches_update_prefix():
    completions = list(
        commands._INTERACTIVE_SLASH_COMPLETER.get_completions(
            Document(text="/u", cursor_position=2),
            None,
        )
    )

    assert [completion.text for completion in completions] == ["/update"]


def test_interactive_slash_completer_matches_update_subcommands():
    completions = list(
        commands._INTERACTIVE_SLASH_COMPLETER.get_completions(
            Document(text="/update c", cursor_position=len("/update c")),
            None,
        )
    )

    assert [completion.text for completion in completions] == ["check"]


def test_interactive_slash_completer_matches_skill_subcommands():
    completions = list(
        commands._INTERACTIVE_SLASH_COMPLETER.get_completions(
            Document(text="/skill d", cursor_position=len("/skill d")),
            None,
        )
    )

    assert [completion.text for completion in completions] == ["derive"]


def test_interactive_slash_completer_matches_repo_subcommands():
    completions = list(
        commands._INTERACTIVE_SLASH_COMPLETER.get_completions(
            Document(text="/repo d", cursor_position=len("/repo d")),
            None,
        )
    )

    assert [completion.text for completion in completions] == ["diff"]


def test_interactive_slash_completer_matches_repo_third_token():
    completions = list(
        commands._INTERACTIVE_SLASH_COMPLETER.get_completions(
            Document(text="/repo diff s", cursor_position=len("/repo diff s")),
            None,
        )
    )

    assert [completion.text for completion in completions] == ["staged"]


def test_interactive_slash_completer_matches_review_subcommands():
    completions = list(
        commands._INTERACTIVE_SLASH_COMPLETER.get_completions(
            Document(text="/review s", cursor_position=len("/review s")),
            None,
        )
    )

    assert [completion.text for completion in completions] == ["staged"]


def test_interactive_slash_completer_matches_dynamic_persona_names(tmp_path):
    (tmp_path / "personas" / "coder").mkdir(parents=True)

    commands._set_interactive_completion_context(
        workspace=tmp_path,
        session_manager=None,
        current_session_id=None,
    )
    completions = list(
        commands._INTERACTIVE_SLASH_COMPLETER.get_completions(
            Document(text="/persona set c", cursor_position=len("/persona set c")),
            None,
        )
    )

    assert [completion.text for completion in completions] == ["coder"]


def test_interactive_slash_completer_matches_dynamic_session_keys(tmp_path):
    from hahobot.session.manager import SessionManager

    manager = SessionManager(tmp_path)
    for key in ("cli:alpha", "cli:beta"):
        session = manager.get_or_create(key)
        session.add_message("user", "hello")
        manager.save(session)

    commands._set_interactive_completion_context(
        workspace=tmp_path,
        session_manager=manager,
        current_session_id="cli:direct",
    )
    completions = list(
        commands._INTERACTIVE_SLASH_COMPLETER.get_completions(
            Document(text="/session use cli:a", cursor_position=len("/session use cli:a")),
            None,
        )
    )

    assert [completion.text for completion in completions] == ["cli:alpha"]

    export_completions = list(
        commands._INTERACTIVE_SLASH_COMPLETER.get_completions(
            Document(text="/session export cli:b", cursor_position=len("/session export cli:b")),
            None,
        )
    )

    assert [completion.text for completion in export_completions] == ["cli:beta"]


def test_interactive_slash_completer_matches_dynamic_compact_session_keys(tmp_path):
    from hahobot.session.manager import SessionManager

    manager = SessionManager(tmp_path)
    for key in ("cli:alpha", "cli:beta"):
        session = manager.get_or_create(key)
        session.add_message("user", "hello")
        manager.save(session)

    commands._set_interactive_completion_context(
        workspace=tmp_path,
        session_manager=manager,
        current_session_id="cli:direct",
    )
    completions = list(
        commands._INTERACTIVE_SLASH_COMPLETER.get_completions(
            Document(text="/compact cli:b", cursor_position=len("/compact cli:b")),
            None,
        )
    )

    assert [completion.text for completion in completions] == ["cli:beta"]


def test_interactive_slash_completer_matches_dynamic_scene_names(tmp_path):
    from hahobot.session.manager import SessionManager

    persona_dir = tmp_path / "personas" / "Aria"
    manifest_dir = persona_dir / ".hahobot"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "st_manifest.json").write_text(
        json.dumps({"scene_prompts": {"rainy_walk": "Umbrella, close walk, wet street reflections."}}),
        encoding="utf-8",
    )

    manager = SessionManager(tmp_path)
    session = manager.get_or_create("cli:direct")
    session.metadata["persona"] = "Aria"
    manager.save(session)

    commands._set_interactive_completion_context(
        workspace=tmp_path,
        session_manager=manager,
        current_session_id="cli:direct",
    )
    completions = list(
        commands._INTERACTIVE_SLASH_COMPLETER.get_completions(
            Document(text="/scene r", cursor_position=len("/scene r")),
            None,
        )
    )

    assert [completion.text for completion in completions] == ["rainy_walk"]


def test_interactive_slash_completer_matches_language_codes():
    completions = list(
        commands._INTERACTIVE_SLASH_COMPLETER.get_completions(
            Document(text="/lang set z", cursor_position=len("/lang set z")),
            None,
        )
    )

    assert [completion.text for completion in completions] == ["zh"]


def test_interactive_slash_completer_ignores_plain_text():
    completions = list(
        commands._INTERACTIVE_SLASH_COMPLETER.get_completions(
            Document(text="hello /s", cursor_position=len("hello /s")),
            None,
        )
    )

    assert completions == []


@pytest.mark.asyncio
async def test_read_interactive_input_async_handles_eof(mock_prompt_session):
    """Test that EOFError converts to KeyboardInterrupt."""
    mock_prompt_session.prompt_async.side_effect = EOFError()

    with pytest.raises(KeyboardInterrupt):
        await commands._read_interactive_input_async()


def test_init_prompt_session_creates_session():
    """Test that _init_prompt_session initializes the global session."""
    # Ensure global is None before test
    commands._PROMPT_SESSION = None

    with patch("hahobot.cli.commands.PromptSession") as mock_session_cls, \
         patch("hahobot.cli.commands.FileHistory"), \
         patch("pathlib.Path.home") as mock_home:

        mock_home.return_value = MagicMock()

        commands._init_prompt_session()

        assert commands._PROMPT_SESSION is not None
        mock_session_cls.assert_called_once()
        _, kwargs = mock_session_cls.call_args
        assert kwargs["multiline"] is False
        assert kwargs["enable_open_in_editor"] is False


def test_thinking_spinner_pause_stops_and_restarts():
    """Pause should stop the active spinner and restart it afterward."""
    spinner = MagicMock()
    mock_console = MagicMock()
    mock_console.status.return_value = spinner

    thinking = stream_mod.ThinkingSpinner(console=mock_console)
    with thinking:
        with thinking.pause():
            pass

    assert spinner.method_calls == [
        call.start(),
        call.stop(),
        call.start(),
        call.stop(),
    ]


def test_print_cli_progress_line_pauses_spinner_before_printing():
    """CLI progress output should pause spinner to avoid garbled lines."""
    order: list[str] = []
    spinner = MagicMock()
    spinner.start.side_effect = lambda: order.append("start")
    spinner.stop.side_effect = lambda: order.append("stop")
    mock_console = MagicMock()
    mock_console.status.return_value = spinner

    with patch.object(commands.console, "print", side_effect=lambda *_args, **_kwargs: order.append("print")):
        thinking = stream_mod.ThinkingSpinner(console=mock_console)
        with thinking:
            commands._print_cli_progress_line("tool running", thinking)

    assert order == ["start", "stop", "print", "start", "stop"]


@pytest.mark.asyncio
async def test_print_interactive_progress_line_pauses_spinner_before_printing():
    """Interactive progress output should also pause spinner cleanly."""
    order: list[str] = []
    spinner = MagicMock()
    spinner.start.side_effect = lambda: order.append("start")
    spinner.stop.side_effect = lambda: order.append("stop")
    mock_console = MagicMock()
    mock_console.status.return_value = spinner

    async def fake_print(_text: str) -> None:
        order.append("print")

    with patch("hahobot.cli.commands._print_interactive_line", side_effect=fake_print):
        thinking = stream_mod.ThinkingSpinner(console=mock_console)
        with thinking:
            await commands._print_interactive_progress_line("tool running", thinking)

    assert order == ["start", "stop", "print", "start", "stop"]


def test_response_renderable_uses_text_for_explicit_plain_rendering():
    status = (
        "🐈 hahobot v0.1.4.post5\n"
        "🧠 Model: MiniMax-M2.7\n"
        "📊 Tokens: 20639 in / 29 out"
    )

    renderable = commands._response_renderable(
        status,
        render_markdown=True,
        metadata={"render_as": "text"},
    )

    assert renderable.__class__.__name__ == "Text"


def test_response_renderable_preserves_normal_markdown_rendering():
    renderable = commands._response_renderable("**bold**", render_markdown=True)

    assert renderable.__class__.__name__ == "Markdown"


def test_response_renderable_without_metadata_keeps_markdown_path():
    help_text = "🐈 hahobot commands:\n/status — Show bot status\n/help — Show available commands"

    renderable = commands._response_renderable(help_text, render_markdown=True)

    assert renderable.__class__.__name__ == "Markdown"


def test_stream_renderer_stop_for_input_stops_spinner():
    """stop_for_input should stop the active spinner to avoid prompt_toolkit conflicts."""
    spinner = MagicMock()
    mock_console = MagicMock()
    mock_console.status.return_value = spinner

    # Create renderer with mocked console
    with patch.object(stream_mod, "_make_console", return_value=mock_console):
        renderer = stream_mod.StreamRenderer(show_spinner=True)

        # Verify spinner started
        spinner.start.assert_called_once()

        # Stop for input
        renderer.stop_for_input()

        # Verify spinner stopped
        spinner.stop.assert_called_once()


def test_make_console_uses_force_terminal():
    """Console should be created with force_terminal=True for proper ANSI handling."""
    console = stream_mod._make_console()
    assert console._force_terminal is True
