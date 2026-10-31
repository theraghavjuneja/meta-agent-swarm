"""AD_TEXT_MODE: 'overlay' (default, unchanged) vs 'model' (model-designed master ad)."""
from __future__ import annotations

import asyncio
import io
import json
import uuid
from types import SimpleNamespace

from PIL import Image, ImageDraw

from app.assets import activities, formats
from app.assets.adapters.image_fixture import FixtureImageGenerationAdapter
from app.assets.prompts import build_full_ad_prompt
from app.config.settings import Settings
from tests.conftest import make_spec

# The video's product push-in zooms up to this (adapters/video_ffmpeg.py, _PRODUCT_ZOOM).
_MAX_VIDEO_ZOOM = 1.22


def _master(size=1024) -> bytes:
    """A stand-in master ad: coloured field with 'text' blocks near the 7% margins."""
    image = Image.new("RGB", (size, size), (120, 20, 30))
    draw = ImageDraw.Draw(image)
    m = int(size * 0.07)
    draw.rectangle((m, m, size - m, m + 80), fill=(250, 210, 90))  # headline band
    draw.rectangle((m, size - m - 70, size // 2, size - m), fill=(255, 140, 30))  # CTA pill
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def test_overlay_is_the_default_mode():
    assert Settings.model_fields["ad_text_mode"].default == "overlay"


def test_mode_is_switched_by_the_env_var_alone(monkeypatch):
    monkeypatch.setenv("AD_TEXT_MODE", "model")
    assert Settings().ad_text_mode == "model"
    monkeypatch.setenv("AD_TEXT_MODE", "overlay")
    assert Settings().ad_text_mode == "overlay"


def test_9x16_plan_keeps_the_master_inside_safe_zones_and_the_video_zoom():
    plan = formats.plan_outpaint((1080, 1920))
    cl, ct, cr, cb = plan.crop_box
    ml, mt, mr, mb = plan.master_box
    crop_w, crop_h = cr - cl, cb - ct
    assert abs(crop_w / crop_h - 1080 / 1920) < 0.01
    assert cl <= ml and mr <= cr and ct <= mt and mb <= cb
    # Meta Stories overlays: top 14%, bottom 20% of the frame.
    assert (mt - ct) / crop_h >= 0.14 and (cb - mb) / crop_h >= 0.20
    # Product push-in crops (1 - 1/zoom)/2 per side; the master's 7% text margin must survive it.
    cropped_per_side = crop_w * (1 - 1 / _MAX_VIDEO_ZOOM) / 2
    text_left_edge = (ml - cl) + (mr - ml) * 0.07
    assert text_left_edge > cropped_per_side


def test_4x5_plan_is_available_for_future_formats():
    plan = formats.plan_outpaint((1080, 1350))
    cl, ct, cr, cb = plan.crop_box
    assert abs((cr - cl) / (cb - ct) - 0.8) < 0.01
    ml, mt, mr, mb = plan.master_box
    assert cl <= ml and mr <= cr and ct <= mt and mb <= cb


def test_outpaint_never_changes_the_masters_pixels():
    master = _master()
    plan = formats.plan_outpaint((1080, 1920))
    canvas, mask = formats.prepare_outpaint(master, plan)
    with Image.open(io.BytesIO(mask)) as m:
        alpha = m.getchannel("A")
        ml, mt, mr, mb = plan.master_box
        assert alpha.getpixel(((ml + mr) // 2, (mt + mb) // 2)) == 255  # keep
        assert alpha.getpixel((5, 5)) == 0  # repaint
    # A model that repaints *everything* green, text included:
    buf = io.BytesIO()
    Image.new("RGB", plan.canvas_size, (0, 255, 0)).save(buf, format="PNG")
    out = Image.open(io.BytesIO(formats.finish_outpaint(buf.getvalue(), master, plan, (1080, 1920))))
    assert out.size == (1080, 1920)
    # The master's headline band (gold) is back, unchanged, inside the frame.
    cl, ct, cr, cb = plan.crop_box
    sx = 1080 / (cr - cl)
    side = (plan.master_box[2] - plan.master_box[0]) * sx
    x = int((plan.master_box[0] - cl) * sx + side * 0.5)
    y = int((plan.master_box[1] - ct) * sx + side * (0.07 + 40 / 1024))
    r, g, b = out.getpixel((x, y))
    assert r > 200 and g > 170 and b < 140, (r, g, b)


def test_full_ad_prompt_carries_the_copy_verbatim():
    spec = make_spec(hook="Train Hard. Shake Smart.", cta="Shop the Festive Sale", typography_style="bold_athletic")
    prompt = build_full_ad_prompt(spec, has_reference_image=True, badge="25g complete protein per scoop")
    assert '"TRAIN HARD. SHAKE SMART."' in prompt
    assert '"SHOP THE FESTIVE SALE"' in prompt
    assert '"25g complete protein per scoop"' in prompt
    assert '"Beast Whey"' in prompt
    assert "attached reference image is the exact product" in prompt
    assert "No other text, numbers, prices" in prompt
    no_badge = build_full_ad_prompt(spec, has_reference_image=False, badge=None)
    assert "badge" not in no_badge.split("Typography")[1].split("Layout rules")[0]
    assert "attached reference image" not in no_badge


def test_only_model_designed_heroes_take_the_model_path():
    hero = SimpleNamespace(generation_prompt=json.dumps({"ad_text_mode": "model", "derived": {}}))
    assert activities._model_mode_record(hero) is not None
    assert activities._model_mode_record(SimpleNamespace(generation_prompt="Create one photorealistic hero")) is None
    assert activities._model_mode_record(SimpleNamespace(generation_prompt=None)) is None


def test_9x16_derivation_runs_offline_with_fixture_adapters(monkeypatch, tmp_path):
    saved: dict[str, bytes] = {}

    class Storage:
        async def save(self, data, key, content_type):
            saved[key] = data
            return SimpleNamespace(storage_url=f"file://{tmp_path}/{key.replace('/', '_')}")

    monkeypatch.setattr(activities, "get_storage_adapter", lambda: Storage())
    url = asyncio.run(
        activities._derive_9x16(FixtureImageGenerationAdapter(), _master(), uuid.uuid4(), "key")
    )
    (key, data), = saved.items()
    assert key.endswith("master_ad_9x16.jpg") and url
    assert Image.open(io.BytesIO(data)).size == (1080, 1920)
