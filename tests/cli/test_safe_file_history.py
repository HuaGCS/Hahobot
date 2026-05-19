"""Regression tests for SafeFileHistory (issue #2846).

Surrogate characters in CLI input must not crash history file writes.
"""

from hahobot.cli.commands.interactive import SafeFileHistory, _sanitize_surrogates


class TestSafeFileHistory:
    def test_surrogate_replaced(self, tmp_path):
        """Malformed surrogate code points are replaced with U+FFFD, not crash."""
        hist = SafeFileHistory(str(tmp_path / "history"))
        hist.store_string("hello \udce9 world")
        entries = list(hist.load_history_strings())
        assert len(entries) == 1
        assert "\udce9" not in entries[0]
        assert "hello" in entries[0]
        assert "world" in entries[0]

    def test_normal_text_unchanged(self, tmp_path):
        hist = SafeFileHistory(str(tmp_path / "history"))
        hist.store_string("normal ascii text")
        entries = list(hist.load_history_strings())
        assert entries[0] == "normal ascii text"

    def test_emoji_preserved(self, tmp_path):
        hist = SafeFileHistory(str(tmp_path / "history"))
        hist.store_string("hello 🐈 hahobot")
        entries = list(hist.load_history_strings())
        assert entries[0] == "hello 🐈 hahobot"

    def test_mixed_unicode_preserved(self, tmp_path):
        """CJK + emoji + latin should all pass through cleanly."""
        hist = SafeFileHistory(str(tmp_path / "history"))
        hist.store_string("你好 hello こんにちは 🎉")
        entries = list(hist.load_history_strings())
        assert entries[0] == "你好 hello こんにちは 🎉"

    def test_multiple_surrogates(self, tmp_path):
        hist = SafeFileHistory(str(tmp_path / "history"))
        hist.store_string("\udce9\udcf1\udcff")
        entries = list(hist.load_history_strings())
        assert len(entries) == 1
        assert "\udce9" not in entries[0]

    def test_high_surrogate_replaced(self, tmp_path):
        hist = SafeFileHistory(str(tmp_path / "history"))
        hist.store_string("hello \ud83d world")
        entries = list(hist.load_history_strings())
        assert len(entries) == 1
        assert "\ud83d" not in entries[0]
        assert entries[0] == "hello � world"

    def test_valid_surrogate_pair_preserved_as_character(self):
        assert _sanitize_surrogates("\ud83d\ude00") == "😀"
