"""SubjectLocatorPort backed by an OpenAI vision model.

Why a model and not only pixels: the compositor's saliency estimate (colour
distance + edges) cannot tell the product from the things ad photography puts
around it -- bokeh lights, a shaker, a towel, garlands. On a festive gym-bench
hero it reported the "product" as 95% of the frame, so the layout engine saw no
free space and put the CTA on the label. A vision model answers the actual
question ("where is the tub?") in one cheap call per format.

The answer is only a hint: it is sanity-checked here, and any failure returns
None so the compositor falls back to saliency instead of failing the asset.
"""
from __future__ import annotations

import base64
import io
import json
from typing import Any

from PIL import Image

from app.assets.ports import SubjectBox
from app.common.logging import get_logger

logger = get_logger(__name__)

# The locator only needs the composition, not detail: a 768 px JPEG keeps the
# request small and fast.
_MAX_SIDE = 768

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["found", "left", "top", "right", "bottom"],
    "properties": {
        "found": {"type": "boolean", "description": "False if the product is not visible."},
        **{
            k: {"type": "number", "description": f"{k} edge as a fraction 0-1 of the image {'width' if k in ('left', 'right') else 'height'}"}
            for k in ("left", "top", "right", "bottom")
        },
    },
}

_PROMPT = (
    "This is an advertising photograph. Return the tight bounding box of the advertised "
    "product itself ({product}) -- only the product's packaging, not props, accessories, "
    "people, shadows, reflections or background lights. Coordinates are fractions 0-1 "
    "of the image width and height."
)


class OpenAISubjectLocatorAdapter:
    def __init__(self, *, api_key: str | None, model: str) -> None:
        from openai import AsyncOpenAI  # adapters own the SDK

        self._client = AsyncOpenAI(api_key=api_key, timeout=60.0) if api_key else None
        self._model = model

    async def locate(self, image: bytes, *, product: str = "") -> SubjectBox | None:
        if self._client is None:
            return None
        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": _PROMPT.format(product=product or "the product")},
                        {"type": "image_url", "image_url": {"url": _data_url(image)}},
                    ],
                }],
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": "product_box", "schema": _SCHEMA, "strict": True},
                },
            )
            data = json.loads(response.choices[0].message.content or "{}")
        except Exception as exc:  # noqa: BLE001 - a hint, never a failure
            logger.warning("subject_locator_failed", error=repr(exc)[:300])
            return None
        return _validated(data)


def _data_url(image: bytes) -> str:
    with Image.open(io.BytesIO(image)) as img:
        img = img.convert("RGB")
        img.thumbnail((_MAX_SIDE, _MAX_SIDE))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def _validated(data: Any) -> SubjectBox | None:
    """Reject answers that cannot be a product box (inverted, degenerate, or the
    whole frame) rather than letting them steer the layout."""
    if not isinstance(data, dict) or not data.get("found"):
        return None
    try:
        left, top, right, bottom = (min(1.0, max(0.0, float(data[k]))) for k in ("left", "top", "right", "bottom"))
    except (KeyError, TypeError, ValueError):
        return None
    width, height = right - left, bottom - top
    if width < 0.03 or height < 0.03 or width * height > 0.9:
        return None
    return (left, top, right, bottom)
