"""이미지를 공개 URL 로 올린다. Instagram Graph API 는 공개 URL 만 받는다.

Cloudflare R2(S3 호환)를 기본으로 하고, AWS S3 도 같은 코드로 동작한다.
"""
from __future__ import annotations

import mimetypes
from pathlib import Path

import boto3

from .config import env


def _client():
    return boto3.client(
        "s3",
        endpoint_url=env("S3_ENDPOINT_URL"),  # R2: https://<account_id>.r2.cloudflarestorage.com
        aws_access_key_id=env("S3_ACCESS_KEY_ID", required=True),
        aws_secret_access_key=env("S3_SECRET_ACCESS_KEY", required=True),
        region_name=env("S3_REGION", "auto"),
    )


def upload(paths: list[Path], prefix: str) -> list[str]:
    bucket = env("S3_BUCKET", required=True)
    public_base = env("PUBLIC_BASE_URL", required=True).rstrip("/")  # 예: https://pub-xxxx.r2.dev
    client = _client()
    urls = []
    for path in paths:
        key = f"{prefix}/{path.name}"
        content_type = mimetypes.guess_type(path.name)[0] or "image/jpeg"
        client.upload_file(str(path), bucket, key, ExtraArgs={"ContentType": content_type})
        urls.append(f"{public_base}/{key}")
    return urls
