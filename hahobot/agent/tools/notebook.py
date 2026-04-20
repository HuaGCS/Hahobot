"""Notebook edit tool for controlled Jupyter .ipynb updates."""

from __future__ import annotations

import json
import uuid
from typing import Any

from hahobot.agent.tools.base import tool_parameters
from hahobot.agent.tools.filesystem import _FsTool
from hahobot.agent.tools.schema import IntegerSchema, StringSchema, tool_parameters_schema


def _new_cell(source: str, cell_type: str = "code", *, generate_id: bool = False) -> dict[str, Any]:
    cell: dict[str, Any] = {
        "cell_type": cell_type,
        "source": source,
        "metadata": {},
    }
    if cell_type == "code":
        cell["outputs"] = []
        cell["execution_count"] = None
    if generate_id:
        cell["id"] = uuid.uuid4().hex[:8]
    return cell


def _empty_notebook() -> dict[str, Any]:
    return {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {
            "kernelspec": {
                "display_name": "Python 3",
                "language": "python",
                "name": "python3",
            },
            "language_info": {"name": "python"},
        },
        "cells": [],
    }


@tool_parameters(
    tool_parameters_schema(
        path=StringSchema("Path to the .ipynb notebook file"),
        cell_index=IntegerSchema(
            0,
            description="0-based target cell index",
            minimum=0,
        ),
        new_source=StringSchema("New source content for the target cell"),
        cell_type=StringSchema(
            "Notebook cell type",
            enum=("code", "markdown"),
        ),
        edit_mode=StringSchema(
            "Edit mode: replace, insert, or delete",
            enum=("replace", "insert", "delete"),
        ),
        required=["path", "cell_index"],
    )
)
class NotebookEditTool(_FsTool):
    """Edit Jupyter notebook cells without exposing raw JSON surgery to the model."""

    _VALID_CELL_TYPES = frozenset({"code", "markdown"})
    _VALID_EDIT_MODES = frozenset({"replace", "insert", "delete"})

    @property
    def name(self) -> str:
        return "notebook_edit"

    @property
    def description(self) -> str:
        return (
            "Edit a Jupyter notebook (.ipynb) cell. "
            "replace updates one cell, insert adds a new cell after the target index, "
            "and delete removes the target cell. "
            "Use this instead of editing notebook JSON manually."
        )

    async def execute(
        self,
        path: str | None = None,
        cell_index: int = 0,
        new_source: str = "",
        cell_type: str = "code",
        edit_mode: str = "replace",
        **kwargs: Any,
    ) -> str:
        try:
            if not path:
                return "Error: path is required"
            if not path.endswith(".ipynb"):
                return (
                    "Error: notebook_edit only works on .ipynb files. "
                    "Use edit_file for other file types."
                )
            if edit_mode not in self._VALID_EDIT_MODES:
                return (
                    f"Error: Invalid edit_mode '{edit_mode}'. "
                    "Use one of: replace, insert, delete."
                )
            if cell_type not in self._VALID_CELL_TYPES:
                return (
                    f"Error: Invalid cell_type '{cell_type}'. "
                    "Use one of: code, markdown."
                )

            notebook_path = self._resolve(path)
            if not notebook_path.exists():
                if edit_mode != "insert":
                    return f"Error: File not found: {path}"
                notebook = _empty_notebook()
                notebook["cells"].append(_new_cell(new_source, cell_type, generate_id=True))
                notebook_path.parent.mkdir(parents=True, exist_ok=True)
                notebook_path.write_text(
                    json.dumps(notebook, indent=1, ensure_ascii=False),
                    encoding="utf-8",
                )
                return f"Successfully created {notebook_path} with 1 cell"

            try:
                notebook = json.loads(notebook_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                return f"Error: Failed to parse notebook: {exc}"

            cells = notebook.get("cells", [])
            generate_id = (
                int(notebook.get("nbformat", 0)) >= 4
                and int(notebook.get("nbformat_minor", 0)) >= 5
            )

            if edit_mode == "delete":
                if cell_index < 0 or cell_index >= len(cells):
                    return (
                        f"Error: cell_index {cell_index} out of range "
                        f"(notebook has {len(cells)} cells)"
                    )
                cells.pop(cell_index)
                notebook["cells"] = cells
                notebook_path.write_text(
                    json.dumps(notebook, indent=1, ensure_ascii=False),
                    encoding="utf-8",
                )
                return f"Successfully deleted cell {cell_index} from {notebook_path}"

            if edit_mode == "insert":
                insert_at = min(cell_index + 1, len(cells))
                cells.insert(insert_at, _new_cell(new_source, cell_type, generate_id=generate_id))
                notebook["cells"] = cells
                notebook_path.write_text(
                    json.dumps(notebook, indent=1, ensure_ascii=False),
                    encoding="utf-8",
                )
                return f"Successfully inserted cell at index {insert_at} in {notebook_path}"

            if cell_index < 0 or cell_index >= len(cells):
                return (
                    f"Error: cell_index {cell_index} out of range "
                    f"(notebook has {len(cells)} cells)"
                )

            cells[cell_index]["source"] = new_source
            if cells[cell_index].get("cell_type") != cell_type:
                cells[cell_index]["cell_type"] = cell_type
                if cell_type == "code":
                    cells[cell_index].setdefault("outputs", [])
                    cells[cell_index].setdefault("execution_count", None)
                else:
                    cells[cell_index].pop("outputs", None)
                    cells[cell_index].pop("execution_count", None)

            notebook["cells"] = cells
            notebook_path.write_text(
                json.dumps(notebook, indent=1, ensure_ascii=False),
                encoding="utf-8",
            )
            return f"Successfully edited cell {cell_index} in {notebook_path}"
        except PermissionError as exc:
            return f"Error: {exc}"
        except Exception as exc:
            return f"Error editing notebook: {exc}"
