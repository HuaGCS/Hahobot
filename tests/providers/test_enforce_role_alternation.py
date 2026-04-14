"""Tests for LLMProvider._enforce_role_alternation."""

from hahobot.providers.base import LLMProvider


class TestEnforceRoleAlternation:
    def test_empty_messages(self):
        assert LLMProvider._enforce_role_alternation([]) == []

    def test_no_change_needed(self):
        msgs = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello!"},
            {"role": "user", "content": "Bye"},
        ]
        result = LLMProvider._enforce_role_alternation(msgs)
        assert len(result) == 4
        assert result[-1]["role"] == "user"

    def test_trailing_assistant_removed(self):
        msgs = [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello!"},
        ]
        result = LLMProvider._enforce_role_alternation(msgs)
        assert result == [{"role": "user", "content": "Hi"}]

    def test_consecutive_user_messages_merged(self):
        msgs = [
            {"role": "user", "content": "Hello"},
            {"role": "user", "content": "How are you?"},
        ]
        result = LLMProvider._enforce_role_alternation(msgs)
        assert len(result) == 1
        assert "Hello" in result[0]["content"]
        assert "How are you?" in result[0]["content"]

    def test_consecutive_assistant_messages_merged(self):
        msgs = [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello!"},
            {"role": "assistant", "content": "How can I help?"},
            {"role": "user", "content": "Thanks"},
        ]
        result = LLMProvider._enforce_role_alternation(msgs)
        assert len(result) == 3
        assert "Hello!" in result[1]["content"]
        assert "How can I help?" in result[1]["content"]

    def test_system_messages_not_merged(self):
        msgs = [
            {"role": "system", "content": "System A"},
            {"role": "system", "content": "System B"},
            {"role": "user", "content": "Hi"},
        ]
        result = LLMProvider._enforce_role_alternation(msgs)
        assert len(result) == 3
        assert result[0]["content"] == "System A"
        assert result[1]["content"] == "System B"

    def test_tool_messages_not_merged(self):
        msgs = [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "1"}]},
            {"role": "tool", "content": "result1", "tool_call_id": "1"},
            {"role": "tool", "content": "result2", "tool_call_id": "2"},
            {"role": "user", "content": "Next"},
        ]
        result = LLMProvider._enforce_role_alternation(msgs)
        tool_msgs = [m for m in result if m["role"] == "tool"]
        assert len(tool_msgs) == 2

    def test_non_string_content_merges_list_and_string(self):
        msgs = [
            {"role": "user", "content": [{"type": "text", "text": "A"}]},
            {"role": "user", "content": "B"},
        ]
        result = LLMProvider._enforce_role_alternation(msgs)
        assert len(result) == 1
        # list + string → list with appended text block
        assert isinstance(result[0]["content"], list)
        assert len(result[0]["content"]) == 2
        assert result[0]["content"][0] == {"type": "text", "text": "A"}
        assert result[0]["content"][1] == {"type": "text", "text": "B"}

    def test_non_string_content_merges_string_and_list(self):
        msgs = [
            {"role": "user", "content": "A"},
            {"role": "user", "content": [{"type": "text", "text": "B"}]},
        ]
        result = LLMProvider._enforce_role_alternation(msgs)
        assert len(result) == 1
        assert isinstance(result[0]["content"], list)
        assert len(result[0]["content"]) == 2
        assert result[0]["content"][0] == {"type": "text", "text": "A"}
        assert result[0]["content"][1] == {"type": "text", "text": "B"}

    def test_non_string_content_merges_two_lists(self):
        msgs = [
            {"role": "user", "content": [{"type": "text", "text": "A"}]},
            {"role": "user", "content": [{"type": "text", "text": "B"}]},
        ]
        result = LLMProvider._enforce_role_alternation(msgs)
        assert len(result) == 1
        assert isinstance(result[0]["content"], list)
        assert len(result[0]["content"]) == 2

    def test_original_messages_not_mutated(self):
        msgs = [
            {"role": "user", "content": "Hello"},
            {"role": "user", "content": "World"},
        ]
        original_first = dict(msgs[0])
        LLMProvider._enforce_role_alternation(msgs)
        assert msgs[0] == original_first
        assert len(msgs) == 2

    def test_trailing_assistant_recovered_as_user_when_only_system_remains(self):
        msgs = [
            {"role": "system", "content": "You are helpful."},
            {"role": "assistant", "content": "Subagent completed successfully."},
        ]
        result = LLMProvider._enforce_role_alternation(msgs)
        assert len(result) == 2
        assert result[0]["role"] == "system"
        assert result[1]["role"] == "user"
        assert "Subagent completed successfully." in result[1]["content"]

    def test_trailing_assistant_not_recovered_when_user_message_present(self):
        msgs = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello!"},
        ]
        result = LLMProvider._enforce_role_alternation(msgs)
        assert len(result) == 2
        assert result[-1]["role"] == "user"

    def test_trailing_assistant_not_recovered_when_tool_message_present(self):
        msgs = [
            {"role": "system", "content": "You are helpful."},
            {"role": "tool", "content": "result", "tool_call_id": "1"},
            {"role": "assistant", "content": "Done."},
        ]
        result = LLMProvider._enforce_role_alternation(msgs)
        assert len(result) == 2
        assert result[-1]["role"] == "tool"
