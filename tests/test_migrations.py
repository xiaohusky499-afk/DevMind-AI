from pathlib import Path

import pytest

from scripts.migrate import discover, pending


def test_migration_plan_and_checksum_guard(tmp_path: Path):
    first = tmp_path / "002_first.sql"
    second = tmp_path / "003_second.sql"
    first.write_text("SELECT 1;", encoding="utf-8")
    second.write_text("SELECT 2;", encoding="utf-8")
    migrations = discover(tmp_path)

    assert [item.version for item in pending(migrations, {})] == ["002", "003"]
    assert pending(migrations, {"002": migrations[0].checksum}) == [migrations[1]]
    with pytest.raises(RuntimeError, match="已执行迁移被修改"):
        pending(migrations, {"002": "0" * 64})
