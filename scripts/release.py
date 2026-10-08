"""Production init, upgrade, validation and application rollback."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from backend.app.core.config import PROJECT_ROOT

BASE = PROJECT_ROOT / "docker/docker-compose.yml"
PRODUCTION = PROJECT_ROOT / "docker/docker-compose.production.yml"
SECRET_RULES = {
    "postgres_password": 12,
    "redis_password": 16,
    "minio_access_key": 3,
    "minio_secret_key": 16,
    "jwt_secret": 32,
    "dashscope_api_key": 1,
    "metrics_token": 24,
}


def read_env(path: Path) -> dict[str, str]:
    values = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def command(args, *parts: str, env: dict[str, str] | None = None) -> None:
    print("+", " ".join(parts))
    if not args.dry_run:
        subprocess.run(parts, check=True, env=env)


def runtime(args) -> tuple[dict[str, str], Path]:
    env_file = Path(args.env_file).resolve()
    if not env_file.is_file():
        raise RuntimeError(f"生产环境文件不存在: {env_file}")
    values = read_env(env_file)
    secret_dir = Path(values.get("DEVMIND_SECRETS_DIR", "./secrets"))
    if not secret_dir.is_absolute():
        secret_dir = (env_file.parent / secret_dir).resolve()
    environment = os.environ.copy()
    environment.update(values)
    return environment, secret_dir


def validate(args) -> tuple[dict[str, str], Path]:
    environment, secret_dir = runtime(args)
    if not environment.get("CORS_ORIGINS", "").startswith("https://"):
        raise RuntimeError("生产 CORS_ORIGINS 必须使用 https://")
    for name, minimum in SECRET_RULES.items():
        path = secret_dir / name
        if not path.is_file():
            raise RuntimeError(f"缺少生产密钥文件: {path}")
        if len(path.read_text(encoding="utf-8").rstrip("\r\n")) < minimum:
            raise RuntimeError(f"生产密钥长度不足: {name}")
    command(
        args,
        "docker",
        "compose",
        "--env-file",
        str(Path(args.env_file).resolve()),
        "-f",
        str(BASE),
        "-f",
        str(PRODUCTION),
        "config",
        "--quiet",
        env=environment,
    )
    return environment, secret_dir


def compose(args, environment: dict[str, str], *parts: str) -> None:
    command(
        args,
        "docker",
        "compose",
        "--env-file",
        str(Path(args.env_file).resolve()),
        "-f",
        str(BASE),
        "-f",
        str(PRODUCTION),
        *parts,
        env=environment,
    )


def migrate(args, environment: dict[str, str]) -> None:
    command(
        args,
        sys.executable,
        str(PROJECT_ROOT / "scripts/migrate.py"),
        "apply",
        "--env-file",
        str(Path(args.env_file).resolve()),
        "--compose-file",
        str(BASE),
        "--compose-file",
        str(PRODUCTION),
        "--user",
        environment.get("POSTGRES_USER", "devmind"),
        "--database",
        environment.get("POSTGRES_DB", "internal_tech_kb"),
        env=environment,
    )


def backup(args, environment: dict[str, str], secret_dir: Path) -> None:
    backup_env = environment.copy()
    backup_env.update(
        {
            "APP_ENV": "development",
            "POSTGRES_HOST": "127.0.0.1",
            "POSTGRES_PORT": "15432",
            "POSTGRES_PASSWORD": (secret_dir / "postgres_password").read_text().strip(),
            "MINIO_ENDPOINT": "127.0.0.1:9000",
            "MINIO_ACCESS_KEY": (secret_dir / "minio_access_key").read_text().strip(),
            "MINIO_SECRET_KEY": (secret_dir / "minio_secret_key").read_text().strip(),
            "MILVUS_HOST": "127.0.0.1",
        }
    )
    command(
        args,
        sys.executable,
        str(PROJECT_ROOT / "scripts/backup.py"),
        "create",
        "--output",
        str(PROJECT_ROOT / "backups"),
        env=backup_env,
    )


def execute(args) -> None:
    environment, secret_dir = validate(args)
    if args.backend_image:
        environment["BACKEND_IMAGE"] = args.backend_image
    if args.web_image:
        environment["WEB_IMAGE"] = args.web_image
    if args.action == "validate":
        return
    if args.action == "init":
        compose(
            args,
            environment,
            "up",
            "-d",
            "--wait",
            "postgres",
            "redis",
            "clamav",
            "etcd",
            "minio",
            "milvus",
        )
        migrate(args, environment)
        compose(args, environment, "up", "-d", "--build", "--wait", "backend", "worker", "web")
        return
    if args.action == "upgrade":
        backup(args, environment, secret_dir)
        compose(args, environment, "build", "backend", "web")
        migrate(args, environment)
        compose(args, environment, "up", "-d", "--wait", "backend", "worker", "web")
        return
    if not args.backend_image or not args.web_image:
        raise RuntimeError("rollback 必须明确传入上一版本 backend 和 web 镜像")
    compose(args, environment, "up", "-d", "--no-build", "--wait", "backend", "worker", "web")


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser()
    cli.add_argument("action", choices=["validate", "init", "upgrade", "rollback"])
    cli.add_argument("--env-file", default=str(PROJECT_ROOT / "docker/production.env"))
    cli.add_argument("--backend-image")
    cli.add_argument("--web-image")
    cli.add_argument("--dry-run", action="store_true")
    return cli


if __name__ == "__main__":
    try:
        execute(parser().parse_args())
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"release failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
