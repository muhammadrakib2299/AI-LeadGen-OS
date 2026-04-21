"""S3 PutObject helper used by the export endpoint.

Wraps boto3 inside `asyncio.to_thread` so the blocking SDK call doesn't
freeze the FastAPI event loop. Returns the canonical s3:// URL on success.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import boto3
from botocore.config import Config

from app.core.logging import get_logger

log = get_logger(__name__)


@dataclass(slots=True)
class S3UploadResult:
    bucket: str
    key: str
    s3_uri: str


def _build_client(*, region: str, access_key_id: str, secret_access_key: str):
    return boto3.client(
        "s3",
        region_name=region,
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
        config=Config(retries={"max_attempts": 3, "mode": "standard"}),
    )


def _put_object_sync(
    *,
    bucket: str,
    region: str,
    access_key_id: str,
    secret_access_key: str,
    key: str,
    body: bytes,
) -> S3UploadResult:
    client = _build_client(
        region=region,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
    )
    client.put_object(
        Bucket=bucket,
        Key=key,
        Body=body,
        ContentType="text/csv",
    )
    return S3UploadResult(bucket=bucket, key=key, s3_uri=f"s3://{bucket}/{key}")


async def put_object(
    *,
    bucket: str,
    region: str,
    access_key_id: str,
    secret_access_key: str,
    key: str,
    body: bytes,
) -> S3UploadResult:
    return await asyncio.to_thread(
        _put_object_sync,
        bucket=bucket,
        region=region,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        key=key,
        body=body,
    )
