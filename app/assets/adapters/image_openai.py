"""ImageGenerationPort adapter backed by the OpenAI Images API.

Drop-in replacement for the old Replicate/Flux adapter. The rest of the
system (activities, dto, repository) still depends on ImageGenerationPort —
only this file changed.

Two request shapes, chosen by whether the caller supplied a reference image:

* no reference  -> ``images.generate`` (text-to-image);
* reference     -> ``images.edit`` with the packshot as the image input, so
  the model actually *sees* the product it has to reproduce. Mentioning a
  reference in the prompt text alone does nothing -- the model has no way
  to look at a URL -- which is why uploaded references used to be ignored.
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
# Portrait: see Settings.openai_image_size for why the hero is not square.
_DEFAULT_SIZE = "1024x1536"
# "auto" lets the provider choose per request -- a source of run-to-run
# variance. Ad creatives pin the top tier.
_DEFAULT_QUALITY = "high"


class OpenAIImageGenerationAdapter:
    """ImageGenerationPort implementation using OpenAI image generation.

    ``idempotency_key`` is carried in structured logs for tracing.
    """

    def __init__(
        self,
        api_token: str | None = None,
        model: str | None = None,
        *,
        size: str | None = None,
        quality: str | None = None,
        input_fidelity: str | None = None,
    ) -> None:
        from openai import AsyncOpenAI  # imported here: adapters own the SDK

        resolved_key = api_token
        if not resolved_key:
            raise InfrastructureError("OpenAI API key is not configured.")

        self._client = AsyncOpenAI(api_key=resolved_key)
        self._model = model or _DEFAULT_MODEL
        self._size = size or _DEFAULT_SIZE
        self._quality = quality or _DEFAULT_QUALITY
        self._input_fidelity = input_fidelity

    async def generate(
        self,
        prompt: str,
        idempotency_key: str,
        *,
        reference_image: bytes | None = None,
        **params: Any,
    ) -> GeneratedImage:
        size = params.get("size", self._size)
        quality = params.get("quality", self._quality)

        try:
            if reference_image is not None:
                # input_fidelity goes through extra_body so this works on
                # SDK versions that predate the typed parameter.
                extra_body = (
                    {"input_fidelity": self._input_fidelity} if self._input_fidelity else None
                )
                result = await self._client.images.edit(
                    model=self._model,
                    image=("reference.png", reference_image, "image/png"),
                    prompt=prompt,
                    size=size,
                    quality=quality,
                    extra_body=extra_body,
                )
            else:
                result = await self._client.images.generate(
                    model=self._model,
                    prompt=prompt,
                    size=size,
                    quality=quality,
                    output_format="png",
                )
        except Exception as exc:
            logger.error(
                "openai_image_generation_failed",
                error=repr(exc),
                used_reference_image=reference_image is not None,
            )
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
            size=size,
            quality=quality,
            used_reference_image=reference_image is not None,
        )
        return GeneratedImage(
            data=data,
            width=width,
            height=height,
            provider_request_id=idempotency_key,
        )
