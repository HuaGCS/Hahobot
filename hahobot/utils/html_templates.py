"""Render packaged HTML templates under ``hahobot/templates``."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

_TEMPLATES_ROOT = Path(__file__).resolve().parent.parent / "templates"


@lru_cache
def _environment() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(_TEMPLATES_ROOT)),
        autoescape=select_autoescape(enabled_extensions=("html", "xml"), default=False),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def render_html_template(name: str, **kwargs: Any) -> str:
    """Render an HTML template under ``hahobot/templates``."""
    return _environment().get_template(name).render(**kwargs)
