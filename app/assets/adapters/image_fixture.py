"""Fixture ImageGenerationPort adapter.

Returns a small, clearly-labeled bundled sample image so the full asset
pipeline is runnable without real credentials. Supports a forced-failure-
once mode, mirroring Module 4's fixture adapter pattern.
"""
from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from app.assets.ports import GeneratedImage
from app.common.exceptions import InfrastructureError
from app.common.logging import get_logger

logger = get_logger(__name__)

_FIXTURE_DIR = Path(__file__).parent / "fixtures"
_FIXTURE_IMAGE_PATH = _FIXTURE_DIR / "sample_hero.jpg"
_FIXTURE_WIDTH, _FIXTURE_HEIGHT = 1024, 1024


def _ensure_fixture_image() -> bytes:
    """Lazily creates (and caches on disk) a clearly-labeled placeholder
    hero image the first time it's needed, so this module doesn't need to
    ship a binary asset in source control."""
    if _FIXTURE_IMAGE_PATH.exists():
        return _FIXTURE_IMAGE_PATH.read_bytes()

    _FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (_FIXTURE_WIDTH, _FIXTURE_HEIGHT), color=(40, 44, 68))
    draw = ImageDraw.Draw(image)
    draw.text((40, 40), "FIXTURE HERO IMAGE", fill=(255, 255, 255))
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=90)
    data = buf.getvalue()
    _FIXTURE_IMAGE_PATH.write_bytes(data)
    return data


class FixtureImageGenerationAdapter:
    """Fixture ImageGenerationPort implementation.

    `force_fail_once=True` makes exactly one call to `generate` raise
    `InfrastructureError` before subsequent calls succeed -- used to
    exercise the retry / attempt-recording path deterministically.
    """

    def __init__(self, force_fail_once: bool = False) -> None:
        self._pending_failure = force_fail_once

    async def generate(self, prompt: str, idempotency_key: str, **params: Any) -> GeneratedImage:
        if self._pending_failure:
            self._pending_failure = False
            logger.info("fixture_image_forced_failure", idempotency_key=idempotency_key)
            raise InfrastructureError("Fixture image adapter: forced failure before success")

        data = _ensure_fixture_image()
        logger.info("fixture_image_generated", idempotency_key=idempotency_key, prompt=prompt)
        return GeneratedImage(
            data=data,
            width=_FIXTURE_WIDTH,
            height=_FIXTURE_HEIGHT,
            provider_request_id=f"fixture-image-{idempotency_key}",
        )
