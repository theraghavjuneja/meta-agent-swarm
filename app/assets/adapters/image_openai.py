"""ImageGenerationPort adapter backed by OpenAI gpt-image-1-mini.

Drop-in replacement for the old Replicate/Flux adapter. The rest of the
system (activities, dto, repository) still depends on ImageGenerationPort —
only this file changed.
"""
from __future__ import annotations

import base64
import io
from typing import Any

from PIL import Image

from app.assets.ports import GeneratedImage
from app.common.exceptions import InfrastructureError
from app.common.logging import get_logger

logger = get_logger(__name__)



_DEFAULT_MODEL = "gpt-image-2.5-flare"
_DEFAULT_SIZE = "1024x1024"
_DEFAULT_QUALITY = "auto"


class OpenAIImageGenerationAdapter:
    """ImageGenerationPort implementation using OpenAI image generation.

    ``idempotency_key`` is carried in structured logs for tracing.
    """

    def __init__(
        self,
        api_token: str | None = None,
        model: str | None = None,
    ) -> None:
        from openai import AsyncOpenAI  # imported here: adapters own the SDK

        resolved_key = api_token
        if not resolved_key:
            raise InfrastructureError("OpenAI API key is not configured.")

        self._client = AsyncOpenAI(api_key=resolved_key)
        self._model = model or _DEFAULT_MODEL

    async def generate(self, prompt: str, idempotency_key: str, **params: Any) -> GeneratedImage:
        try:
            result = await self._client.images.generate(
                model=self._model,
                prompt=prompt,
                size=params.get("size", _DEFAULT_SIZE),
                quality=params.get("quality", _DEFAULT_QUALITY),
                output_format="png",
            )
        except Exception as exc:
            logger.error("openai_image_generation_failed", error=repr(exc))
            raise InfrastructureError(f"OpenAI image generation failed: {exc}") from exc

        b64_data = result.data[0].b64_json
        if not b64_data:
            raise InfrastructureError("OpenAI returned no image data.")

        data = base64.b64decode(b64_data)

        with Image.open(io.BytesIO(data)) as img:
            width, height = img.size

        logger.info(
            "openai_image_generated",
            idempotency_key=idempotency_key,
            model=self._model,
        )
        return GeneratedImage(
            data=data,
            width=width,
            height=height,
            provider_request_id=idempotency_key,
        )

