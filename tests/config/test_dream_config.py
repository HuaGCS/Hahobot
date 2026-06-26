from hahobot.config.schema import DreamConfig


def test_dream_config_defaults_to_interval_hours() -> None:
    cfg = DreamConfig()

    assert cfg.interval_h == 2
    assert cfg.cron is None


def test_dream_config_builds_every_schedule_from_interval() -> None:
    cfg = DreamConfig(interval_h=3)

    schedule = cfg.build_schedule("UTC")

    assert schedule.kind == "every"
    assert schedule.every_ms == 3 * 3_600_000
    assert schedule.expr is None


def test_dream_config_honors_legacy_cron_override() -> None:
    cfg = DreamConfig.model_validate({"cron": "0 */4 * * *"})

    schedule = cfg.build_schedule("UTC")

    assert schedule.kind == "cron"
    assert schedule.expr == "0 */4 * * *"
    assert schedule.tz == "UTC"
    assert cfg.describe_schedule() == "cron 0 */4 * * * (legacy)"


def test_dream_config_dump_uses_interval_h_and_hides_unset_cron() -> None:
    cfg = DreamConfig.model_validate({"intervalH": 5})

    dumped = cfg.model_dump(by_alias=True)

    assert dumped["intervalH"] == 5
    # An unset (None) legacy cron stays out of the dump so default configs stay clean.
    assert "cron" not in dumped


def test_dream_config_dump_preserves_configured_legacy_cron() -> None:
    # Regression for nanobot 319791cd: an operator-set cron must survive a config
    # round-trip. save_config() / admin saves re-dump the model, so dropping it (the old
    # exclude=True behavior) silently lost a configured cron on the next write.
    cfg = DreamConfig.model_validate({"intervalH": 5, "cron": "0 */4 * * *"})

    dumped = cfg.model_dump(by_alias=True)

    assert dumped["intervalH"] == 5
    assert dumped["cron"] == "0 */4 * * *"
    # And the round-tripped config still resolves to the cron schedule.
    assert DreamConfig.model_validate(dumped).build_schedule("UTC").expr == "0 */4 * * *"


def test_dream_config_uses_model_override_name_and_accepts_legacy_model() -> None:
    cfg = DreamConfig.model_validate({"model": "openrouter/sonnet"})

    dumped = cfg.model_dump(by_alias=True)

    assert cfg.model_override == "openrouter/sonnet"
    assert dumped["modelOverride"] == "openrouter/sonnet"
    assert "model" not in dumped
