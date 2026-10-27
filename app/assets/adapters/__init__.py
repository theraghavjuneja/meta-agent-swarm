"""Adapter factory: the one place that branches on fixture-vs-real,
mirroring app/research/adapters's factory pattern.

`resolve_adapters()` reads `settings.provider_mode` ("fixture" | "real") and
returns a fully-wired (ImageGenerationPort, VideoRenderPort, StoragePort)
triple. Storage additionally branches on `settings.storage_backend`
("local" | "s3") when in "real" mode: `provider_mode` alone says
fixture-vs-real, not which real storage backend to use, and both local
filesystem and S3-compatible are valid real adapters per the project's own
architecture notes. Defaults to "local" if `storage_backend` isn't set.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.assets.adapters.image_fixture import FixtureImageGenerationAdapter
from app.assets.adapters.image_openai import OpenAIImageGenerationAdapter
from app.assets.adapters.storage_fixture import FixtureStorageAdapter
from app.assets.adapters.storage_local import LocalFilesystemStorageAdapter
from app.assets.adapters.storage_s3 import S3CompatibleStorageAdapter
from app.assets.adapters.video_ffmpeg import FfmpegVideoRenderAdapter
from app.assets.adapters.video_fixture import FixtureVideoRenderAdapter
from app.assets.ports import ImageGenerationPort, StoragePort, VideoRenderPort
from app.config import get_settings


@dataclass(frozen=True)
class AssetAdapters:
    image: ImageGenerationPort
    video: VideoRenderPort
    storage: StoragePort


def get_image_adapter(settings=None, *, force_fail_once: bool = False) -> ImageGenerationPort:
    settings = settings or get_settings()
    if settings.provider_mode == "fixture":
        return FixtureImageGenerationAdapter(force_fail_once=force_fail_once)
    return OpenAIImageGenerationAdapter(
        api_token=settings.openai_api_key,
        model=settings.openai_image_model,
        size=settings.openai_image_size,
        quality=settings.openai_image_quality,
        input_fidelity=settings.openai_image_input_fidelity,
    )


def get_video_adapter(settings=None, *, force_fail_once: bool = False) -> VideoRenderPort:
    settings = settings or get_settings()
    if settings.provider_mode == "fixture":
        return FixtureVideoRenderAdapter(force_fail_once=force_fail_once)
    return FfmpegVideoRenderAdapter()


def get_storage_adapter(settings=None) -> StoragePort:
    settings = settings or get_settings()
    if settings.provider_mode == "fixture":
        return FixtureStorageAdapter()
    backend = getattr(settings, "storage_backend", "local")
    if backend == "s3":
        return S3CompatibleStorageAdapter()
    return LocalFilesystemStorageAdapter()


def resolve_adapters(settings=None) -> AssetAdapters:
    """Resolves all three ports at once, for callers (activities) that need
    the full set."""
    settings = settings or get_settings()
    return AssetAdapters(
        image=get_image_adapter(settings),
        video=get_video_adapter(settings),
        storage=get_storage_adapter(settings),
    )
