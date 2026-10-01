"""Problem 1: deterministic layout -- exact export sizes, safe zones,
line-balanced headlines, and layers for the video."""
import io

import pytest
from PIL import Image, ImageFont

from app.assets import compositing
from app.assets.compositing import compose_1x1, compose_9x16, render_video_layers
from tests.conftest import make_hero, make_spec


def _open(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data))


@pytest.mark.parametrize("hero_size", [(1024, 1536), (1024, 1024), (1536, 1024)])
def test_exact_export_dimensions_for_any_hero_shape(spec, hero_size):
    hero = make_hero(hero_size)
    assert _open(compose_1x1(hero, spec)).size == (1080, 1080)
    assert _open(compose_9x16(hero, spec)).size == (1080, 1920)


def test_long_hook_is_wrapped_not_truncated_or_overflowing(spec):
    long_hook = "Some mornings start with coffee. The best ones start with thirty grams of protein and a plan."
    layout = compositing._layout_for((1080, 1920))
    block = compositing._fit_headline(long_hook, layout)

    assert " ".join(block.lines) == long_hook  # approved copy is never cut
    max_w = int(1080 * (1 - 2 * layout.side_margin))
    assert all(compositing._text_width(block.font, line) <= max_w for line in block.lines)
    # Renders without error.
    assert _open(compose_9x16(make_hero(), make_spec(hook=long_hook))).size == (1080, 1920)


def test_balanced_wrap_avoids_single_word_last_line():
    font = ImageFont.truetype(str(compositing._HEADLINE_FONT), 100)
    lines = compositing._balanced_wrap(font, "One signature from first light to last dance", 900)
    assert len(lines[-1].split()) > 1


def test_copy_stays_inside_9x16_safe_zones(spec):
    design = compositing._design(make_hero(), (1080, 1920), spec)
    top_safe, bottom_safe = int(1920 * 0.14), 1920 - int(1920 * 0.20)

    # Only the (opaque) text pixels matter -- gradients may extend further.
    def opaque_rows(layer):
        alpha = layer.getchannel("A").point(lambda a: 255 if a > 200 else 0)
        return alpha.getbbox()

    headline_box = opaque_rows(design.headline_layer)
    cta_box = opaque_rows(design.cta_layer)
    assert headline_box[1] >= top_safe
    assert cta_box[3] <= bottom_safe + 2


def test_ink_follows_background_luminance():
    light = compositing._treatment((240, 240, 240), 0.01, [])[0]
    dark = compositing._treatment((15, 15, 15), 0.01, [])[0]
    assert light != compositing._WHITE
    assert dark == compositing._WHITE


def test_busy_background_gets_stronger_legibility_treatment():
    calm = compositing._treatment((30, 30, 30), 0.01, [])[2]
    busy = compositing._treatment((30, 30, 30), 0.06, [])[2]
    assert busy > calm


def test_video_layers_match_target_size(spec, hero):
    layers = render_video_layers(hero, spec, (1080, 1920))
    assert _open(layers.background_frame).size == (1080, 1920)
    assert _open(layers.end_card_frame).size == (1080, 1920)
    headline = _open(layers.headline_layer)
    cta = _open(layers.cta_layer)
    assert headline.mode == "RGBA" and headline.size == (1080, 1920)
    assert cta.mode == "RGBA" and cta.getchannel("A").getbbox() is not None
