import asyncio
import zipfile
from pathlib import Path

import pytest
from fastapi import HTTPException

from backend.app.core.access_control import can_access_content
from backend.app.core.config import Settings, read_secret
from backend.app.core.rate_limit import enforce, limit_key
from backend.app.services import file_security_service
from backend.app.services.file_security_service import validate_structure


class FakeRedis:
    def __init__(self):
        self.counts = {}

    async def eval(self, _script, _keys, key, window):
        self.counts[key] = self.counts.get(key, 0) + 1
        return [self.counts[key], window]


def error_code(exc: HTTPException) -> str:
    return exc.detail["code"]


def test_rate_limit_hashes_identifiers_and_rejects_excess() -> None:
    redis = FakeRedis()
    assert "13800138000" not in limit_key("sms", "13800138000")
    asyncio.run(enforce(redis, "login", "account", 1, 60))
    with pytest.raises(HTTPException) as caught:
        asyncio.run(enforce(redis, "login", "account", 1, 60))
    assert caught.value.status_code == 429
    assert error_code(caught.value) == "RATE_LIMITED"


def test_pdf_signature_and_eof_are_required(tmp_path: Path) -> None:
    valid = tmp_path / "valid.pdf"
    valid.write_bytes(b"%PDF-1.7\nbody\n%%EOF")
    assert validate_structure(valid, ".pdf", "application/pdf") == "application/pdf"

    forged = tmp_path / "forged.pdf"
    forged.write_bytes(b"MZ executable")
    with pytest.raises(HTTPException) as caught:
        validate_structure(forged, ".pdf", "application/pdf")
    assert error_code(caught.value) == "INVALID_PDF"


def test_docx_structure_and_active_parts_are_checked(tmp_path: Path) -> None:
    valid = tmp_path / "valid.docx"
    with zipfile.ZipFile(valid, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types />")
        archive.writestr("word/document.xml", "<document />")
    assert "wordprocessingml" in validate_structure(valid, ".docx", None)

    active = tmp_path / "active.docx"
    with zipfile.ZipFile(active, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types />")
        archive.writestr("word/document.xml", "<document />")
        archive.writestr("word/vbaProject.bin", b"macro")
    with pytest.raises(HTTPException) as caught:
        validate_structure(active, ".docx", None)
    assert error_code(caught.value) == "ACTIVE_DOCUMENT_CONTENT"


def test_text_files_reject_active_or_binary_content(tmp_path: Path) -> None:
    active = tmp_path / "active.md"
    active.write_text("# title\n<script>alert(1)</script>", encoding="utf-8")
    with pytest.raises(HTTPException) as caught:
        validate_structure(active, ".md", "text/markdown")
    assert error_code(caught.value) == "ACTIVE_DOCUMENT_CONTENT"

    binary = tmp_path / "binary.md"
    binary.write_bytes(b"hello\x00world")
    with pytest.raises(HTTPException) as caught:
        validate_structure(binary, ".md", "text/plain")
    assert error_code(caught.value) == "BINARY_CONTENT"


def test_clamav_adapter_runs_when_enabled(tmp_path: Path, monkeypatch) -> None:
    document = tmp_path / "safe.md"
    document.write_text("# safe", encoding="utf-8")
    scanned = []
    monkeypatch.setattr(file_security_service.settings, "CLAMAV_ENABLED", True)
    monkeypatch.setattr(file_security_service, "_clamav_scan", scanned.append)

    result = asyncio.run(
        file_security_service.validate_and_scan(document, ".md", "text/markdown")
    )

    assert scanned == [document]
    assert result == {"mime_type": "text/markdown", "scanner": "clamav"}


def test_secret_file_is_exclusive_and_trims_only_line_endings(
    tmp_path: Path, monkeypatch
) -> None:
    secret_file = tmp_path / "jwt-secret"
    secret_file.write_text("independent secret with spaces  \n", encoding="utf-8")
    monkeypatch.delenv("TEST_SECRET", raising=False)
    monkeypatch.setenv("TEST_SECRET_FILE", str(secret_file))
    assert read_secret("TEST_SECRET") == "independent secret with spaces  "

    monkeypatch.setenv("TEST_SECRET", "ambiguous")
    with pytest.raises(RuntimeError, match="不能同时配置"):
        read_secret("TEST_SECRET")


def test_content_access_requires_clearance_and_matching_team() -> None:
    user = {"security_level": "team", "team": "platform"}
    assert can_access_content(user, "public", "security")
    assert can_access_content(user, "team", "platform")
    assert not can_access_content(user, "team", "security")
    assert not can_access_content(user, "confidential", "platform")


def test_production_settings_accept_independent_secret_files(
    tmp_path: Path, monkeypatch
) -> None:
    values = {
        "POSTGRES_PASSWORD": "postgres-production-password",
        "REDIS_PASSWORD": "redis-production-password",
        "JWT_SECRET": "jwt-production-secret-with-at-least-32-bytes",
        "MINIO_ACCESS_KEY": "devmind-production",
        "MINIO_SECRET_KEY": "minio-production-secret",
        "METRICS_TOKEN": "metrics-production-token-123456",
    }
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("APP_DEBUG", "false")
    monkeypatch.setenv("CLAMAV_ENABLED", "true")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL_FILE", raising=False)
    for name, value in values.items():
        secret_file = tmp_path / name.casefold()
        secret_file.write_text(value + "\n", encoding="utf-8")
        monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv(f"{name}_FILE", str(secret_file))

    loaded = Settings()

    assert loaded.POSTGRES_PASSWORD == values["POSTGRES_PASSWORD"]
    assert loaded.REDIS_PASSWORD == values["REDIS_PASSWORD"]
    assert loaded.JWT_SECRET == values["JWT_SECRET"]
    assert loaded.MINIO_SECRET_KEY == values["MINIO_SECRET_KEY"]
