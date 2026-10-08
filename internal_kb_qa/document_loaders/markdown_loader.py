"""Markdown parser for T2."""

from __future__ import annotations

from pathlib import Path

from ._common import document_type_for_path, get_logger, make_document, read_utf8


class MarkdownLoader:
    """Keep headings, tables and fenced code blocks as plain text."""

    def load(self, file_path: str) -> list:
        path = Path(file_path)
        log = get_logger()
        try:
            document = make_document(
                read_utf8(path), path, 1, document_type_for_path(path)
            )
        except Exception:
            log.exception("Failed to parse Markdown: source=%s", path)
            raise
        documents = [document] if document is not None else []
        if not documents:
            log.warning("Parsed empty Markdown document: source=%s", path.name)
        log.info("Parsed Markdown: source=%s documents=%s", path.name, len(documents))
        return documents
