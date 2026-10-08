"""T2 多格式文档解析器。

统一对外接口（任务分工 T2 契约）：
    load(file_path: str) -> list[Document]

支持的内部文档格式：
- Confluence / Wiki 导出 HTML
- Word (.docx)
- Markdown (.md)
- PDF (.pdf)
- OpenAPI / Swagger JSON
"""
from .confluence_loader import ConfluenceLoader
from .markdown_loader import MarkdownLoader
from .openapi_loader import OpenAPILoader
from .pdf_loader import PDFLoader
from .word_loader import WordLoader
from pathlib import Path

from ._common import get_logger

__all__ = [
    "load",
    "ConfluenceLoader",
    "MarkdownLoader",
    "OpenAPILoader",
    "PDFLoader",
    "WordLoader",
]


_LOADERS = {
    ".pdf": PDFLoader,
    ".docx": WordLoader,
    ".md": MarkdownLoader,
    ".markdown": MarkdownLoader,
    ".html": ConfluenceLoader,
    ".htm": ConfluenceLoader,
    ".json": OpenAPILoader,
}


def load(file_path: str) -> list:
    """按扩展名分发到对应 loader，返回标准 Document 列表。"""
    path = Path(file_path)
    log = get_logger()
    if not path.exists():
        log.error("Document file does not exist: source=%s", path)
        raise FileNotFoundError(f"Document file does not exist: {path}")
    if not path.is_file():
        log.error("Document path is not a file: source=%s", path)
        raise ValueError(f"Document path is not a file: {path}")
    loader_type = _LOADERS.get(path.suffix.casefold())
    if loader_type is None:
        supported = ", ".join(sorted(_LOADERS))
        log.error("Unsupported document extension: source=%s", path)
        raise ValueError(f"Unsupported document extension '{path.suffix}'. Supported: {supported}")
    log.info("Dispatching document loader: source=%s loader=%s", path.name, loader_type.__name__)
    return loader_type().load(str(path))
