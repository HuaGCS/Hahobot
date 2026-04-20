"""Tests for NotebookEditTool Jupyter notebook editing."""

from __future__ import annotations

import json

import pytest

from hahobot.agent.tools.notebook import NotebookEditTool


def _make_notebook(
    cells: list[dict] | None = None,
    *,
    nbformat: int = 4,
    nbformat_minor: int = 5,
) -> dict:
    return {
        "nbformat": nbformat,
        "nbformat_minor": nbformat_minor,
        "metadata": {
            "kernelspec": {
                "display_name": "Python 3",
                "language": "python",
                "name": "python3",
            }
        },
        "cells": cells or [],
    }


def _code_cell(source: str, cell_id: str | None = None) -> dict:
    cell = {
        "cell_type": "code",
        "source": source,
        "metadata": {},
        "outputs": [],
        "execution_count": None,
    }
    if cell_id:
        cell["id"] = cell_id
    return cell


def _md_cell(source: str, cell_id: str | None = None) -> dict:
    cell = {"cell_type": "markdown", "source": source, "metadata": {}}
    if cell_id:
        cell["id"] = cell_id
    return cell


def _write_notebook(tmp_path, name: str, notebook: dict) -> str:
    path = tmp_path / name
    path.write_text(json.dumps(notebook), encoding="utf-8")
    return str(path)


class TestNotebookEditTool:
    @pytest.fixture()
    def tool(self, tmp_path):
        return NotebookEditTool(workspace=tmp_path)

    @pytest.mark.asyncio
    async def test_replace_cell_content(self, tool, tmp_path) -> None:
        path = _write_notebook(
            tmp_path,
            "test.ipynb",
            _make_notebook([_code_cell("print('hello')"), _code_cell("x = 1")]),
        )

        result = await tool.execute(path=path, cell_index=0, new_source="print('world')")

        assert "Successfully" in result
        saved = json.loads((tmp_path / "test.ipynb").read_text(encoding="utf-8"))
        assert saved["cells"][0]["source"] == "print('world')"
        assert saved["cells"][1]["source"] == "x = 1"

    @pytest.mark.asyncio
    async def test_insert_cell_after_target(self, tool, tmp_path) -> None:
        path = _write_notebook(
            tmp_path,
            "test.ipynb",
            _make_notebook([_code_cell("cell 0"), _code_cell("cell 1")]),
        )

        result = await tool.execute(
            path=path,
            cell_index=0,
            new_source="inserted",
            edit_mode="insert",
        )

        assert "Successfully" in result
        saved = json.loads((tmp_path / "test.ipynb").read_text(encoding="utf-8"))
        assert [cell["source"] for cell in saved["cells"]] == ["cell 0", "inserted", "cell 1"]

    @pytest.mark.asyncio
    async def test_delete_cell(self, tool, tmp_path) -> None:
        path = _write_notebook(
            tmp_path,
            "test.ipynb",
            _make_notebook([_code_cell("A"), _code_cell("B"), _code_cell("C")]),
        )

        result = await tool.execute(path=path, cell_index=1, edit_mode="delete")

        assert "Successfully" in result
        saved = json.loads((tmp_path / "test.ipynb").read_text(encoding="utf-8"))
        assert [cell["source"] for cell in saved["cells"]] == ["A", "C"]

    @pytest.mark.asyncio
    async def test_create_new_notebook_on_insert(self, tool, tmp_path) -> None:
        path = str(tmp_path / "new.ipynb")

        result = await tool.execute(
            path=path,
            cell_index=0,
            new_source="# Hello",
            edit_mode="insert",
            cell_type="markdown",
        )

        assert "Successfully" in result
        saved = json.loads((tmp_path / "new.ipynb").read_text(encoding="utf-8"))
        assert saved["nbformat"] == 4
        assert len(saved["cells"]) == 1
        assert saved["cells"][0]["cell_type"] == "markdown"
        assert saved["cells"][0]["source"] == "# Hello"

    @pytest.mark.asyncio
    async def test_invalid_cell_index_errors(self, tool, tmp_path) -> None:
        path = _write_notebook(tmp_path, "test.ipynb", _make_notebook([_code_cell("only cell")]))

        result = await tool.execute(path=path, cell_index=5, new_source="x")

        assert "Error" in result
        assert "out of range" in result

    @pytest.mark.asyncio
    async def test_rejects_non_ipynb(self, tool, tmp_path) -> None:
        script = tmp_path / "script.py"
        script.write_text("pass", encoding="utf-8")

        result = await tool.execute(path=str(script), cell_index=0, new_source="x")

        assert "Error" in result
        assert ".ipynb" in result

    @pytest.mark.asyncio
    async def test_preserves_notebook_metadata(self, tool, tmp_path) -> None:
        cell = _code_cell("old")
        cell["outputs"] = [{"output_type": "stream", "text": "hello\n"}]
        cell["execution_count"] = 42
        path = _write_notebook(tmp_path, "test.ipynb", _make_notebook([cell]))

        await tool.execute(path=path, cell_index=0, new_source="new")

        saved = json.loads((tmp_path / "test.ipynb").read_text(encoding="utf-8"))
        assert saved["metadata"]["kernelspec"]["language"] == "python"

    @pytest.mark.asyncio
    async def test_nbformat_45_insert_generates_cell_id(self, tool, tmp_path) -> None:
        path = _write_notebook(tmp_path, "test.ipynb", _make_notebook([], nbformat_minor=5))

        await tool.execute(path=path, cell_index=0, new_source="x = 1", edit_mode="insert")

        saved = json.loads((tmp_path / "test.ipynb").read_text(encoding="utf-8"))
        assert saved["cells"][0]["id"]

    @pytest.mark.asyncio
    async def test_insert_markdown_cell(self, tool, tmp_path) -> None:
        path = _write_notebook(tmp_path, "test.ipynb", _make_notebook([_code_cell("code")]))

        await tool.execute(
            path=path,
            cell_index=0,
            new_source="# Title",
            edit_mode="insert",
            cell_type="markdown",
        )

        saved = json.loads((tmp_path / "test.ipynb").read_text(encoding="utf-8"))
        assert saved["cells"][1]["cell_type"] == "markdown"

    @pytest.mark.asyncio
    async def test_replace_can_switch_code_cell_to_markdown(self, tool, tmp_path) -> None:
        path = _write_notebook(tmp_path, "test.ipynb", _make_notebook([_code_cell("code")]))

        await tool.execute(
            path=path,
            cell_index=0,
            new_source="# Notes",
            cell_type="markdown",
        )

        saved = json.loads((tmp_path / "test.ipynb").read_text(encoding="utf-8"))
        assert saved["cells"][0]["cell_type"] == "markdown"
        assert "outputs" not in saved["cells"][0]
        assert "execution_count" not in saved["cells"][0]

    @pytest.mark.asyncio
    async def test_invalid_edit_mode_rejected(self, tool, tmp_path) -> None:
        path = _write_notebook(tmp_path, "test.ipynb", _make_notebook([_code_cell("code")]))

        result = await tool.execute(path=path, cell_index=0, new_source="x", edit_mode="replcae")

        assert "Error" in result
        assert "edit_mode" in result

    @pytest.mark.asyncio
    async def test_invalid_cell_type_rejected(self, tool, tmp_path) -> None:
        path = _write_notebook(tmp_path, "test.ipynb", _make_notebook([_code_cell("code")]))

        result = await tool.execute(path=path, cell_index=0, new_source="x", cell_type="raw")

        assert "Error" in result
        assert "cell_type" in result

    @pytest.mark.asyncio
    async def test_workspace_restriction_blocks_external_notebook(self, tmp_path) -> None:
        workspace = tmp_path / "ws"
        workspace.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        notebook = outside / "test.ipynb"
        notebook.write_text(json.dumps(_make_notebook([_md_cell("hi")])), encoding="utf-8")
        tool = NotebookEditTool(workspace=workspace, allowed_dir=workspace)

        result = await tool.execute(path=str(notebook), cell_index=0, new_source="x")

        assert "Error" in result
        assert "outside" in result.lower()
