"""Shared test fixtures. Everything here runs offline: no provider calls."""
from __future__ import annotations

import io
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def make_spec(**overrides):
    """A stand-in for a CreativeSpec row with only the fields assets read."""
    fields = dict(
        hook="Fuel the rep after the rep.",
        approved_copy="25 g protein per scoop.",
        cta="Explore the range",
        product_identity={"name": "Beast Whey", "key_visual_traits": ["matte black tub", "orange lid"]},
        scene_description="A matte black protein tub on a concrete gym bench, morning light.",
        palette=["#101214", "#FF6A13", "#F2F2F2"],
        composition_guidance="Low three-quarter angle, product centred in the middle band.",
        video_outline={"beats": [{"label": "Hook", "description": "d"}, {"label": "CTA", "description": "d"}]},
    )
    fields.update(overrides)
    return SimpleNamespace(**fields)


def make_hero(size=(1024, 1536), backdrop=(236, 238, 240)) -> bytes:
    """A portrait hero shaped like the new prompt's output: calm backdrop,
    product-like block in the middle band."""
    image = Image.new("RGB", size, backdrop)
    draw = ImageDraw.Draw(image)
    w, h = size
    draw.rectangle((w * 0.35, h * 0.35, w * 0.65, h * 0.72), fill=(20, 20, 22))
    draw.rectangle((w * 0.35, h * 0.33, w * 0.65, h * 0.37), fill=(255, 106, 19))
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=92)
    return buf.getvalue()


@pytest.fixture
def spec():
    return make_spec()


@pytest.fixture
def hero() -> bytes:
    return make_hero()
