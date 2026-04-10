"""
hahobot - A lightweight AI agent framework
"""

import tomllib
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from pathlib import Path


def _read_pyproject_version() -> str | None:
    """Read the source-tree version when package metadata is unavailable."""
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    if not pyproject.exists():
        return None
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    return data.get("project", {}).get("version")


def _resolve_version() -> str:
    try:
        return _pkg_version("hahobot-ai")
    except PackageNotFoundError:
        # Source checkouts often import hahobot without installed dist-info.
        return _read_pyproject_version() or "0.1.5"


__version__ = _resolve_version()
__logo__ = "🐈"

__all__ = ["ExternalHookBridge", "Hahobot", "RunResult"]


def __getattr__(name: str):
    """Lazy top-level exports to avoid import cycles during package init."""
    if name == "ExternalHookBridge":
        from hahobot.agent.hook_bridge import ExternalHookBridge

        return ExternalHookBridge
    if name in {"Hahobot", "RunResult"}:
        from hahobot.hahobot import Hahobot, RunResult

        exports = {
            "Hahobot": Hahobot,
            "RunResult": RunResult,
        }
        return exports[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
