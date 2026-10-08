import json
from pathlib import Path

import pytest

from scripts.backup import create_manifest, verify


def test_backup_manifest_detects_tampering_and_extra_files(tmp_path: Path) -> None:
    payload = tmp_path / "postgres.dump"
    payload.write_bytes(b"valid backup")
    create_manifest(tmp_path, {"postgresql": {"database": "test"}})

    assert verify(tmp_path)["components"]["postgresql"]["database"] == "test"

    payload.write_bytes(b"tampered")
    with pytest.raises(RuntimeError, match="校验失败"):
        verify(tmp_path)

    payload.write_bytes(b"valid backup")
    (tmp_path / "unexpected").write_text("unexpected", encoding="utf-8")
    with pytest.raises(RuntimeError, match="文件集合"):
        verify(tmp_path)


def test_manifest_includes_nested_vendor_manifest(tmp_path: Path) -> None:
    nested = tmp_path / "milvus" / "manifest.json"
    nested.parent.mkdir()
    nested.write_text(json.dumps({"vendor": True}), encoding="utf-8")

    manifest = create_manifest(tmp_path, {})

    assert "milvus/manifest.json" in {item["path"] for item in manifest["files"]}
