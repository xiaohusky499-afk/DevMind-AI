"""MinIO 文档对象存储。"""

from minio import Minio

from ..core.config import settings

_client: Minio | None = None


def client() -> Minio:
    global _client
    if _client is None:
        _client = Minio(
            settings.MINIO_ENDPOINT,
            access_key=settings.MINIO_ACCESS_KEY,
            secret_key=settings.MINIO_SECRET_KEY,
            secure=settings.MINIO_SECURE,
        )
        if not _client.bucket_exists(settings.MINIO_BUCKET):
            _client.make_bucket(settings.MINIO_BUCKET)
    return _client


def upload(path: str, object_key: str, content_type: str) -> None:
    client().fput_object(
        settings.MINIO_BUCKET, object_key, path, content_type=content_type
    )


def download(object_key: str, path: str) -> None:
    client().fget_object(settings.MINIO_BUCKET, object_key, path)


def delete(object_key: str) -> None:
    client().remove_object(settings.MINIO_BUCKET, object_key)
