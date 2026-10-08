"""Upload format validation and optional ClamAV scanning."""

import asyncio
import json
import re
import socket
import struct
import zipfile
from pathlib import Path, PurePosixPath

from fastapi import HTTPException

from ..core.config import settings

_DECLARED_MIME_TYPES = {
    ".pdf": {"application/pdf"},
    ".docx": {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/zip",
        "application/x-zip-compressed",
    },
    ".md": {"text/markdown", "text/plain"},
    ".markdown": {"text/markdown", "text/plain"},
    ".html": {"text/html", "text/plain"},
    ".htm": {"text/html", "text/plain"},
    ".json": {"application/json", "text/json", "text/plain"},
}
_DETECTED_MIME_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".html": "text/html",
    ".htm": "text/html",
    ".json": "application/json",
}
_ACTIVE_CONTENT = re.compile(
    r"<(?:script|iframe|object|embed|applet)\b|"
    r"\bon(?:load|error|click|focus|mouseover)\s*=|"
    r"javascript\s*:|<meta\b[^>]*http-equiv\s*=\s*['\"]?refresh",
    re.IGNORECASE,
)
_DANGEROUS_DOCX_PARTS = ("vbaproject.bin", "word/activex/", "word/embeddings/")


def _error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def _validate_declared_mime(suffix: str, declared_mime: str | None) -> None:
    mime = (declared_mime or "").split(";", 1)[0].strip().lower()
    if not mime or mime == "application/octet-stream":
        return
    if mime not in _DECLARED_MIME_TYPES[suffix]:
        raise _error(415, "FILE_TYPE_MISMATCH", "文件扩展名与声明的 MIME 类型不一致")


def _validate_pdf(path: Path) -> None:
    with path.open("rb") as source:
        if source.read(5) != b"%PDF-":
            raise _error(415, "INVALID_PDF", "文件扩展名为 PDF，但内容不是有效 PDF")
        source.seek(max(0, path.stat().st_size - 4096))
        if b"%%EOF" not in source.read():
            raise _error(415, "INVALID_PDF", "PDF 文件结构不完整")


def _validate_docx(path: Path) -> None:
    if not zipfile.is_zipfile(path):
        raise _error(415, "INVALID_DOCX", "文件扩展名为 DOCX，但内容不是有效 Office 文档")
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            if len(members) > settings.DOCUMENT_ARCHIVE_MAX_FILES:
                raise _error(422, "ARCHIVE_BOMB", "DOCX 内部文件数量超过安全限制")
            total_size = sum(member.file_size for member in members)
            compressed_size = sum(member.compress_size for member in members)
            if total_size > settings.DOCUMENT_ARCHIVE_MAX_UNCOMPRESSED:
                raise _error(422, "ARCHIVE_BOMB", "DOCX 解压后大小超过安全限制")
            if total_size / max(1, compressed_size) > settings.DOCUMENT_ARCHIVE_MAX_RATIO:
                raise _error(422, "ARCHIVE_BOMB", "DOCX 压缩比超过安全限制")

            names = {member.filename.replace("\\", "/") for member in members}
            lowered = {name.lower() for name in names}
            if "[content_types].xml" not in lowered or "word/document.xml" not in lowered:
                raise _error(415, "INVALID_DOCX", "DOCX 缺少必要的文档结构")
            for member in members:
                normalized = member.filename.replace("\\", "/")
                parts = PurePosixPath(normalized).parts
                if normalized.startswith("/") or ".." in parts:
                    raise _error(422, "UNSAFE_ARCHIVE_PATH", "DOCX 包含不安全路径")
                if member.flag_bits & 0x1:
                    raise _error(422, "ENCRYPTED_DOCUMENT", "不支持加密 DOCX")
                lowered_name = normalized.lower()
                if any(part in lowered_name for part in _DANGEROUS_DOCX_PARTS):
                    raise _error(422, "ACTIVE_DOCUMENT_CONTENT", "DOCX 包含宏或嵌入式活动内容")
    except zipfile.BadZipFile as exc:
        raise _error(415, "INVALID_DOCX", "DOCX 压缩结构损坏") from exc


def _read_safe_text(path: Path) -> str:
    try:
        content = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise _error(415, "INVALID_TEXT_ENCODING", "文本文件必须使用 UTF-8 编码") from exc
    if "\x00" in content:
        raise _error(415, "BINARY_CONTENT", "文本文件中检测到二进制内容")
    return content


def validate_structure(path: Path, suffix: str, declared_mime: str | None) -> str:
    suffix = suffix.casefold()
    if suffix not in _DETECTED_MIME_TYPES:
        raise _error(415, "UNSUPPORTED_FILE_TYPE", "文件类型不受支持")
    _validate_declared_mime(suffix, declared_mime)
    if suffix == ".pdf":
        _validate_pdf(path)
    elif suffix == ".docx":
        _validate_docx(path)
    else:
        content = _read_safe_text(path)
        if suffix == ".json":
            try:
                json.loads(content)
            except (json.JSONDecodeError, RecursionError) as exc:
                raise _error(415, "INVALID_JSON", "JSON 文件结构无效") from exc
        if suffix in {".html", ".htm", ".md", ".markdown"} and _ACTIVE_CONTENT.search(content):
            raise _error(422, "ACTIVE_DOCUMENT_CONTENT", "文档包含脚本或活动网页内容")
    return _DETECTED_MIME_TYPES[suffix]


def _clamav_scan(path: Path) -> None:
    try:
        with socket.create_connection(
            (settings.CLAMAV_HOST, settings.CLAMAV_PORT),
            timeout=settings.CLAMAV_TIMEOUT_SECONDS,
        ) as client:
            client.settimeout(settings.CLAMAV_TIMEOUT_SECONDS)
            client.sendall(b"zINSTREAM\0")
            with path.open("rb") as source:
                while chunk := source.read(1024 * 1024):
                    client.sendall(struct.pack(">I", len(chunk)))
                    client.sendall(chunk)
            client.sendall(struct.pack(">I", 0))
            response = client.recv(4096).rstrip(b"\0").decode("utf-8", errors="replace")
    except (OSError, TimeoutError) as exc:
        raise _error(503, "FILE_SCANNER_UNAVAILABLE", "文件安全扫描服务不可用") from exc
    if response.endswith(" FOUND"):
        signature = response.rsplit(": ", 1)[-1].removesuffix(" FOUND")
        raise _error(422, "MALWARE_DETECTED", f"文件安全扫描未通过：{signature}")
    if not response.endswith(" OK"):
        raise _error(503, "FILE_SCANNER_ERROR", "文件安全扫描返回异常结果")


async def validate_and_scan(path: Path, suffix: str, declared_mime: str | None) -> dict:
    detected_mime = await asyncio.to_thread(
        validate_structure, path, suffix, declared_mime
    )
    if settings.CLAMAV_ENABLED:
        await asyncio.to_thread(_clamav_scan, path)
    return {
        "mime_type": detected_mime,
        "scanner": "clamav" if settings.CLAMAV_ENABLED else "structural",
    }
