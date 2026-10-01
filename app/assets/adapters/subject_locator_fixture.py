"""Fixture SubjectLocatorPort: no model call, so the compositor uses its own
pixel-based saliency estimate -- exactly the pre-locator behaviour."""
from __future__ import annotations

from app.assets.ports import SubjectBox


class FixtureSubjectLocatorAdapter:
    async def locate(self, image: bytes, *, product: str = "") -> SubjectBox | None:
        return None
