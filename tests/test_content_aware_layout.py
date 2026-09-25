"""Task 2: content-aware copy placement, type systems, and a verbatim brief CTA."""
from __future__ import annotations

import asyncio
import io
import json
import uuid
from types import SimpleNamespace

import pytest
from PIL import Image

from app.assets import compositing
from app.assets.compositing import compose_with_report, render_video_layers
from app.assets.prompts import build_hero_image_prompt
from tests.conftest import make_hero, make_spec

STYLES = ["modern_clean", "editorial_serif", "bold_athletic"]
SIZES = [(1080, 1080), (1080, 1920)]


def _hero_with_small_product(size=(1024, 1536)) -> bytes:
    """What the hero prompt now asks for: product ~35% of frame height in the
    middle band, calm space above and below."""
    return make_hero(size)


def test_vision_check_finds_the_product():
    image = Image.open(io.BytesIO(make_hero((1080, 1080)))).convert("RGB")
    box = compositing._subject_box(image)
    assert box is not None
    left, top, right, bottom = box
    # make_hero draws the product at x 35-65%, y 33-72%. The mask is dilated on
    # purpose (a safety margin so type keeps clear of the product's edge), so the
    # box is allowed to be up to ~6% of the frame larger on each side.
    assert abs(left - 378) < 65 and abs(right - 702) < 65
    assert abs(top - 356) < 65 and abs(bottom - 778) < 65
    assert left < 378 and right > 702  # never smaller than the product


def test_flat_image_has_no_subject():
    flat = Image.new("RGB", (800, 800), (200, 200, 205))
    assert compositing._subject_box(flat) is None


@pytest.mark.parametrize("style", STYLES)
@pytest.mark.parametrize("size", SIZES)
def test_headline_never_lands_on_the_product(style, size):
    _, report = compose_with_report(_hero_with_small_product(), make_spec(typography_style=style), size)
    assert report["headline_overlap"] < 0.04, report
    assert report["style"] == style
    json.dumps(report)  # persisted with the asset


@pytest.mark.parametrize("style", STYLES)
def test_post_render_contrast_meets_target(style):
    _, report = compose_with_report(_hero_with_small_product(), make_spec(typography_style=style), (1080, 1920))
    assert report["contrast"] >= compositing._TARGET_CONTRAST


def test_busy_image_gets_stronger_legibility_treatment():
    noisy = Image.effect_noise((1024, 1536), 120).convert("RGB")
    buf = io.BytesIO()
    noisy.save(buf, format="JPEG")
    _, calm = compose_with_report(_hero_with_small_product(), make_spec(), (1080, 1920))
    _, busy = compose_with_report(buf.getvalue(), make_spec(), (1080, 1920))
    assert busy["scrim_alpha"] > calm["scrim_alpha"]


def test_off_centre_product_offers_a_side_column():
    image = Image.new("RGB", (1024, 1536), (235, 236, 238))
    from PIL import ImageDraw

    ImageDraw.Draw(image).rectangle((650, 450, 950, 1150), fill=(20, 20, 22))
    buf = io.BytesIO()
    image.save(buf, format="JPEG")
    framed = compositing._crop_to_fill(Image.open(io.BytesIO(buf.getvalue())).convert("RGB"), (1080, 1920))
    mask = compositing._subject_mask(framed)
    style = compositing._STYLES["modern_clean"]
    layout = compositing._layout_for((1080, 1920))
    cands = compositing._candidates(make_spec(), style, layout, compositing._subject_box(framed), mask)
    assert "side_left" in {c.placement for c in cands}


def test_two_tier_headline_split_keeps_the_copy():
    assert compositing._split_hook("Every conversation. Deserves its own fragrance.") == (
        "Every conversation.",
        "Deserves its own fragrance.",
    )
    assert compositing._split_hook("Fuel the rep after the rep") == (None, "Fuel the rep after the rep")


@pytest.mark.parametrize("style", STYLES)
def test_long_brief_cta_always_fits_the_frame(style):
    long_cta = "Pre-order the limited first batch today and get it before anyone else"
    spec = make_spec(cta=long_cta, typography_style=style)
    layout = compositing._layout_for((1080, 1080))
    button = compositing._render_cta(
        long_cta, compositing._STYLES[style], layout, fill=(255, 106, 19), max_width=int(1080 * 0.84)
    )
    assert button.width <= 1080
    image = Image.open(io.BytesIO(compose_with_report(make_hero(), spec, (1080, 1080))[0]))
    assert image.size == (1080, 1080)


def test_video_layers_use_the_same_design():
    layers = render_video_layers(make_hero(), make_spec(typography_style="bold_athletic"), (540, 960))
    assert Image.open(io.BytesIO(layers.cta_layer)).getchannel("A").getbbox() is not None


def test_hero_prompt_art_direction_follows_the_type_system():
    athletic = build_hero_image_prompt(make_spec(typography_style="bold_athletic"))
    editorial = build_hero_image_prompt(make_spec(typography_style="editorial_serif"))
    assert "performance still life" in athletic and "luxury editorial" not in athletic
    assert "luxury editorial" in editorial
    for prompt in (athletic, editorial):
        assert "No text of any kind" in prompt


def test_spec_cta_is_the_brief_cta_verbatim(monkeypatch):
    """The LLM may reword the CTA; what reaches the ads is the brief's text."""
    from app.creative import service
    from app.creative.dto import GenerateSpecInput
    from app.research.adapters.llm_fixture import FIXTURE_CREATIVE_SPEC

    captured = {}

    class LLM:
        async def structured_output(self, **kwargs):
            data = dict(FIXTURE_CREATIVE_SPEC, cta="Grab yours now!!", typography_style="bold_athletic")
            return SimpleNamespace(data=data, usage=None)

    async def get_angle(session, angle_id):
        return SimpleNamespace(audience_insight="a", hook="h", visual_direction="v", rationale="r", observations=[])

    async def create_version(session, **kwargs):
        captured.update(kwargs["spec_data"])
        return SimpleNamespace(id=uuid.uuid4(), version=1, is_valid=True)

    monkeypatch.setattr(service, "get_llm_port", lambda settings: LLM())
    monkeypatch.setattr(service.research_repository, "get_angle", get_angle)
    monkeypatch.setattr(service.repository, "create_version", create_version)

    brief_cta = "Explore the range"
    asyncio.run(
        service.generate_creative_spec(
            None,
            GenerateSpecInput(
                campaign_id=uuid.uuid4(), angle_id=uuid.uuid4(), product_name="Beast Whey",
                product_description="d", target_audience="a", objective="o", tone="t", cta=brief_cta,
            ),
        )
    )
    assert captured["cta"] == brief_cta
    assert captured["typography_style"] == "bold_athletic"
