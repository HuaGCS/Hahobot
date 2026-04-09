"""Compatibility package for legacy nanobot imports."""

from __future__ import annotations

import sys
from importlib import abc, import_module, util

from hahobot import __logo__, __version__

from .nanobot import Nanobot, RunResult, _make_provider

__all__ = ["Nanobot", "RunResult", "__version__", "__logo__", "_make_provider"]


class _NanobotAliasLoader(abc.Loader):
    """Load ``nanobot.*`` modules from their ``hahobot.*`` equivalents."""

    def __init__(self, legacy_name: str, target_name: str):
        self.legacy_name = legacy_name
        self.target_name = target_name

    def create_module(self, spec):  # type: ignore[override]
        module = import_module(self.target_name)
        sys.modules[self.legacy_name] = module
        return module

    def exec_module(self, module) -> None:  # type: ignore[override]
        return None


class _NanobotAliasFinder(abc.MetaPathFinder):
    """Resolve ``nanobot.*`` imports to the matching ``hahobot.*`` module."""

    _SKIP = {"nanobot.nanobot", "nanobot.__main__"}

    def find_spec(self, fullname: str, path=None, target=None):  # type: ignore[override]
        if not fullname.startswith("nanobot.") or fullname in self._SKIP:
            return None

        target_name = f"hahobot.{fullname.removeprefix('nanobot.')}"
        target_spec = util.find_spec(target_name)
        if target_spec is None:
            return None

        is_package = target_spec.submodule_search_locations is not None
        return util.spec_from_loader(
            fullname,
            _NanobotAliasLoader(fullname, target_name),
            origin=target_spec.origin,
            is_package=is_package,
        )


if not any(isinstance(finder, _NanobotAliasFinder) for finder in sys.meta_path):
    sys.meta_path.insert(0, _NanobotAliasFinder())
