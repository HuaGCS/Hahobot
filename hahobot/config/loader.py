"""Configuration loading utilities."""

import copy
import json
import os
import re
from pathlib import Path

import pydantic
from loguru import logger

from hahobot.config.schema import Config

DEFAULT_CONFIG_DIR = Path.home() / ".hahobot"
DEFAULT_CONFIG_PATH = DEFAULT_CONFIG_DIR / "config.json"
LEGACY_CONFIG_DIR = Path.home() / ".nanobot"
LEGACY_CONFIG_PATH = LEGACY_CONFIG_DIR / "config.json"

# Global variable to store current config path (for multi-instance support)
_current_config_path: Path | None = None


def set_config_path(path: Path) -> None:
    """Set the current config path (used to derive data directory)."""
    global _current_config_path
    _current_config_path = path


def get_config_path() -> Path:
    """Get the configuration file path."""
    if _current_config_path:
        return _current_config_path
    return DEFAULT_CONFIG_PATH


def get_default_config_path() -> Path:
    """Return hahobot's default global config file path."""
    return DEFAULT_CONFIG_PATH


def get_legacy_config_path() -> Path:
    """Return nanobot's legacy global config file path."""
    return LEGACY_CONFIG_PATH


def find_compatible_config_source() -> Path | None:
    """Return a legacy config path that should be auto-copied on startup."""
    source_path, target_path = _resolve_config_paths(None)
    if source_path.exists() and source_path != target_path:
        return source_path
    return None


def load_config(config_path: Path | None = None) -> Config:
    """
    Load configuration from file or create default.

    Args:
        config_path: Optional path to config file. Uses default if not provided.

    Returns:
        Loaded configuration object.
    """
    source_path, target_path = _resolve_config_paths(config_path)

    if source_path.exists():
        try:
            with open(source_path, encoding="utf-8") as f:
                original_data = json.load(f)
            data = _migrate_config(
                copy.deepcopy(original_data),
                source_path=source_path,
                target_path=target_path,
            )
            config = Config.model_validate(data).bind_config_path(target_path)
            if source_path != target_path or data != original_data:
                save_config(config, target_path)
                if source_path != target_path:
                    logger.info(
                        "Copied legacy config from {} to {}",
                        source_path,
                        target_path,
                    )
                else:
                    logger.info("Normalized compatible config at {}", target_path)
            return config
        except (json.JSONDecodeError, ValueError, pydantic.ValidationError) as e:
            logger.warning(f"Failed to load config from {source_path}: {e}")
            logger.warning("Using default configuration.")

    return Config().bind_config_path(target_path)


def save_config(config: Config, config_path: Path | None = None) -> None:
    """
    Save configuration to file.

    Args:
        config: Configuration to save.
        config_path: Optional path to save to. Uses default if not provided.
    """
    path = (config_path or get_config_path()).expanduser().resolve(strict=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    config.bind_config_path(path)

    data = config.model_dump(mode="json", by_alias=True)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def resolve_config_env_vars(config: Config) -> Config:
    """Return a copy of *config* with ``${VAR}`` env-var references resolved.

    Only string values are affected; other types pass through unchanged.
    Raises :class:`ValueError` if a referenced variable is not set.
    """
    data = config.model_dump(mode="json", by_alias=True)
    data = _resolve_env_vars(data)
    resolved = Config.model_validate(data)
    # Preserve the private _config_path that model_dump/model_validate cannot
    # round-trip, so downstream code still resolves the correct workspace.
    if hasattr(config, "_config_path") and config._config_path is not None:
        resolved.bind_config_path(config._config_path)
    return resolved


def _resolve_env_vars(obj: object) -> object:
    """Recursively resolve ``${VAR}`` patterns in string values."""
    if isinstance(obj, str):
        return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", _env_replace, obj)
    if isinstance(obj, dict):
        return {k: _resolve_env_vars(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_env_vars(v) for v in obj]
    return obj


def _env_replace(match: re.Match[str]) -> str:
    name = match.group(1)
    value = os.environ.get(name)
    if value is None:
        raise ValueError(
            f"Environment variable '{name}' referenced in config is not set"
        )
    return value


def _resolve_config_paths(config_path: Path | None) -> tuple[Path, Path]:
    """Resolve the source config path and canonical target path."""
    requested_path = (config_path or get_config_path()).expanduser().resolve(strict=False)
    if config_path is not None or _current_config_path is not None:
        return requested_path, requested_path
    if requested_path.exists():
        return requested_path, requested_path

    legacy_path = get_legacy_config_path().expanduser().resolve(strict=False)
    if legacy_path.exists():
        return legacy_path, requested_path
    return requested_path, requested_path


def _migrate_config(
    data: dict,
    *,
    source_path: Path | None = None,
    target_path: Path | None = None,
) -> dict:
    """Migrate old config formats to current."""
    # Move tools.exec.restrictToWorkspace → tools.restrictToWorkspace
    tools = data.get("tools", {})
    exec_cfg = tools.get("exec", {})
    if "restrictToWorkspace" in exec_cfg and "restrictToWorkspace" not in tools:
        tools["restrictToWorkspace"] = exec_cfg.pop("restrictToWorkspace")

    defaults = data.get("agents", {}).get("defaults", {})
    defaults.pop("memoryWindow", None)

    if source_path is not None and target_path is not None:
        _preserve_legacy_workspace(data, source_path=source_path, target_path=target_path)
    return data


def _preserve_legacy_workspace(
    data: dict,
    *,
    source_path: Path,
    target_path: Path,
) -> None:
    """Keep the old default workspace reachable when seeding from legacy homes."""
    legacy_default = get_legacy_config_path().expanduser().resolve(strict=False)
    canonical_default = get_default_config_path().expanduser().resolve(strict=False)
    if source_path != legacy_default or target_path != canonical_default:
        return

    defaults = data.setdefault("agents", {}).setdefault("defaults", {})
    workspace = str(defaults.get("workspace", "") or "").strip()
    if workspace:
        return

    legacy_workspace = source_path.parent / "workspace"
    if legacy_workspace.exists():
        defaults["workspace"] = str(legacy_workspace)
