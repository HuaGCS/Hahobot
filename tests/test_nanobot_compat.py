"""Tests for legacy nanobot compatibility aliases."""

from __future__ import annotations


def test_legacy_sdk_imports_remain_available() -> None:
    from hahobot.hahobot import Hahobot
    from hahobot.hahobot import RunResult as HahobotRunResult
    from nanobot import Nanobot, RunResult
    from nanobot.nanobot import Nanobot as LegacyFacade

    assert issubclass(Nanobot, Hahobot)
    assert LegacyFacade is Nanobot
    assert RunResult is HahobotRunResult


def test_legacy_submodule_aliases_point_to_hahobot_modules() -> None:
    from nanobot.cli.commands import app as legacy_app
    from nanobot.config.loader import load_config as legacy_load_config

    from hahobot.cli.commands import app as hahobot_app
    from hahobot.config.loader import load_config as hahobot_load_config

    assert legacy_app is hahobot_app
    assert legacy_load_config is hahobot_load_config


def test_legacy_module_entrypoint_is_importable() -> None:
    from nanobot.__main__ import main

    assert callable(main)
