"""Blob storage: receipt images. Local filesystem by default; Cloudflare R2 in prod.

Production uploads go browser -> R2 directly via a presigned PUT (the 4.5 MB
function body cap means photos must never pass through the function). Locally
the same shape is honoured: `upload_url()` returns a PUT to our own
/api/blob/{key} receiver, so the capture flow code is identical either way.
The DB stores only the KEY; display goes through short-lived links.
"""
from __future__ import annotations

import shutil
from functools import lru_cache
from pathlib import Path

from .config import WRITABLE_ROOT, settings

LOCAL_ROOT = WRITABLE_ROOT / "local_storage"


class StorageBackend:
    name = "base"

    def upload_url(self, key: str, content_type: str = "image/jpeg") -> dict:
        """Return {method, url, headers} for the browser to upload the bytes to."""
        raise NotImplementedError

    def display_url(self, key: str, expires: int = 3600) -> str:
        raise NotImplementedError

    def put_bytes(self, key: str, data: bytes, content_type: str = "image/jpeg"):
        raise NotImplementedError

    def get_bytes(self, key: str) -> bytes:
        raise NotImplementedError

    def exists(self, key: str) -> bool:
        raise NotImplementedError

    def delete_prefix(self, prefix: str):
        raise NotImplementedError


class LocalStorage(StorageBackend):
    name = "local"

    def _path(self, key: str) -> Path:
        p = (LOCAL_ROOT / key).resolve()
        if not str(p).startswith(str(LOCAL_ROOT.resolve())):
            raise ValueError("path traversal blocked")
        return p

    def upload_url(self, key: str, content_type: str = "image/jpeg") -> dict:
        return {"method": "PUT", "url": f"/api/blob/{key}",
                "headers": {"content-type": content_type}}

    def display_url(self, key: str, expires: int = 3600) -> str:
        return f"/api/blob/{key}"

    def put_bytes(self, key: str, data: bytes, content_type: str = "image/jpeg"):
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def get_bytes(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def delete_prefix(self, prefix: str):
        p = (LOCAL_ROOT / prefix).resolve()
        if str(p).startswith(str(LOCAL_ROOT.resolve())) and p.exists():
            shutil.rmtree(p, ignore_errors=True)


class R2Storage(StorageBackend):
    name = "r2"

    def __init__(self):
        import boto3  # lazy: only needed in prod
        from botocore.config import Config
        self.bucket = settings.r2_bucket
        self.client = boto3.client(
            "s3", endpoint_url=settings.r2_endpoint_url,
            aws_access_key_id=settings.r2_access_key_id,
            aws_secret_access_key=settings.r2_secret_access_key,
            config=Config(signature_version="s3v4"), region_name="auto")

    def upload_url(self, key: str, content_type: str = "image/jpeg") -> dict:
        url = self.client.generate_presigned_url(
            "put_object",
            Params={"Bucket": self.bucket, "Key": key, "ContentType": content_type},
            ExpiresIn=900)
        return {"method": "PUT", "url": url, "headers": {"content-type": content_type}}

    def display_url(self, key: str, expires: int = 3600) -> str:
        return self.client.generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=expires)

    def put_bytes(self, key: str, data: bytes, content_type: str = "image/jpeg"):
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data,
                               ContentType=content_type)

    def get_bytes(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception:
            return False

    def delete_prefix(self, prefix: str):
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            keys = [{"Key": o["Key"]} for o in page.get("Contents", [])]
            if keys:
                self.client.delete_objects(Bucket=self.bucket, Delete={"Objects": keys})


@lru_cache
def get_storage() -> StorageBackend:
    return R2Storage() if settings.effective_storage == "r2" else LocalStorage()
