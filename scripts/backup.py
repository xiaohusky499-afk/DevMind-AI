"""DevMind AI PostgreSQL / MinIO / Milvus backup and isolated restore drill."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import shutil
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg
from minio import Minio
from pymilvus import DataType, MilvusClient

from backend.app.core.config import PROJECT_ROOT, settings

POSTGRES_CONTAINER = "devmind_postgres"
_SAFE_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,62}$")


def run(*args: str, capture: bool = False) -> str:
    result = subprocess.run(
        args,
        check=True,
        text=True,
        capture_output=capture,
        encoding="utf-8",
    )
    return result.stdout.strip() if capture else ""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def source_counts(database: str) -> dict[str, int]:
    sql = """SELECT json_build_object(
        'organizations',(SELECT count(*) FROM organizations),
        'users',(SELECT count(*) FROM users),
        'knowledge_bases',(SELECT count(*) FROM knowledge_bases),
        'documents',(SELECT count(*) FROM documents),
        'document_chunks',(SELECT count(*) FROM document_chunks)
    )"""
    raw = run(
        "docker",
        "exec",
        POSTGRES_CONTAINER,
        "psql",
        "-U",
        settings.POSTGRES_USER,
        "-d",
        database,
        "-At",
        "-c",
        sql,
        capture=True,
    )
    return {key: int(value) for key, value in json.loads(raw).items()}


def postgres_backup(output: Path) -> dict[str, int]:
    remote = f"/tmp/devmind-{uuid.uuid4().hex}.dump"
    try:
        run(
            "docker",
            "exec",
            POSTGRES_CONTAINER,
            "pg_dump",
            "-U",
            settings.POSTGRES_USER,
            "-d",
            settings.POSTGRES_DB,
            "--format=custom",
            "--no-owner",
            "--file",
            remote,
        )
        run("docker", "cp", f"{POSTGRES_CONTAINER}:{remote}", str(output))
    finally:
        subprocess.run(
            ["docker", "exec", POSTGRES_CONTAINER, "rm", "-f", remote], check=False
        )
    return source_counts(settings.POSTGRES_DB)


def minio_client() -> Minio:
    return Minio(
        settings.MINIO_ENDPOINT,
        access_key=settings.MINIO_ACCESS_KEY,
        secret_key=settings.MINIO_SECRET_KEY,
        secure=settings.MINIO_SECURE,
    )


def minio_backup(output: Path) -> dict:
    client = minio_client()
    output.mkdir(parents=True)
    objects = []
    if not client.bucket_exists(settings.MINIO_BUCKET):
        raise RuntimeError(f"MinIO bucket 不存在: {settings.MINIO_BUCKET}")
    for item in client.list_objects(settings.MINIO_BUCKET, recursive=True):
        local_name = hashlib.sha256(item.object_name.encode()).hexdigest()
        local_path = output / local_name
        client.fget_object(settings.MINIO_BUCKET, item.object_name, str(local_path))
        stat = client.stat_object(settings.MINIO_BUCKET, item.object_name)
        objects.append(
            {
                "name": item.object_name,
                "file": local_name,
                "size": local_path.stat().st_size,
                "sha256": sha256(local_path),
                "content_type": stat.content_type or "application/octet-stream",
            }
        )
    snapshot = {"source_bucket": settings.MINIO_BUCKET, "objects": objects}
    (output / "objects.json").write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"bucket": settings.MINIO_BUCKET, "object_count": len(objects)}


def milvus_backup(output: Path, name: str) -> dict:
    target = output / "milvus"
    target.mkdir(parents=True, exist_ok=True)
    client = MilvusClient(
        uri=f"http://{settings.MILVUS_HOST}:{settings.MILVUS_PORT}",
        db_name=settings.MILVUS_DATABASE,
    )
    collection = settings.MILVUS_COLLECTION
    rows_file = target / "rows.jsonl"
    row_count = 0
    iterator = client.query_iterator(
        collection,
        batch_size=500,
        filter="",
        output_fields=["*"],
    )
    try:
        with rows_file.open("w", encoding="utf-8", newline="\n") as stream:
            while batch := iterator.next():
                for row in batch:
                    stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
                    stream.write("\n")
                    row_count += 1
    finally:
        iterator.close()
    expected = int(client.get_collection_stats(collection).get("row_count", 0))
    return {
        "method": "logical-jsonl-v1",
        "snapshot_name": name,
        "database": settings.MILVUS_DATABASE,
        "collections": {collection: row_count},
        "source_reported_row_count": expected,
    }


def create_manifest(root: Path, components: dict) -> dict:
    files = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path != root / "manifest.json":
            files.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "size": path.stat().st_size,
                    "sha256": sha256(path),
                }
            )
    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "components": components,
        "files": files,
    }
    (root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def verify(root: Path) -> dict:
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = {item["path"]: item for item in manifest["files"]}
    actual = {
        path.relative_to(root).as_posix(): path
        for path in root.rglob("*")
        if path.is_file() and path != root / "manifest.json"
    }
    if set(expected) != set(actual):
        raise RuntimeError("备份文件集合与 manifest 不一致")
    for name, item in expected.items():
        path = actual[name]
        if path.stat().st_size != item["size"] or sha256(path) != item["sha256"]:
            raise RuntimeError(f"备份校验失败: {name}")
    return manifest


def create(args) -> None:
    root = Path(args.output).resolve()
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    final = root / (args.name or f"devmind-{stamp}")
    if final.exists():
        raise RuntimeError(f"备份目录已存在: {final}")
    partial = root / f".{final.name}.partial-{uuid.uuid4().hex[:8]}"
    partial.mkdir(parents=True)
    try:
        backup_name = f"devmind_{stamp.replace('-', '_')}"
        components = {
            "postgresql": {
                "database": settings.POSTGRES_DB,
                "counts": postgres_backup(partial / "postgres.dump"),
            },
            "minio": minio_backup(partial / "minio"),
            "milvus": milvus_backup(partial, backup_name),
        }
        create_manifest(partial, components)
        verify(partial)
        partial.rename(final)
    except Exception:
        shutil.rmtree(partial, ignore_errors=True)
        raise
    print(final)


async def postgres_counts(database: str) -> dict[str, int]:
    connection = await asyncpg.connect(
        host=settings.POSTGRES_HOST,
        port=settings.POSTGRES_PORT,
        user=settings.POSTGRES_USER,
        password=settings.POSTGRES_PASSWORD,
        database=database,
    )
    try:
        rows = await connection.fetch(
            """SELECT name, count FROM (VALUES
               ('organizations',(SELECT count(*) FROM organizations)),
               ('users',(SELECT count(*) FROM users)),
               ('knowledge_bases',(SELECT count(*) FROM knowledge_bases)),
               ('documents',(SELECT count(*) FROM documents)),
               ('document_chunks',(SELECT count(*) FROM document_chunks))
            ) AS counts(name,count)"""
        )
        return {row["name"]: int(row["count"]) for row in rows}
    finally:
        await connection.close()


def restore_postgres(root: Path, target: str, expected: dict) -> None:
    if not _SAFE_NAME.fullmatch(target) or target == settings.POSTGRES_DB:
        raise RuntimeError("恢复数据库名称不安全或与源数据库相同")
    existing = run(
        "docker",
        "exec",
        POSTGRES_CONTAINER,
        "psql",
        "-U",
        settings.POSTGRES_USER,
        "-d",
        "postgres",
        "-At",
        "-c",
        f"SELECT 1 FROM pg_database WHERE datname='{target}'",
        capture=True,
    )
    if existing:
        raise RuntimeError(f"恢复数据库已存在: {target}")
    remote = f"/tmp/devmind-restore-{uuid.uuid4().hex}.dump"
    try:
        run("docker", "exec", POSTGRES_CONTAINER, "createdb", "-U", settings.POSTGRES_USER, target)
        run("docker", "cp", str(root / "postgres.dump"), f"{POSTGRES_CONTAINER}:{remote}")
        run(
            "docker",
            "exec",
            POSTGRES_CONTAINER,
            "pg_restore",
            "-U",
            settings.POSTGRES_USER,
            "-d",
            target,
            "--no-owner",
            "--no-privileges",
            remote,
        )
        if asyncio.run(postgres_counts(target)) != expected:
            raise RuntimeError("PostgreSQL 恢复后的核心表计数不一致")
    finally:
        subprocess.run(["docker", "exec", POSTGRES_CONTAINER, "rm", "-f", remote], check=False)


def restore_minio(root: Path, target_bucket: str, expected_count: int) -> None:
    client = minio_client()
    if client.bucket_exists(target_bucket):
        raise RuntimeError(f"恢复 Bucket 已存在: {target_bucket}")
    client.make_bucket(target_bucket)
    snapshot = json.loads((root / "minio/objects.json").read_text(encoding="utf-8"))
    for item in snapshot["objects"]:
        client.fput_object(
            target_bucket,
            item["name"],
            str(root / "minio" / item["file"]),
            content_type=item["content_type"],
        )
    restored = list(client.list_objects(target_bucket, recursive=True))
    if len(restored) != expected_count:
        raise RuntimeError("MinIO 恢复后的对象数量不一致")
    restored_sizes = {item.object_name: item.size for item in restored}
    for item in snapshot["objects"]:
        if restored_sizes.get(item["name"]) != item["size"]:
            raise RuntimeError(f"MinIO 恢复后的对象大小不一致: {item['name']}")


def create_milvus_collection(client: MilvusClient, name: str) -> None:
    schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
    schema.add_field("id", DataType.VARCHAR, is_primary=True, max_length=36)
    schema.add_field("dense_vector", DataType.FLOAT_VECTOR, dim=settings.BGE_M3_DIM)
    schema.add_field("sparse_vector", DataType.SPARSE_FLOAT_VECTOR)
    schema.add_field("text", DataType.VARCHAR, max_length=65535)
    schema.add_field("document_id", DataType.VARCHAR, max_length=36)
    schema.add_field("knowledge_base_id", DataType.VARCHAR, max_length=36)
    schema.add_field("source", DataType.VARCHAR, max_length=500)
    schema.add_field("location_label", DataType.VARCHAR, max_length=160)
    schema.add_field("parent_id", DataType.VARCHAR, max_length=36)
    schema.add_field("parent_content", DataType.VARCHAR, max_length=65535)
    schema.add_field("timestamp", DataType.VARCHAR, max_length=64)
    indexes = client.prepare_index_params()
    indexes.add_index("dense_vector", index_type="AUTOINDEX", metric_type="IP")
    indexes.add_index(
        "sparse_vector", index_type="SPARSE_INVERTED_INDEX", metric_type="IP"
    )
    client.create_collection(name, schema=schema, index_params=indexes)


def restore_milvus(root: Path, suffix: str, expected: dict) -> list[str]:
    client = MilvusClient(
        uri=f"http://{settings.MILVUS_HOST}:{settings.MILVUS_PORT}",
        db_name=settings.MILVUS_DATABASE,
    )
    restored: list[str] = []
    for source, count in expected.items():
        target = f"{source}{suffix}"
        if client.has_collection(collection_name=target):
            raise RuntimeError(f"恢复 Collection 已存在: {target}")
        create_milvus_collection(client, target)
        batch: list[dict] = []
        with (root / "milvus/rows.jsonl").open(encoding="utf-8") as stream:
            for line in stream:
                row = json.loads(line)
                row["sparse_vector"] = {
                    int(key): value for key, value in row["sparse_vector"].items()
                }
                batch.append(row)
                if len(batch) == 500:
                    client.insert(target, batch)
                    batch.clear()
        if batch:
            client.insert(target, batch)
        client.flush(target)
        stats = client.get_collection_stats(target)
        if int(stats.get("row_count", 0)) != count:
            raise RuntimeError(f"Milvus 恢复后的行数不一致: {target}")
        restored.append(target)
    return restored


def cleanup_drill(database: str, bucket: str, collections: list[str]) -> None:
    if not database.startswith("devmind_restore_") or not bucket.startswith("devmind-restore-"):
        raise RuntimeError("拒绝清理非隔离恢复资源")
    subprocess.run(
        [
            "docker",
            "exec",
            POSTGRES_CONTAINER,
            "dropdb",
            "-U",
            settings.POSTGRES_USER,
            "--force",
            "--if-exists",
            database,
        ],
        check=False,
    )
    client = minio_client()
    if client.bucket_exists(bucket):
        for item in client.list_objects(bucket, recursive=True):
            client.remove_object(bucket, item.object_name)
        client.remove_bucket(bucket)
    milvus = MilvusClient(
        uri=f"http://{settings.MILVUS_HOST}:{settings.MILVUS_PORT}",
        db_name=settings.MILVUS_DATABASE,
    )
    for collection in collections:
        if "_restore_" not in collection:
            raise RuntimeError("拒绝清理非隔离恢复 Collection")
        if milvus.has_collection(collection_name=collection):
            milvus.drop_collection(collection_name=collection)


def restore_drill(args) -> None:
    root = Path(args.input).resolve()
    manifest = verify(root)
    suffix_id = uuid.uuid4().hex[:8]
    database = f"devmind_restore_{suffix_id}"
    bucket = f"devmind-restore-{suffix_id}"
    suffix = f"_restore_{suffix_id}"
    components = manifest["components"]
    restored_collections = [
        f"{source}{suffix}" for source in components["milvus"]["collections"]
    ]
    try:
        restore_postgres(root, database, components["postgresql"]["counts"])
        restore_minio(root, bucket, components["minio"]["object_count"])
        restored_collections = restore_milvus(
            root,
            suffix,
            components["milvus"]["collections"],
        )
        print(json.dumps({"database": database, "bucket": bucket, "collections": restored_collections}, ensure_ascii=False))
    finally:
        if args.cleanup:
            cleanup_drill(database, bucket, restored_collections)


def prune(args) -> None:
    root = Path(args.output).resolve()
    if args.days < 1:
        raise RuntimeError("保留天数必须大于 0")
    cutoff = datetime.now(UTC) - timedelta(days=args.days)
    candidates = []
    for manifest_path in root.glob("devmind-*/manifest.json"):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if datetime.fromisoformat(manifest["created_at"]) < cutoff:
            candidates.append(manifest_path.parent)
    if not args.confirm:
        print("\n".join(str(path) for path in candidates))
        return
    for path in candidates:
        if path.parent != root or not path.name.startswith("devmind-"):
            raise RuntimeError(f"拒绝删除非备份目录: {path}")
        shutil.rmtree(path)


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser()
    commands = cli.add_subparsers(dest="command", required=True)
    create_cmd = commands.add_parser("create")
    create_cmd.add_argument("--output", default=str(PROJECT_ROOT / "backups"))
    create_cmd.add_argument("--name")
    create_cmd.set_defaults(handler=create)
    verify_cmd = commands.add_parser("verify")
    verify_cmd.add_argument("--input", required=True)
    verify_cmd.set_defaults(handler=lambda args: print(json.dumps(verify(Path(args.input).resolve())["components"], ensure_ascii=False)))
    restore_cmd = commands.add_parser("restore-drill")
    restore_cmd.add_argument("--input", required=True)
    restore_cmd.add_argument("--cleanup", action="store_true")
    restore_cmd.set_defaults(handler=restore_drill)
    prune_cmd = commands.add_parser("prune")
    prune_cmd.add_argument("--output", default=str(PROJECT_ROOT / "backups"))
    prune_cmd.add_argument("--days", type=int, default=14)
    prune_cmd.add_argument("--confirm", action="store_true")
    prune_cmd.set_defaults(handler=prune)
    return cli


if __name__ == "__main__":
    try:
        arguments = parser().parse_args()
        arguments.handler(arguments)
    except Exception as exc:
        print(f"backup failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
