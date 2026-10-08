"""Word parser for T2, using python-docx."""

from __future__ import annotations

from pathlib import Path

from docx import Document as DocxDocument
from docx.table import Table
from docx.text.paragraph import Paragraph

from ._common import document_type_for_path, get_logger, make_document, markdown_table


def _iter_block_items(document: DocxDocument):
    """Yield paragraphs and tables in their original document order."""
    body = document.element.body
    for child in body.iterchildren():
        if child.tag.endswith("}p"):
            yield Paragraph(child, document)
        elif child.tag.endswith("}tbl"):
            yield Table(child, document)


class WordLoader:
    """Extract paragraphs and tables from a DOCX as one Document."""

    def load(self, file_path: str) -> list:
        path = Path(file_path)
        log = get_logger()
        try:
            source = DocxDocument(path)
            blocks = []
            for block in _iter_block_items(source):
                if isinstance(block, Paragraph):
                    if block.text.strip():
                        blocks.append(block.text)
                else:
                    rows = [[cell.text.strip() for cell in row.cells] for row in block.rows]
                    table = markdown_table(rows)
                    if table:
                        blocks.append(table)
            document = make_document(
                "\n\n".join(blocks), path, 1, document_type_for_path(path)
            )
        except Exception:
            log.exception("Failed to parse Word document: source=%s", path)
            raise
        documents = [document] if document is not None else []
        if not documents:
            log.warning("Parsed empty Word document: source=%s", path.name)
        log.info("Parsed Word document: source=%s documents=%s", path.name, len(documents))
        return documents
