"""S3-compatible StoragePort adapter, using settings.s3_* fields from
Module 2 (bucket, region, endpoint_url, credentials, optional public base
URL). Works against real S3 as well as S3-compatible services (MinIO, R2,
etc.) via `s3_endpoint_url`.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import boto3
from botocore.config import Config as BotoConfig

from app.assets.ports import StoredAsset
from app.common.exceptions import InfrastructureError
from app.common.logging import get_logger
from app.config import get_settings

logger = get_logger(__name__)

_DEFAULT_PRESIGN_EXPIRY_SECONDS = 3600


class S3CompatibleStorageAdapter:
    def __init__(self) -> None:
        settings = get_settings()
        bucket = getattr(settings, "s3_bucket", None)
        if not bucket:
            raise InfrastructureError("S3 storage adapter requires settings.s3_bucket to be configured")

        self._bucket = bucket
        self._public_base_url = getattr(settings, "s3_public_base_url", None)
        # boto3's own retry machinery handles transient failures for this
        # single call; Temporal's RetryPolicy on the calling activity governs
        # retries for the activity as a whole. No app-level retry wrapper here.
        self._client = boto3.client(
            "s3",
            endpoint_url=getattr(settings, "s3_endpoint_url", None),
            region_name=getattr(settings, "s3_region", None),
            aws_access_key_id=getattr(settings, "s3_access_key_id", None),
            aws_secret_access_key=getattr(settings, "s3_secret_access_key", None),
            config=BotoConfig(retries={"max_attempts": 3, "mode": "standard"}),
        )

    async def save(self, data: bytes, key: str, content_type: str) -> StoredAsset:
        try:
            await asyncio.to_thread(
                self._client.put_object,
                Bucket=self._bucket,
                Key=key,
                Body=data,
                ContentType=content_type,
            )
        except InfrastructureError:
            raise
        except Exception as exc:  # defensive: normalize any boto3 error surfaced from put_object
            raise InfrastructureError(f"S3 upload failed for key={key}: {exc}") from exc

        if self._public_base_url:
            return StoredAsset(storage_url=f"{self._public_base_url.rstrip('/')}/{key}", expires_at=None)

        url = await asyncio.to_thread(
            self._client.generate_presigned_url,
            "get_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=_DEFAULT_PRESIGN_EXPIRY_SECONDS,
        )
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=_DEFAULT_PRESIGN_EXPIRY_SECONDS)
        logger.info("s3_storage_saved", key=key, bucket=self._bucket)
        return StoredAsset(storage_url=url, expires_at=expires_at)