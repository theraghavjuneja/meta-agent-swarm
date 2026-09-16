"""Ports (Protocols) for the assets module.

External capabilities (image generation, video rendering, storage) are
accessed exclusively through these Protocols. Concrete implementations live
in app/assets/adapters/ and are selected by the factory in
app/assets/adapters/__init__.py based on settings.provider_mode, mirroring
app/research/adapters's factory pattern.

Text overlay/crop compositing (app/assets/compositing.py) is deterministic
Pillow code, not a provider call, so it deliberately has no port here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class GeneratedImage:
    """Result of an image generation call. Carries either raw bytes or a
    temp path -- callers should use `read_bytes()` rather than poking at
    the fields directly."""

    data: bytes | None = None
    path: Path | None = None
    width: int = 0
    height: int = 0
    provider_request_id: str | None = None

    def read_bytes(self) -> bytes:
        if self.data is not None:
            return self.data
        if self.path is not None:
            return Path(self.path).read_bytes()
        raise ValueError("GeneratedImage has neither data nor path")


@dataclass(frozen=True)
class VideoRenderSpec:
    """Everything the video render pipeline needs, sourced from
    creative_specs.video_outline plus the target format. `extra` carries any
    additional outline fields a given adapter wants to use without forcing a
    schema change here."""

    headline_text: str
    cta_text: str
    target_width: int
    target_height: int
    target_duration_seconds: float
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GeneratedVideo:
    """Result of a video render call. Carries either raw bytes or a temp
    path -- callers should use `read_bytes()` rather than poking at the
    fields directly."""

    data: bytes | None = None
    path: Path | None = None
    duration_seconds: float = 0.0
    width: int = 0
    height: int = 0
    provider_request_id: str | None = None

    def read_bytes(self) -> bytes:
        if self.data is not None:
            return self.data
        if self.path is not None:
            return Path(self.path).read_bytes()
        raise ValueError("GeneratedVideo has neither data nor path")


@dataclass(frozen=True)
class StoredAsset:
    """Result of a storage save call. `expires_at` is set for backends that
    return a time-limited URL (e.g. an S3 presigned URL); None for
    durable/public URLs (local filesystem, a public S3 base URL)."""

    storage_url: str
    expires_at: datetime | None = None


class ImageGenerationPort(Protocol):
    async def generate(self, prompt: str, idempotency_key: str, **params: Any) -> GeneratedImage:
        """Generate a hero image from `prompt`. `idempotency_key` is passed
        through to providers that support one, so a retried call can't cause
        a second paid generation for the same logical asset."""
        ...


class VideoRenderPort(Protocol):
    async def render(
        self, hero_image: bytes | Path, spec: VideoRenderSpec, idempotency_key: str
    ) -> GeneratedVideo:
        """Render a video animating `hero_image` per `spec` (pan/zoom,
        headline reveal, CTA end frame)."""
        ...


class StoragePort(Protocol):
    async def save(self, data: bytes, key: str, content_type: str) -> StoredAsset:
        """Persist `data` under `key` and return the resolved URL. Kept
        provider-agnostic: local filesystem and S3-compatible are both
        valid real adapters behind this same interface."""
        ...
