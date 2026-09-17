"""Local-filesystem StoragePort adapter.

Writes under a configured base path and returns an HTTP URL hosted by the local FastAPI server. A valid
"real" backend alongside the S3-compatible adapter -- StoragePort is kept
provider-agnostic on purpose.
"""
from __future__ import annotations

from pathlib import Path

from app.assets.ports import StoredAsset
from app.common.logging import get_logger
from app.config import get_settings

logger = get_logger(__name__)

_DEFAULT_BASE_PATH = "./data/assets"


class LocalFilesystemStorageAdapter:
    def __init__(self, base_path: Path | str | None = None) -> None:
        settings = get_settings()
        configured = base_path or getattr(settings, "local_storage_path", None) or _DEFAULT_BASE_PATH
        self._base_path = Path(configured)
        self._base_path.mkdir(parents=True, exist_ok=True)

    async def save(self, data: bytes, key: str, content_type: str) -> StoredAsset:
        target = self._base_path / key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        logger.info("local_storage_saved", key=key, bytes=len(data))
        
        settings = get_settings()
        api_base = getattr(settings, "api_base_url", "http://localhost:8000").rstrip("/")
        url = f"{api_base}/assets/{key.lstrip('/')}"
        
        return StoredAsset(storage_url=url)
