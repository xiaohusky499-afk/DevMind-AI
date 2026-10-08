"""Versioned PostgreSQL migration runner for Docker Compose deployments."""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from backend.app.core.config import PROJECT_ROOT

MIGRATION_NAME = re.compile(r"^(\d{3})_[A-Za-z0-9_.-]+\.sql$")


@dataclass(frozen=True)
class Migration:
    version: str
    name: str
    path: Path
    checksum: str


def discover(directory: Path) -> list[Migration]:
    migrations = []
    for path in sorted(directory.glob("*.sql")):
        match = MIGRATION_NAME.fullmatch(path.name)
        if match:
            migrations.append(
                Migration(
                    match.group(1),
                    path.name,
                    path,
                    hashlib.sha256(path.read_bytes()).hexdigest(),
                )
            )
    versions = [item.version for item in migrations]
    if len(versions) != len(set(versions)):
        raise RuntimeError("迁移版本号重复")
    return migrations


def compose_command(files: list[str], env_file: str | None) -> list[str]:
    command = ["docker", "compose"]
    if env_file:
        command.extend(["--env-file", env_file])
    for file_name in files:
        command.extend(["-f", file_name])
    return command


def psql(args, sql: str, capture: bool = False) -> str:
    command = compose_command(args.compose_file, args.env_file)
    command.extend(
        [
            "exec",
            "-T",
            "postgres",
            "psql",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            args.user,
            "-d",
            args.database,
            "-At",
        ]
    )
    result = subprocess.run(
        command,
        input=sql,
        text=True,
        encoding="utf-8",
        capture_output=capture,
        check=True,
    )
    return result.stdout.strip() if capture else ""


def load_applied(args) -> dict[str, str]:
    psql(
        args,
        """CREATE TABLE IF NOT EXISTS schema_migrations (
            version varchar(16) PRIMARY KEY,
            name varchar(255) NOT NULL,
            checksum char(64) NOT NULL,
            applied_at timestamptz NOT NULL DEFAULT now()
        );""",
    )
    rows = psql(
        args,
        "SELECT version || E'\\t' || checksum FROM schema_migrations ORDER BY version;",
        capture=True,
    )
    return dict(line.split("\t", 1) for line in rows.splitlines() if line)


def pending(migrations: list[Migration], applied: dict[str, str]) -> list[Migration]:
    for migration in migrations:
        recorded = applied.get(migration.version)
        if recorded and recorded != migration.checksum:
            raise RuntimeError(f"已执行迁移被修改: {migration.name}")
    return [item for item in migrations if item.version not in applied]


def execute(args) -> None:
    migrations = discover(Path(args.directory).resolve())
    waiting = pending(migrations, load_applied(args))
    if args.command in {"plan", "status"}:
        print("\n".join(item.name for item in waiting) or "数据库已是最新版本")
        return
    for migration in waiting:
        sql = migration.path.read_text(encoding="utf-8")
        record = (
            "\nINSERT INTO schema_migrations(version,name,checksum) VALUES "
            f"('{migration.version}','{migration.name}','{migration.checksum}');\n"
        )
        psql(args, sql + record)
        print(f"applied {migration.name}")


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser()
    cli.add_argument("command", choices=["plan", "apply", "status"])
    cli.add_argument(
        "--compose-file",
        action="append",
        default=None,
        help="可重复传入；默认 docker/docker-compose.yml",
    )
    cli.add_argument("--env-file")
    cli.add_argument("--directory", default=str(PROJECT_ROOT / "sql/migrations"))
    cli.add_argument("--user", default="devmind")
    cli.add_argument("--database", default="internal_tech_kb")
    return cli


if __name__ == "__main__":
    arguments = parser().parse_args()
    arguments.compose_file = arguments.compose_file or [
        str(PROJECT_ROOT / "docker/docker-compose.yml")
    ]
    execute(arguments)
