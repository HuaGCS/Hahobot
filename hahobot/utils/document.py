"""Document text extraction utilities for hahobot."""

from __future__ import annotations

import html
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from loguru import logger

_MAX_TEXT_LENGTH = 200_000
_TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".csv",
    ".json",
    ".xml",
    ".html",
    ".htm",
    ".log",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
}


def extract_text(path: Path | str) -> str | None:
    """Extract readable text from supported document/text files."""
    path = Path(path)
    if not path.exists():
        return f"[error: file not found: {path}]"

    ext = path.suffix.lower()
    if ext == ".docx":
        return _extract_docx(path)
    if ext == ".xlsx":
        return _extract_xlsx(path)
    if ext == ".pptx":
        return _extract_pptx(path)
    if ext in _TEXT_EXTENSIONS:
        return _extract_text_file(path)
    return None


def _extract_docx(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as zf:
            parts = [
                name for name in zf.namelist()
                if name == "word/document.xml"
                or name.startswith("word/header")
                or name.startswith("word/footer")
            ]
            chunks = [_paragraph_texts(zf.read(name)) for name in parts]
        return _truncate("\n\n".join(chunk for chunk in chunks if chunk), _MAX_TEXT_LENGTH)
    except Exception as exc:
        logger.error("Failed to extract DOCX {}: {}", path, exc)
        return f"[error: failed to extract DOCX: {exc!s}]"


def _extract_xlsx(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as zf:
            shared_strings = _xlsx_shared_strings(zf)
            workbook_names = _xlsx_sheet_names(zf)
            sheet_files = sorted(
                name for name in zf.namelist()
                if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name)
            )
            sheets: list[str] = []
            for index, name in enumerate(sheet_files, 1):
                title = workbook_names.get(index, f"Sheet{index}")
                rows = _xlsx_rows(zf.read(name), shared_strings)
                if rows:
                    sheets.append(f"--- Sheet: {title} ---\n" + "\n".join(rows))
        return _truncate("\n\n".join(sheets), _MAX_TEXT_LENGTH)
    except Exception as exc:
        logger.error("Failed to extract XLSX {}: {}", path, exc)
        return f"[error: failed to extract XLSX: {exc!s}]"


def _extract_pptx(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as zf:
            slide_files = sorted(
                name for name in zf.namelist()
                if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)
            )
            slides: list[str] = []
            for index, name in enumerate(slide_files, 1):
                text = _all_text_nodes(zf.read(name))
                if text:
                    slides.append(f"--- Slide {index} ---\n" + "\n".join(text))
        return _truncate("\n\n".join(slides), _MAX_TEXT_LENGTH)
    except Exception as exc:
        logger.error("Failed to extract PPTX {}: {}", path, exc)
        return f"[error: failed to extract PPTX: {exc!s}]"


def _paragraph_texts(xml_bytes: bytes) -> str:
    root = ET.fromstring(xml_bytes)
    paragraphs: list[str] = []
    for paragraph in root.iter(_qname("w", "p")):
        texts = [node.text or "" for node in paragraph.iter(_qname("w", "t"))]
        line = "".join(texts).strip()
        if line:
            paragraphs.append(line)
    return "\n".join(paragraphs)


def _xlsx_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    try:
        root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    return ["".join(node.text or "" for node in item.iter(_qname("a", "t"))) for item in root]


def _xlsx_sheet_names(zf: zipfile.ZipFile) -> dict[int, str]:
    try:
        root = ET.fromstring(zf.read("xl/workbook.xml"))
    except KeyError:
        return {}
    names: dict[int, str] = {}
    for index, sheet in enumerate(root.iter(_qname("a", "sheet")), 1):
        if name := sheet.attrib.get("name"):
            names[index] = name
    return names


def _xlsx_rows(xml_bytes: bytes, shared_strings: list[str]) -> list[str]:
    root = ET.fromstring(xml_bytes)
    rows: list[str] = []
    for row in root.iter(_qname("a", "row")):
        cells: list[str] = []
        for cell in row.iter(_qname("a", "c")):
            cells.append(_xlsx_cell_text(cell, shared_strings))
        line = "\t".join(cells).rstrip()
        if line.strip():
            rows.append(line)
    return rows


def _xlsx_cell_text(cell: ET.Element, shared_strings: list[str]) -> str:
    value = cell.find(_qname("a", "v"))
    if value is None or value.text is None:
        inline_text = cell.find(f".//{_qname('a', 't')}")
        return inline_text.text if inline_text is not None and inline_text.text else ""
    if cell.attrib.get("t") == "s":
        try:
            return shared_strings[int(value.text)]
        except (ValueError, IndexError):
            return value.text
    return value.text


def _all_text_nodes(xml_bytes: bytes) -> list[str]:
    root = ET.fromstring(xml_bytes)
    lines: list[str] = []
    for node in root.iter(_qname("a", "t")):
        text = (node.text or "").strip()
        if text:
            lines.append(text)
    return lines


def _extract_text_file(path: Path) -> str:
    try:
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            content = path.read_text(encoding="latin-1")
        if path.suffix.lower() in {".html", ".htm", ".xml"}:
            content = html.unescape(re.sub(r"<[^>]+>", " ", content))
        return _truncate(content, _MAX_TEXT_LENGTH)
    except Exception as exc:
        logger.error("Failed to read text file {}: {}", path, exc)
        return f"[error: failed to read file: {exc!s}]"


def _truncate(text: str, max_length: int) -> str:
    if len(text) <= max_length:
        return text
    return text[:max_length] + f"... (truncated, {len(text)} chars total)"


def _qname(kind: str, local: str) -> str:
    namespaces = {
        "a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
        "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    }
    return f"{{{namespaces[kind]}}}{local}"
