"""Confluence/Wiki HTML parser for T2, using BeautifulSoup."""

from __future__ import annotations

import re
from pathlib import Path

from bs4 import BeautifulSoup

from ._common import document_type_for_path, get_logger, make_document, read_utf8


_NOISE_TAGS = ("script", "style", "nav", "header", "footer", "aside", "noscript")
_NOISE_NAME_RE = re.compile(r"(?:nav|sidebar|side-bar|breadcrumb|footer|header|comment)", re.I)


class ConfluenceLoader:
    """Extract the readable HTML body while preserving code and table text."""

    def load(self, file_path: str) -> list:
        path = Path(file_path)
        log = get_logger()
        try:
            soup = BeautifulSoup(read_utf8(path), "html.parser")
            for element in soup(_NOISE_TAGS):
                element.decompose()
            for element in soup.find_all(True):
                identity = " ".join(
                    [str(element.get("id", "")), " ".join(element.get("class", []))]
                )
                if _NOISE_NAME_RE.search(identity):
                    element.decompose()
            root = soup.find("main") or soup.find("article") or soup.body or soup
            document = make_document(
                root.get_text("\n"), path, 1, document_type_for_path(path)
            )
        except Exception:
            log.exception("Failed to parse Confluence HTML: source=%s", path)
            raise
        documents = [document] if document is not None else []
        if not documents:
            log.warning("Parsed empty Confluence HTML: source=%s", path.name)
        log.info("Parsed Confluence HTML: source=%s documents=%s", path.name, len(documents))
        return documents
