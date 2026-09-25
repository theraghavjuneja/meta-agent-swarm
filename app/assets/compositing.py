"""Deterministic, non-AI compositing: reframe + designed text overlay.

Pure functions -- bytes/data in, bytes out. No provider calls and no I/O
beyond image processing (and reading the bundled fonts), so these stay
trivially reusable from an activity without needing any port. This is what
turns one hero-image generation into two coordinated ad formats and the
video's text layers, rather than independent generations.

What "designed" means here, versus the old centred-text-with-a-stroke
overlay that made the ads look amateur:

* **Format-aware safe zones.** Copy stays out of the areas Meta's own UI
  covers (profile header / reply bar in Stories & Reels) and away from the
  frame edges.
* **The image decides where the copy goes.** Candidate text zones are scored
  for visual busyness (edge density) and the calmer one gets the headline,
  so copy lands on negative space instead of on the product.
* **Contrast is measured, not assumed.** Ink colour follows the luminance
  under the text (dark ink on light backdrops, white on dark), and a soft
  gradient -- never a hard box -- is added only as strongly as the measured
  contrast/busyness requires.
* **Real typographic hierarchy.** A bundled display face (Inter Display,
  SIL OFL), a letter-spaced eyebrow with the product name, an auto-fitted
  and line-balanced headline, and the CTA as a filled pill button coloured
  from the spec's palette -- not a second line of white text.
* **Crisp output.** JPEG at q95 with 4:4:4 chroma, so coloured type edges
  are not smeared by chroma subsampling.

The same design is exposed as separate layers (``render_video_layers``) so
the video animates exactly the typography the still ads use.
"""
from __future__ import annotations

import io
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageStat

if TYPE_CHECKING:
    from app.creative.models import CreativeSpec

__all__ = [
    "VideoLayers",
    "compose_1x1",
    "compose_9x16",
    "render_video_layers",
]

_1X1_SIZE = (1080, 1080)
_9X16_SIZE = (1080, 1920)

_FONT_DIR = Path(__file__).parent / "fonts"
_HEADLINE_FONT = _FONT_DIR / "InterDisplay-ExtraBold.ttf"
_UI_FONT = _FONT_DIR / "InterDisplay-SemiBold.ttf"
# System fallbacks, in case the bundled fonts were stripped from an image.
_FALLBACK_FONTS = ("DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf")

_WHITE = (255, 255, 255)
_INK_DARK = (17, 20, 24)

# Edge-density thresholds (mean of FIND_EDGES / 255), calibrated on the
# heroes in data/assets: seamless/dark backdrops measure ~0.01-0.02, props,
# foliage and collage-like scenes 0.035-0.05.
_CALM_BUSYNESS = 0.02
_BUSY_BUSYNESS = 0.04
# WCAG AA for large text is 3:1; ads are viewed small and fast, so aim higher.
_TARGET_CONTRAST = 4.5

RGB = tuple[int, int, int]


# --------------------------------------------------------------------------- #
# Format layouts
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _FormatLayout:
    size: tuple[int, int]
    # Fractions of the canvas height kept free of copy (platform UI overlays).
    safe_top: float
    safe_bottom: float
    # Fraction of the canvas width kept clear on each side.
    side_margin: float
    # Headline size range as a fraction of canvas width, and line budget.
    headline_max: float
    headline_min: float
    headline_max_lines: int
    # Largest fraction of canvas height the headline block may take.
    headline_max_block: float
    cta_size: float
    eyebrow_size: float


_LAYOUT_1X1 = _FormatLayout(
    size=_1X1_SIZE,
    safe_top=0.065,
    safe_bottom=0.065,
    side_margin=0.08,
    headline_max=0.082,
    headline_min=0.052,
    headline_max_lines=3,
    headline_max_block=0.30,
    cta_size=0.034,
    eyebrow_size=0.024,
)

# Stories/Reels: Meta overlays the profile header in the top ~14% and the
# reply bar / caption in the bottom ~20%; copy placed there gets covered.
_LAYOUT_9X16 = _FormatLayout(
    size=_9X16_SIZE,
    safe_top=0.14,
    safe_bottom=0.20,
    side_margin=0.085,
    headline_max=0.095,
    headline_min=0.058,
    headline_max_lines=4,
    headline_max_block=0.24,
    cta_size=0.040,
    eyebrow_size=0.027,
)


def _layout_for(size: tuple[int, int]) -> _FormatLayout:
    width, height = size
    base = _LAYOUT_9X16 if height / width > 1.3 else _LAYOUT_1X1
    return base if base.size == size else replace(base, size=size)


# --------------------------------------------------------------------------- #
# Small colour / measurement helpers
# --------------------------------------------------------------------------- #


def _hex_to_rgb(value: str) -> RGB | None:
    value = value.strip().lstrip("#")
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    if len(value) != 6:
        return None
    try:
        return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))
    except ValueError:
        return None


def _relative_luminance(rgb: RGB) -> float:
    def channel(c: int) -> float:
        c_s = c / 255
        return c_s / 12.92 if c_s <= 0.03928 else ((c_s + 0.055) / 1.055) ** 2.4

    r, g, b = rgb
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def _contrast(a: RGB, b: RGB) -> float:
    la, lb = _relative_luminance(a), _relative_luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _saturation(rgb: RGB) -> float:
    hi, lo = max(rgb), min(rgb)
    return 0.0 if hi == 0 else (hi - lo) / hi


def _region_stats(image: Image.Image, box: tuple[int, int, int, int]) -> tuple[RGB, float]:
    """Mean colour and busyness (edge density, 0..1) of a region."""
    region = image.crop(box)
    mean = tuple(int(v) for v in ImageStat.Stat(region).mean[:3])
    edges = region.convert("L").filter(ImageFilter.FIND_EDGES)
    busyness = ImageStat.Stat(edges).mean[0] / 255
    return mean, busyness  # type: ignore[return-value]


def _palette(spec: CreativeSpec) -> list[RGB]:
    colors = [_hex_to_rgb(c) for c in (spec.palette or []) if isinstance(c, str)]
    return [c for c in colors if c is not None]


# --------------------------------------------------------------------------- #
# Fonts and text layout
# --------------------------------------------------------------------------- #


@lru_cache(maxsize=64)
def _font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    for candidate in (str(path), *_FALLBACK_FONTS):
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def _text_width(font, text: str) -> int:
    left, _, right, _ = font.getbbox(text)
    return right - left


def _greedy_wrap(font, words: list[str], max_width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if not current or _text_width(font, candidate) <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _balanced_wrap(font, text: str, max_width: int) -> list[str]:
    """Wrap to the fewest lines that fit, then even out line lengths.

    Greedy wrapping alone leaves ragged blocks with a one-word last line
    ("...to last / dance."), which is the single most amateur-looking
    thing in ad typography. Keeping the line count fixed, binary-search the
    narrowest width that still produces that many lines.
    """
    words = text.split()
    if not words:
        return []
    lines = _greedy_wrap(font, words, max_width)
    target = len(lines)
    if target == 1:
        return lines
    lo, hi = max_width // 2, max_width
    best = lines
    while lo <= hi:
        mid = (lo + hi) // 2
        trial = _greedy_wrap(font, words, mid)
        if len(trial) <= target:
            best, hi = trial, mid - 1
        else:
            lo = mid + 1
    return best


@dataclass(frozen=True)
class _TextBlock:
    lines: list[str]
    font: ImageFont.FreeTypeFont
    line_height: int
    height: int


def _fit_headline(text: str, layout: _FormatLayout, max_block_px: int | None = None) -> _TextBlock:
    """Largest headline size that fits the line and height budget.

    `max_block_px` tightens the height budget further, e.g. to the space
    between the safe zone and the top of the product. Copy is never
    truncated -- it is approved copy -- so if even the minimum size needs
    more room than budgeted, the extra lines are accepted.
    """
    width, height = layout.size
    max_text_width = int(width * (1 - 2 * layout.side_margin))
    max_block = int(height * layout.headline_max_block)
    if max_block_px is not None:
        max_block = min(max_block, max_block_px)

    size = int(width * layout.headline_max)
    min_size = int(width * layout.headline_min)
    while True:
        font = _font(_HEADLINE_FONT, size)
        lines = _balanced_wrap(font, text, max_text_width)
        line_height = int(size * 1.1)
        block_height = line_height * len(lines)
        fits = len(lines) <= layout.headline_max_lines and block_height <= max_block
        if fits or size <= min_size:
            return _TextBlock(lines=lines, font=font, line_height=line_height, height=block_height)
        size = max(min_size, int(size * 0.94))


def _draw_tracked(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, font, fill, tracking: float) -> None:
    """Draw text with letter-spacing (Pillow has no native tracking)."""
    x, y = xy
    for char in text:
        draw.text((x, y), char, font=font, fill=fill)
        x += _text_width(font, char) + tracking


def _tracked_width(font, text: str, tracking: float) -> int:
    return int(sum(_text_width(font, c) for c in text) + tracking * max(len(text) - 1, 0))


# --------------------------------------------------------------------------- #
# Framing
# --------------------------------------------------------------------------- #


# Fractions of the *cropped* frame to keep above the product (eyebrow +
# headline + safe zone) and below it (CTA + safe zone) when choosing where
# to crop a hero that is taller than the target format.
_CROP_HEADROOM = 0.34
_CROP_FOOTROOM = 0.16


def _subject_rows(image: Image.Image) -> tuple[int, int] | None:
    """Vertical extent (top, bottom rows) of the product, or None.

    The hero prompt asks for one product on calm negative space, so the
    product is where edge density is concentrated in the central columns.
    Row profile = the central band's edge map averaged per row (a BOX resize
    to 1 px wide does the averaging). Returns None for a flat image, and a
    near-full-height extent for a busy one -- both mean "no clear subject",
    which degrades to a plain centre crop.
    """
    width, height = image.size
    probe_w = 160
    probe_h = max(1, round(height * probe_w / width))
    edges = image.convert("L").resize((probe_w, probe_h), Image.BILINEAR).filter(ImageFilter.FIND_EDGES)
    band = edges.crop((int(probe_w * 0.2), 2, int(probe_w * 0.8), probe_h - 2))
    column = band.resize((1, band.height), Image.BOX)
    rows = [column.getpixel((0, y)) for y in range(column.height)]
    peak = max(rows, default=0)
    if peak < 8:
        return None
    active = [i for i, v in enumerate(rows) if v >= max(6, peak * 0.3)]
    if not active:
        return None
    scale = height / probe_h
    return int((active[0] + 2) * scale), int((active[-1] + 2) * scale)


def _crop_box(image: Image.Image, target_size: tuple[int, int]) -> tuple[int, int, int, int]:
    """Crop window matching the target aspect ratio.

    Wider sources crop left/right about the centre. Taller sources (a 2:3
    hero going to 1:1) pick the vertical offset that keeps the whole product
    in frame *and* keeps headroom above it for the headline -- a plain centre
    crop throws away most of the negative space the hero prompt reserved at
    the top and parks the headline on the product.
    """
    target_w, target_h = target_size
    target_ratio = target_w / target_h
    src_w, src_h = image.size

    if src_w / src_h > target_ratio:
        new_w = int(src_h * target_ratio)
        left = (src_w - new_w) // 2
        return (left, 0, left + new_w, src_h)

    crop_h = int(src_w / target_ratio)
    slack = src_h - crop_h
    top = slack // 2
    subject = _subject_rows(image) if slack > 0 else None
    if subject is not None:
        subject_top, subject_bottom = subject
        lowest = subject_bottom + _CROP_FOOTROOM * crop_h - crop_h  # keep the base in frame
        highest = subject_top - _CROP_HEADROOM * crop_h  # keep headroom above
        if lowest <= highest:
            top = int(min(max(top, lowest), highest))
        else:  # cannot have both: split the difference
            top = int((lowest + highest) / 2)
        top = min(max(top, 0), slack)
    return (0, top, src_w, top + crop_h)


def _crop_to_fill(image: Image.Image, target_size: tuple[int, int]) -> Image.Image:
    """Crop to the target aspect ratio (subject-aware, see `_crop_box`),
    then resize to the exact target pixel dimensions -- never stretched."""
    return image.crop(_crop_box(image, target_size)).resize(target_size, Image.LANCZOS)


# --------------------------------------------------------------------------- #
# Layered design
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _Design:
    framed: Image.Image  # RGB, the reframed hero
    headline_layer: Image.Image  # RGBA: legibility gradient + eyebrow + headline
    cta_layer: Image.Image  # RGBA: CTA gradient (if needed) + button
    placement: Literal["split", "stacked"]


def _gradient(
    size: tuple[int, int],
    *,
    edge: Literal["top", "bottom"],
    solid_to: int,
    fade: int,
    color: RGB,
    max_alpha: int,
) -> Image.Image:
    """Vertical gradient: `max_alpha` from the `edge` of the frame to the
    row `solid_to` (i.e. behind the whole text block), then easing to 0 over
    `fade` px. Soft falloff reads as lighting, not as a pasted box."""
    _, height = size
    layer = Image.new("RGBA", size, (*color, 0))
    fade = max(fade, 1)
    column = Image.new("L", (1, height), 0)
    px = column.load()
    for y in range(height):
        distance = y - solid_to if edge == "top" else solid_to - y
        t = 1.0 if distance <= 0 else max(0.0, 1 - distance / fade)
        px[0, y] = int(max_alpha * (t * t * (3 - 2 * t)))  # smoothstep
    layer.putalpha(column.resize(size))
    return layer


def _treatment(bg_mean: RGB, busyness: float, palette: list[RGB]) -> tuple[RGB, RGB, int]:
    """Pick ink colour, gradient colour and gradient strength for a zone."""
    dark_palette = [c for c in palette if _relative_luminance(c) < 0.05]
    dark_ink = min(dark_palette, key=_relative_luminance) if dark_palette else _INK_DARK
    ink = dark_ink if _contrast(dark_ink, bg_mean) >= _contrast(_WHITE, bg_mean) else _WHITE
    scrim_color: RGB = (255, 255, 255) if ink != _WHITE else (0, 0, 0)

    contrast = _contrast(ink, bg_mean)
    if contrast >= _TARGET_CONTRAST and busyness <= _CALM_BUSYNESS:
        alpha = 30  # barely-there grade, just to seat the type
    elif contrast >= 3.0 and busyness <= _BUSY_BUSYNESS:
        alpha = 120
    else:
        alpha = 185
    return ink, scrim_color, alpha


def _pick_accent(palette: list[RGB], bg_mean: RGB, ink: RGB) -> RGB:
    """CTA fill: the brand's accent colour, if it stands off the background.

    Saturated palette colours are the candidates, so the button reads as the
    brand accent (the spec asks for one) rather than near-black/near-white.
    Contrast is judged against the zone's *mean* colour, which is often a mix
    of backdrop and product, so the bar is deliberately low -- the pill's
    drop shadow adds separation. Prefer a candidate at >= 1.4:1, accept the
    most saturated one down to 1.15:1, and only below that fall back to the
    ink colour (a solid dark or white pill).
    """
    saturated = sorted(
        (c for c in palette if _saturation(c) >= 0.35), key=_saturation, reverse=True
    )
    for color in saturated:
        if _contrast(color, bg_mean) >= 1.4:
            return color
    if saturated and _contrast(saturated[0], bg_mean) >= 1.15:
        return saturated[0]
    return ink


def _cta_button(text: str, font_size: int, fill: RGB, scale: int = 2) -> Image.Image:
    """Pill button with a soft shadow, drawn at 2x and downsampled so the
    rounded edges are anti-aliased."""
    label = _WHITE if _contrast(_WHITE, fill) >= _contrast(_INK_DARK, fill) else _INK_DARK
    big_font = _font(_UI_FONT, font_size * scale)
    text_w = _text_width(big_font, text)
    ascent, descent = big_font.getmetrics()
    pad_x = int(big_font.size * 1.15)
    pad_y = int(big_font.size * 0.7)
    btn_w = text_w + 2 * pad_x
    btn_h = ascent + descent + 2 * pad_y
    # Room for the blurred shadow on every side, so it is never clipped.
    shadow_pad = pad_y * 2
    blur = pad_y // 2
    offset = pad_y // 3

    canvas = Image.new("RGBA", (btn_w + 2 * shadow_pad, btn_h + 2 * shadow_pad), (0, 0, 0, 0))
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        (shadow_pad, shadow_pad + offset, shadow_pad + btn_w, shadow_pad + btn_h + offset),
        radius=btn_h // 2,
        fill=(0, 0, 0, 70),
    )
    canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(blur)))
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle(
        (shadow_pad, shadow_pad, shadow_pad + btn_w, shadow_pad + btn_h),
        radius=btn_h // 2,
        fill=(*fill, 255),
    )
    left = big_font.getbbox(text)[0]
    draw.text(
        (shadow_pad + pad_x - left, shadow_pad + pad_y),
        text,
        font=big_font,
        fill=(*label, 255),
    )
    return canvas.resize((canvas.width // scale, canvas.height // scale), Image.LANCZOS)


def _design(hero_image: bytes, target_size: tuple[int, int], spec: CreativeSpec) -> _Design:
    layout = _layout_for(target_size)
    width, height = target_size
    with Image.open(io.BytesIO(hero_image)) as src:
        framed = _crop_to_fill(src.convert("RGB"), target_size)

    palette = _palette(spec)
    safe_top = int(height * layout.safe_top)
    safe_bottom = height - int(height * layout.safe_bottom)
    margin_x = int(width * layout.side_margin)

    # --- measure the copy ---------------------------------------------------
    eyebrow_text = str((spec.product_identity or {}).get("name") or "").strip().upper()
    eyebrow_font = _font(_UI_FONT, int(width * layout.eyebrow_size))
    eyebrow_tracking = eyebrow_font.size * 0.22
    eyebrow_h = int(eyebrow_font.size * 1.9) if eyebrow_text else 0
    # Where the product starts, so a top headline can be sized to sit above it.
    subject = _subject_rows(framed)
    room_above_subject = (
        subject[0] - safe_top - eyebrow_h - int(height * 0.025) if subject is not None else None
    )
    headline = _fit_headline(spec.hook.strip(), layout, room_above_subject)
    if room_above_subject is not None and headline.height > room_above_subject:
        # No size fits above the product (e.g. a legacy square hero with no
        # headroom). Shrinking further only makes it small *and* overlapping,
        # so keep the normal size and let the legibility treatment carry it.
        headline = _fit_headline(spec.hook.strip(), layout)
    headline_block_h = eyebrow_h + headline.height

    cta_size = int(width * layout.cta_size)
    cta_probe = _cta_button(spec.cta.strip(), cta_size, _INK_DARK)
    gap = int(height * 0.03)

    # --- decide placement from the image itself ------------------------------
    top_zone = (margin_x, safe_top, width - margin_x, min(safe_top + headline_block_h, height))
    bottom_block_h = headline_block_h + gap + cta_probe.height
    bottom_zone = (margin_x, max(safe_bottom - bottom_block_h, 0), width - margin_x, safe_bottom)
    _, top_busy = _region_stats(framed, top_zone)
    _, bottom_busy = _region_stats(framed, bottom_zone)
    # The hero prompt reserves the top for the headline, so "split" is the
    # default; only move everything down when the top is clearly busier.
    placement: Literal["split", "stacked"] = (
        "stacked" if top_busy > max(bottom_busy * 1.35, _CALM_BUSYNESS) else "split"
    )

    if placement == "split":
        headline_top = safe_top
        cta_top = safe_bottom - cta_probe.height
    else:
        cta_top = safe_bottom - cta_probe.height
        headline_top = cta_top - gap - headline_block_h

    headline_box = (margin_x, headline_top, width - margin_x, headline_top + headline_block_h)
    bg_mean, busy = _region_stats(framed, headline_box)
    ink, scrim_color, scrim_alpha = _treatment(bg_mean, busy, palette)

    # --- headline layer -------------------------------------------------------
    headline_layer = Image.new("RGBA", target_size, (0, 0, 0, 0))
    fade = int(height * 0.12)
    if placement == "split":
        headline_layer.alpha_composite(
            _gradient(target_size, edge="top", solid_to=headline_box[3], fade=fade,
                      color=scrim_color, max_alpha=scrim_alpha)
        )
    else:
        headline_layer.alpha_composite(
            _gradient(target_size, edge="bottom", solid_to=headline_box[1], fade=fade,
                      color=scrim_color, max_alpha=scrim_alpha)
        )

    text_layer = Image.new("RGBA", target_size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(text_layer)
    y = headline_top
    if eyebrow_text:
        ew = _tracked_width(eyebrow_font, eyebrow_text, eyebrow_tracking)
        _draw_tracked(draw, ((width - ew) // 2, y), eyebrow_text, eyebrow_font,
                      (*ink, 215), eyebrow_tracking)
        y += eyebrow_h
    for line in headline.lines:
        left, top, right, _ = headline.font.getbbox(line)
        x = (width - (right - left)) // 2 - left
        draw.text((x, y - top // 2), line, font=headline.font, fill=(*ink, 255))
        y += headline.line_height

    if ink == _WHITE:
        # Soft shadow lifts white type off the photo without a hard stroke.
        shadow = Image.new("RGBA", target_size, (0, 0, 0, 0))
        shadow.putalpha(text_layer.getchannel("A").point(lambda a: int(a * 0.45)))
        headline_layer.alpha_composite(
            shadow.filter(ImageFilter.GaussianBlur(max(3, width // 240)))
        )
    headline_layer.alpha_composite(text_layer)

    # --- CTA layer -----------------------------------------------------------
    cta_layer = Image.new("RGBA", target_size, (0, 0, 0, 0))
    cta_zone = (margin_x, cta_top, width - margin_x, cta_top + cta_probe.height)
    cta_bg, cta_busy = _region_stats(framed, cta_zone)
    if placement == "split":
        cta_ink, cta_scrim, cta_alpha = _treatment(cta_bg, cta_busy, palette)
        # The button carries its own fill, so the gradient only needs to calm
        # a busy area around it -- never more than the headline's treatment.
        if cta_busy > _CALM_BUSYNESS:
            cta_layer.alpha_composite(
                _gradient(target_size, edge="bottom", solid_to=cta_top, fade=fade,
                          color=cta_scrim, max_alpha=min(cta_alpha, 140))
            )
    else:
        cta_ink = ink
    accent = _pick_accent(palette, cta_bg, cta_ink)
    button = _cta_button(spec.cta.strip(), cta_size, accent)
    cta_layer.alpha_composite(button, ((width - button.width) // 2, cta_top))

    return _Design(framed=framed, headline_layer=headline_layer, cta_layer=cta_layer, placement=placement)


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def _to_jpeg(image: Image.Image) -> bytes:
    out = io.BytesIO()
    image.convert("RGB").save(out, format="JPEG", quality=95, subsampling=0, optimize=True)
    return out.getvalue()


def _to_png(image: Image.Image) -> bytes:
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def _composite(hero_image: bytes, target_size: tuple[int, int], spec: CreativeSpec) -> bytes:
    design = _design(hero_image, target_size, spec)
    canvas = design.framed.convert("RGBA")
    canvas.alpha_composite(design.headline_layer)
    canvas.alpha_composite(design.cta_layer)
    return _to_jpeg(canvas)


def compose_1x1(hero_image: bytes, spec: CreativeSpec) -> bytes:
    """Reframe the hero image to exactly 1080x1080 and lay out eyebrow,
    headline and CTA button for the feed format."""
    return _composite(hero_image, _1X1_SIZE, spec)


def compose_9x16(hero_image: bytes, spec: CreativeSpec) -> bytes:
    """Reframe the hero image to exactly 1080x1920 and lay out eyebrow,
    headline and CTA button inside the Stories/Reels safe zones."""
    return _composite(hero_image, _9X16_SIZE, spec)


@dataclass(frozen=True)
class VideoLayers:
    """PNG layers for the video, all exactly `size`. See VideoRenderSpec."""

    background_frame: bytes
    headline_layer: bytes
    end_card_frame: bytes
    cta_layer: bytes


def render_video_layers(hero_image: bytes, spec: CreativeSpec, size: tuple[int, int]) -> VideoLayers:
    """The 9:16 ad design, split into the layers the video animates, so the
    video's typography is identical to the still ads'."""
    design = _design(hero_image, size, spec)
    end_card = design.framed.convert("RGBA")
    end_card.alpha_composite(design.headline_layer)
    return VideoLayers(
        background_frame=_to_png(design.framed),
        headline_layer=_to_png(design.headline_layer),
        end_card_frame=_to_png(end_card.convert("RGB")),
        cta_layer=_to_png(design.cta_layer),
    )
