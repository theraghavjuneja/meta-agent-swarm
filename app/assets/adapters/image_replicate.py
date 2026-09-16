"""Real ImageGenerationPort adapter backed by Replicate-hosted Flux.

The provider choice (Replicate/Flux) is a flagged, swappable detail per the
project's own architecture notes -- ImageGenerationPort is what the rest of
the system depends on, not this specific provider. Swap this file out (and
the one line in adapters/__init__.py that selects it) to change providers
without touching activities, dto, or repository code.

Assumes app.common.retry exposes:
    async def with_retry(fn: Callable[[], Awaitable[T]]) -> T
retrying transient failures and raising InfrastructureError on exhaustion.
"""
from __future__ import annotations

import io
from typing import Any

import httpx
from PIL import Image

from app.assets.ports import GeneratedImage
from app.common.exceptions import InfrastructureError
from app.common.logging import get_logger
from app.common.retry import with_retry
from app.config import get_settings

logger = get_logger(__name__)

_REPLICATE_API_BASE = "https://api.replicate.com/v1"
_DEFAULT_FLUX_MODEL = "black-forest-labs/flux-schnell"


class ReplicateImageGenerationAdapter:
    """ImageGenerationPort implementation calling Replicate's hosted Flux
    model. Idempotency is best-effort: Replicate predictions don't natively
    accept a client idempotency key, so `idempotency_key` is carried in
    structured logs for tracing/dedup on our side rather than sent to the
    provider."""

    def __init__(self, api_token: str | None = None, model: str | None = None) -> None:
        settings = get_settings()
        self._api_token = api_token or getattr(settings, "replicate_api_token", None)
        self._model = model or getattr(settings, "replicate_flux_model", None) or _DEFAULT_FLUX_MODEL
        if not self._api_token:
            raise InfrastructureError("Replicate API token is not configured (settings.replicate_api_token)")

    async def generate(self, prompt: str, idempotency_key: str, **params: Any) -> GeneratedImage:
        async def _create_prediction() -> dict[str, Any]:
            async with httpx.AsyncClient(timeout=120) as client:
                response = await client.post(
                    f"{_REPLICATE_API_BASE}/models/{self._model}/predictions",
                    headers={
                        "Authorization": f"Bearer {self._api_token}",
                        "Content-Type": "application/json",
                        "Prefer": "wait",
                    },
                    json={"input": {"prompt": prompt, **params}},
                )
                response.raise_for_status()
                return response.json()

        payload = await with_retry(_create_prediction)

        output = payload.get("output")
        image_url = output[0] if isinstance(output, list) and output else output
        if not image_url:
            raise InfrastructureError(f"Replicate returned no image output for prediction: {payload!r}")

        async def _download_image() -> bytes:
            async with httpx.AsyncClient(timeout=120) as client:
                image_response = await client.get(image_url)
                image_response.raise_for_status()
                return image_response.content

        data = await with_retry(_download_image)

        with Image.open(io.BytesIO(data)) as img:
            width, height = img.size

        logger.info(
            "replicate_image_generated",
            idempotency_key=idempotency_key,
            prediction_id=payload.get("id"),
        )
        return GeneratedImage(
            data=data,
            width=width,
            height=height,
            provider_request_id=payload.get("id"),
        )
