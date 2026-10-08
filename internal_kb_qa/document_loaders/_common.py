"""Shared types, text handling and logging for T2 document loaders."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from langchain_core.documents import Document

from base.logger import logger


_FILE_HANDLER_MARKER = "_document_loader_file_handler"


def get_logger() -> logging.Logger:
    """Return the project logger with a file handler configured once."""
    if not any(getattr(handler, _FILE_HANDLER_MARKER, False) for handler in logger.handlers):
        log_dir = Path(__file__).resolve().parents[2] / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_dir / "document_loaders.log", encoding="utf-8")
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        )
        setattr(file_handler, _FILE_HANDLER_MARKER, True)
        logger.addHandler(file_handler)
    return logger


def normalize_text(text: str) -> str:
    """Normalize line endings and surrounding whitespace without changing code indentation."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ")
    lines = [line.rstrip() for line in text.split("\n")]
    normalized = "\n".join(lines)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


def document_type_for_path(file_path: str | Path, default: str = "wiki") -> str:
    """Infer the business document type from a filename when it is unambiguous."""
    name = Path(file_path).stem.casefold()
    if "runbook" in name or "值班" in name or "手册" in name:
        return "runbook"
    if "faq" in name or "常见问题" in name or "常见问答" in name:
        return "faq"
    if "report" in name or "报告" in name or "复盘" in name:
        return "report"
    return default


def source_name(file_path: str | Path) -> str:
    return Path(file_path).name


def make_document(
    text: str,
    file_path: str | Path,
    page: int,
    doc_type: str | None = None,
) -> Document | None:
    """Create a normalized Document, returning None for content without text."""
    normalized = normalize_text(text)
    if not normalized:
        return None
    return Document(
        page_content=normalized,
        metadata={
            "source": source_name(file_path),
            "page": page,
            "doc_type": doc_type or document_type_for_path(file_path),
        },
    )


def read_utf8(file_path: str | Path) -> str:
    return Path(file_path).read_text(encoding="utf-8-sig")


def markdown_table(rows: list[list[str]]) -> str:
    """Render a Word table in a stable Markdown-like form for downstream splitting."""
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    padded = [row + [""] * (width - len(row)) for row in rows]
    header = padded[0]
    separator = ["---"] * width
    output = [
        "| " + " | ".join(cell.replace("|", "\\|") for cell in row) + " |"
        for row in [header, separator, *padded[1:]]
    ]
    return "\n".join(output)
