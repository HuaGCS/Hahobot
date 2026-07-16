from hahobot.cli.session_stats import CliSessionStats


def test_cli_session_stats_formats_provider_usage_and_weighted_stream_rate() -> None:
    stats = CliSessionStats(started_at=10.0)
    stats.record_usage(
        {
            "prompt_tokens": 10_000,
            "completion_tokens": 1_000,
            "total_tokens": 11_200,
            "cached_tokens": 4_000,
        }
    )
    stats.record_usage({"prompt_tokens": 2_000, "completion_tokens": 500})
    stats.record_stream(100, 4.0)
    stats.record_stream(50, 1.0)

    lines = stats.summary_lines(
        turn_count=3,
        now=75.0,
    )

    assert lines == [
        "💬 Completed agent turns: 3 · Model calls: 2",
        "📊 Tokens: 12,000 in / 1,500 out / 13,700 total (4,000 cached)",
        "⚡ Stream: ≈150 tok / 5.0s active · avg ≈30.0 tok/s",
        "⏱ Duration: 1m 5s",
    ]


def test_cli_session_stats_marks_missing_usage_and_stream_samples() -> None:
    stats = CliSessionStats(started_at=20.0)

    lines = stats.summary_lines(usage={}, turn_count=0, now=20.25)

    assert lines == [
        "💬 Completed agent turns: 0",
        "📊 Tokens: provider usage unavailable",
        "⚡ Stream: no completed streamed output",
        "⏱ Duration: 0.2s",
    ]
