"""PDF parser for T2, using PyMuPDF."""

from __future__ import annotations

from pathlib import Path

import fitz

from ._common import document_type_for_path, get_logger, make_document


class PDFLoader:
    """Extract one normalized Document per non-empty PDF page."""

    def load(self, file_path: str) -> list:
        path = Path(file_path)
        log = get_logger()
        documents = []
        try:
            with fitz.open(path) as pdf:
                for page_number, page in enumerate(pdf, start=1):
                    document = make_document(
                        page.get_text("text"),
                        path,
                        page_number,
                        document_type_for_path(path),
                    )
                    if document is None:
                        log.warning("Skipped empty PDF page: source=%s page=%s", path.name, page_number)
                        continue
                    documents.append(document)
        except Exception:
            log.exception("Failed to parse PDF: source=%s", path)
            raise
        log.info("Parsed PDF: source=%s pages=%s documents=%s", path.name, len(documents), len(documents))
        return documents
