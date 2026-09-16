"""Fixture StoragePort adapter.

Writes to a clearly-labeled temp/fixture directory regardless of the
configured backend, so asset flows are fully runnable without real cloud
credentials -- this is what this module's "done" verification exercises.
"""
from __future__ import annotations

from pathlib import Path

from app.assets.ports import StoredAsset
from app.common.logging import get_logger

logger = get_logger(__name__)

_FIXTURE_STORAGE_DIR = Path(__file__).parent / "fixtures" / "storage"


class FixtureStorageAdapter:
    def __init__(self, base_path: Path | str | None = None) -> None:
        self._base_path = Path(base_path) if base_path else _FIXTURE_STORAGE_DIR
        self._base_path.mkdir(parents=True, exist_ok=True)

    async def save(self, data: bytes, key: str, content_type: str) -> StoredAsset:
        target = self._base_path / key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        logger.info("fixture_storage_saved", key=key, bytes=len(data))
        return StoredAsset(storage_url=target.resolve().as_uri())
