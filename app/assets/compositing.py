"""Deterministic, non-AI compositing: reframe + designed text overlay.

Pure functions -- bytes/data in, bytes out. No provider calls and no I/O
beyond image processing (and reading the bundled fonts), so these stay
trivially reusable from an activity without needing any port. This is what
turns one hero-image generation into two coordinated ad formats and the
video's text layers, rather than independent generations.

Where the copy comes from: the headline is the spec's ``hook`` and the CTA is the
brief's call to action verbatim (see app/creative/service.py). The image model
never draws either -- the hero prompt forbids text -- so every character on the
ad is rendered here, deterministically.

How placement is decided, per image and per format:

1. **Vision check.** ``_subject_mask`` estimates where the product is (colour
   distance from the backdrop + edge density, surfaces removed). No model call.
2. **Candidates.** Several layouts that respect Meta's safe zones: headline
   top / bottom / in a side column beside an off-centre product, centred or
   left-aligned, sized to the room above the product where possible.
3. **Scoring.** Each candidate is scored on collision with the product
   (heavily penalised), busyness and contrast under the type, and headline
   size; the best wins. A remaining collision is flagged in the report.
4. **Render + validate.** Ink colour follows the luminance behind the text; a
   soft gradient (never a box) is added as needed; the worst line's contrast
   is re-measured after rendering and the gradient strengthened until it reads.

Type systems (``typography_style`` on the spec) pair the type with the image, as
the reference ads do: ``editorial_serif`` (Playfair Display, spaced capitals,
hairline divider, outlined CTA), ``bold_athletic`` (Anton condensed capitals with
an italic serif kicker, solid CTA pill) and ``modern_clean`` (Inter Display).
Output is JPEG q95 with 4:4:4 chroma so coloured type edges stay crisp. Every
composition returns a ``LayoutReport`` explaining its decisions.

The same design is exposed as separate layers (``render_video_layers``) so
the video animates exactly the typography the still ads use.
"""
from __future__ import annotations

import io
import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageStat

if TYPE_CHECKING:
    from app.creative.models import CreativeSpec

__all__ = [
    "LayoutReport",
    "VideoLayers",
    "compose_with_report",
    "compose_1x1",
    "compose_9x16",
    "render_video_layers",
]

_1X1_SIZE = (1080, 1080)
_9X16_SIZE = (1080, 1920)

_FONT_DIR = Path(__file__).parent / "fonts"
_HEADLINE_FONT = _FONT_DIR / "InterDisplay-ExtraBold.ttf"
_UI_FONT = _FONT_DIR / "InterDisplay-SemiBold.ttf"
# Playfair Display ships as unmodified variable fonts (its OFL reserves the name for
# derivatives); the weight is chosen at load time.
_SERIF_FONT = _FONT_DIR / "PlayfairDisplay-VF.ttf"
_SERIF_ITALIC_FONT = _FONT_DIR / "PlayfairDisplay-Italic-VF.ttf"
_CONDENSED_FONT = _FONT_DIR / "Anton-Regular.ttf"
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


@lru_cache(maxsize=128)
def _font(path: Path, size: int, weight: int | None = None) -> ImageFont.FreeTypeFont:
    for candidate in (str(path), *_FALLBACK_FONTS):
        try:
            font = ImageFont.truetype(candidate, size)
        except OSError:
            continue
        if weight is not None and candidate == str(path):
            try:
                font.set_variation_by_axes([weight])
            except (OSError, AttributeError):
                pass  # not a variable font, or FreeType without MM support
        return font
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
    """Largest single-tier headline (default type system) that fits the layout's
    line and height budget, optionally tightened to `max_block_px`. Copy is never
    truncated: at the minimum size, extra lines are accepted."""
    width, height = layout.size
    budget = int(height * layout.headline_max_block)
    if max_block_px is not None:
        budget = min(budget, max_block_px)
    lines, font, size = _fit_lines(
        text,
        _HEADLINE_FONT,
        None,
        max_width=int(width * (1 - 2 * layout.side_margin)),
        max_height=budget,
        size_max=int(width * layout.headline_max),
        size_min=int(width * layout.headline_min),
        max_lines=layout.headline_max_lines,
        line_height=1.1,
    )
    line_height = int(size * 1.1)
    return _TextBlock(lines=lines, font=font, line_height=line_height, height=line_height * len(lines))


def _draw_tracked(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, font, fill, tracking: float) -> None:
    """Draw text with letter-spacing (Pillow has no native tracking)."""
    x, y = xy
    for char in text:
        draw.text((x, y), char, font=font, fill=fill)
        x += _text_width(font, char) + tracking


def _tracked_width(font, text: str, tracking: float) -> int:
    return int(sum(_text_width(font, c) for c in text) + tracking * max(len(text) - 1, 0))


# --------------------------------------------------------------------------- #
# Vision check: where is the product?
# --------------------------------------------------------------------------- #
#
# A deterministic, dependency-free saliency estimate (Pillow only, ~5 ms). The hero
# prompt asks for one product on a calm backdrop, so "the product" is whatever
# differs from the backdrop colour or carries dense edges. Full-width bands
# (a table top, a horizon) are removed: they are surfaces, not the product, and it
# is fine for a CTA to sit on them.

_PROBE_W = 160
_MASK_ON = 255


@lru_cache(maxsize=16)
def _subject_mask_cached(key: bytes, size: tuple[int, int], mode: str) -> Image.Image:
    image = Image.frombytes(mode, size, key)
    width, height = size
    probe_h = max(8, round(height * _PROBE_W / width))
    small = image.convert("RGB").resize((_PROBE_W, probe_h), Image.BILINEAR)

    # Backdrop colour: median of the top strip and the outer side strips. The bottom
    # is excluded -- products usually stand on a surface of a different colour.
    strips = [
        small.crop((0, 0, _PROBE_W, max(2, probe_h // 10))),
        small.crop((0, 0, max(2, _PROBE_W // 16), probe_h)),
        small.crop((_PROBE_W - max(2, _PROBE_W // 16), 0, _PROBE_W, probe_h)),
    ]
    medians = [ImageStat.Stat(s).median for s in strips]
    backdrop = tuple(sorted(m[c] for m in medians)[1] for c in range(3))

    colour_diff = ImageChops.difference(small, Image.new("RGB", small.size, backdrop)).convert("L")
    # Edges on a slightly blurred copy: object contours survive, fine texture
    # (foliage, stone grain, bokeh) does not.
    edges = (
        small.convert("L")
        .filter(ImageFilter.GaussianBlur(1.2))
        .filter(ImageFilter.FIND_EDGES)
        .filter(ImageFilter.MaxFilter(3))
    )
    saliency = ImageChops.lighter(
        colour_diff.point(lambda v: min(255, int(v * 1.6))),
        edges.point(lambda v: min(255, v * 4)),
    ).filter(ImageFilter.GaussianBlur(1.5))
    # Mild horizontal centre prior: the hero prompt centres the product, so equal
    # saliency near the edges is more likely a prop or backdrop detail.
    prior = Image.new("L", (_PROBE_W, 1))
    for x in range(_PROBE_W):
        dx = abs(x - (_PROBE_W - 1) / 2) / ((_PROBE_W - 1) / 2)
        prior.putpixel((x, 0), int(255 * (1 - 0.45 * dx**1.5)))
    saliency = ImageChops.multiply(saliency, prior.resize(small.size))
    mask = saliency.point(lambda v: _MASK_ON if v > 80 else 0)
    mask = mask.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.MaxFilter(5))
    # FIND_EDGES lights up the frame border itself; that is never the product.
    border = 5
    for rect in ((0, 0, _PROBE_W, border), (0, probe_h - border, _PROBE_W, probe_h),
                 (0, 0, border, probe_h), (_PROBE_W - border, 0, _PROBE_W, probe_h)):
        mask.paste(0, rect)

    # Drop full-width bands (surfaces / horizons).
    row_cover = mask.resize((1, probe_h), Image.BOX)
    for y in range(probe_h):
        if row_cover.getpixel((0, y)) > 0.85 * _MASK_ON:
            mask.paste(0, (0, y, _PROBE_W, y + 1))
    return mask.resize(size, Image.BILINEAR).point(lambda v: _MASK_ON if v > 127 else 0)


def _subject_mask(image: Image.Image) -> Image.Image:
    """Binary mask ("L", same size) of where the product (and salient props) are."""
    small = image if image.width <= 540 else image.resize((540, round(image.height * 540 / image.width)))
    mask = _subject_mask_cached(small.tobytes(), small.size, small.mode)
    return mask if mask.size == image.size else mask.resize(image.size, Image.NEAREST)


def _subject_box(image: Image.Image) -> tuple[int, int, int, int] | None:
    """Bounding box of the main salient mass, or None if there is no clear subject.

    Robust to scattered specks (a petal in a corner): the box is the mask's
    centroid +/- 2 standard deviations per axis, clipped to the mask's extent.
    A box covering nearly the whole frame means a busy scene with no single
    subject -- returned as None so callers fall back to centred crops.
    """
    mask = _subject_mask(image)
    width, height = mask.size
    probe_h = max(1, round(height * _PROBE_W / width))
    small = mask.resize((_PROBE_W, probe_h), Image.NEAREST)
    data = small.tobytes()
    xs: list[int] = []
    ys: list[int] = []
    for index, value in enumerate(data):
        if value:
            ys.append(index // _PROBE_W)
            xs.append(index % _PROBE_W)
    if len(xs) < 0.01 * _PROBE_W * probe_h:
        return None

    def span(values: list[int]) -> tuple[float, float]:
        mean = sum(values) / len(values)
        std = (sum((v - mean) ** 2 for v in values) / len(values)) ** 0.5
        return max(min(values), mean - 2 * std), min(max(values) + 1, mean + 2 * std)

    x0, x1 = span(xs)
    y0, y1 = span(ys)
    sx, sy = width / _PROBE_W, height / probe_h
    box = (int(x0 * sx), int(y0 * sy), int(x1 * sx), int(y1 * sy))
    if (box[2] - box[0]) * (box[3] - box[1]) > 0.85 * width * height:
        return None
    return box


def _first_salient_row(mask: Image.Image, x0: int, x1: int, *, from_top: bool = True, threshold: float = 0.03) -> int:
    """First row (scanning from the top or bottom) where more than `threshold` of
    the columns [x0, x1) are salient -- i.e. where text in that column would
    start touching the product or a prop."""
    band = mask.crop((max(0, x0), 0, min(mask.width, x1), mask.height))
    rows = band.resize((1, band.height), Image.BOX)
    order = range(band.height) if from_top else range(band.height - 1, -1, -1)
    for y in order:
        if rows.getpixel((0, y)) > threshold * _MASK_ON:
            return y
    return band.height if from_top else 0


def _coverage(mask: Image.Image, rects: Sequence[tuple[int, int, int, int]]) -> float:
    """Area-weighted fraction of `rects` that the mask marks as subject."""
    covered = total = 0.0
    for left, top, right, bottom in rects:
        box = (max(0, left), max(0, top), min(mask.width, right), min(mask.height, bottom))
        area = max(0, box[2] - box[0]) * max(0, box[3] - box[1])
        if area == 0:
            continue
        covered += ImageStat.Stat(mask.crop(box)).mean[0] / _MASK_ON * area
        total += area
    return covered / total if total else 0.0


# --------------------------------------------------------------------------- #
# Framing
# --------------------------------------------------------------------------- #


# Fractions of the *cropped* frame to keep above the product (eyebrow +
# headline + safe zone) and below it (CTA + safe zone) when choosing where
# to crop a hero that is taller than the target format.
_CROP_HEADROOM = 0.34
_CROP_FOOTROOM = 0.20


def _crop_box(image: Image.Image, target_size: tuple[int, int]) -> tuple[int, int, int, int]:
    """Crop window matching the target aspect ratio, placed around the product.

    Taller sources (a 2:3 hero going to 1:1) pick the vertical offset that keeps
    the whole product in frame *and* headroom above it for the headline; wider
    sources centre the window on the product horizontally. A plain centre crop
    throws away the negative space the hero prompt reserved and parks the
    headline on the product.
    """
    target_w, target_h = target_size
    target_ratio = target_w / target_h
    src_w, src_h = image.size
    subject = _subject_box(image)

    if src_w / src_h > target_ratio:
        new_w = int(src_h * target_ratio)
        slack = src_w - new_w
        left = slack // 2
        if subject is not None:
            left = int((subject[0] + subject[2]) / 2 - new_w / 2)
            left = min(max(left, 0), slack)
        return (left, 0, left + new_w, src_h)

    crop_h = int(src_w / target_ratio)
    slack = src_h - crop_h
    top = slack // 2
    if subject is not None and slack > 0:
        subject_top, subject_bottom = subject[1], subject[3]
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
# Type systems
# --------------------------------------------------------------------------- #
#
# Chosen by the creative spec (`typography_style`) so the type suits the brand,
# the way the reference ads pair their typography with their imagery: refined
# serif + spaced capitals + understated CTA for premium goods; heavy condensed
# capitals + solid CTA for performance products; clean sans for everything else.


@dataclass(frozen=True)
class _TypeStyle:
    name: str
    headline_font: Path
    headline_weight: int | None  # variable-font weight, None for static fonts
    headline_upper: bool
    headline_line_height: float
    headline_scale: float  # condensed faces can run larger in the same width
    kicker_font: Path | None  # two-tier headline: small italic line above
    kicker_weight: int | None
    eyebrow_tracking: float  # em
    cta_kind: Literal["pill", "outline"]
    cta_font: Path
    cta_weight: int | None
    cta_upper: bool
    cta_tracking: float  # em
    divider: bool
    alignments: tuple[Literal["center", "left"], ...]  # most preferred first


_STYLES: dict[str, _TypeStyle] = {
    "modern_clean": _TypeStyle(
        name="modern_clean",
        headline_font=_HEADLINE_FONT, headline_weight=None, headline_upper=False,
        headline_line_height=1.1, headline_scale=1.0,
        kicker_font=None, kicker_weight=None,
        eyebrow_tracking=0.22,
        cta_kind="pill", cta_font=_UI_FONT, cta_weight=None, cta_upper=False, cta_tracking=0.0,
        divider=False, alignments=("center", "left"),
    ),
    "editorial_serif": _TypeStyle(
        name="editorial_serif",
        headline_font=_SERIF_FONT, headline_weight=600, headline_upper=False,
        headline_line_height=1.14, headline_scale=1.0,
        kicker_font=_SERIF_ITALIC_FONT, kicker_weight=500,
        eyebrow_tracking=0.34,
        cta_kind="outline", cta_font=_SERIF_FONT, cta_weight=600, cta_upper=True, cta_tracking=0.2,
        divider=True, alignments=("center",),
    ),
    "bold_athletic": _TypeStyle(
        name="bold_athletic",
        headline_font=_CONDENSED_FONT, headline_weight=None, headline_upper=True,
        headline_line_height=1.02, headline_scale=1.2,
        kicker_font=_SERIF_ITALIC_FONT, kicker_weight=500,
        eyebrow_tracking=0.28,
        cta_kind="pill", cta_font=_UI_FONT, cta_weight=None, cta_upper=True, cta_tracking=0.08,
        divider=False, alignments=("left", "center"),
    ),
}


def _style_for(spec: CreativeSpec) -> _TypeStyle:
    return _STYLES.get(getattr(spec, "typography_style", None) or "modern_clean", _STYLES["modern_clean"])


def _split_hook(hook: str) -> tuple[str | None, str]:
    """Split "Every conversation. Deserves its own fragrance." into a small kicker
    and the main line, at the first sentence break. The copy itself is unchanged."""
    match = re.match(r"^(.{3,60}?[.!?:—–])\s+(\S.{2,})$", hook.strip())
    if not match:
        return None, hook.strip()
    return match.group(1), match.group(2)


# --------------------------------------------------------------------------- #
# Text blocks
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _Line:
    kind: Literal["eyebrow", "kicker", "headline", "divider"]
    text: str
    font: ImageFont.FreeTypeFont | None
    x: int  # relative to the block's left edge
    y: int  # relative to the block's top edge
    width: int
    height: int
    tracking: float = 0.0


@dataclass(frozen=True)
class _Block:
    lines: tuple[_Line, ...]
    width: int
    height: int
    headline_size: int


def _fit_lines(
    text: str,
    font_path: Path,
    weight: int | None,
    *,
    max_width: int,
    max_height: int,
    size_max: int,
    size_min: int,
    max_lines: int,
    line_height: float,
) -> tuple[list[str], ImageFont.FreeTypeFont, int]:
    """Largest size (<= size_max) whose balanced wrap fits the width, line count
    and height budget; never below size_min, never truncating."""
    size = size_max
    while True:
        font = _font(font_path, size, weight)
        lines = _balanced_wrap(font, text, max_width)
        fits = len(lines) <= max_lines and int(size * line_height) * len(lines) <= max_height
        if fits or size <= size_min:
            return lines, font, size
        size = max(size_min, int(size * 0.94))


def _build_block(
    spec: CreativeSpec,
    style: _TypeStyle,
    layout: _FormatLayout,
    *,
    max_width: int,
    max_height: int,
    align: Literal["center", "left"],
) -> _Block:
    width = layout.size[0]
    kicker_text, main_text = _split_hook(spec.hook) if style.kicker_font else (None, spec.hook.strip())
    if style.headline_upper:
        main_text = main_text.upper()

    eyebrow_text = str((spec.product_identity or {}).get("name") or "").strip().upper()
    eyebrow_font = _font(_UI_FONT, int(width * layout.eyebrow_size))
    eyebrow_tracking = eyebrow_font.size * style.eyebrow_tracking
    eyebrow_h = int(eyebrow_font.size * 1.9) if eyebrow_text else 0

    kicker_lines: list[str] = []
    kicker_font = None
    kicker_h = 0
    if kicker_text and style.kicker_font:
        kicker_lines, kicker_font, kicker_size = _fit_lines(
            kicker_text, style.kicker_font, style.kicker_weight,
            max_width=max_width, max_height=10_000,
            size_max=int(width * layout.headline_max * 0.62), size_min=int(width * 0.034),
            max_lines=2, line_height=1.2,
        )
        kicker_h = int(kicker_size * 1.2) * len(kicker_lines) + int(kicker_size * 0.15)

    divider_h = int(width * 0.04) if style.divider else 0
    headline_budget = max(max_height - eyebrow_h - kicker_h - divider_h, int(width * layout.headline_min * 1.2))
    lines, font, size = _fit_lines(
        main_text, style.headline_font, style.headline_weight,
        max_width=max_width, max_height=headline_budget,
        size_max=int(width * layout.headline_max * style.headline_scale),
        size_min=int(width * layout.headline_min * style.headline_scale),
        max_lines=layout.headline_max_lines, line_height=style.headline_line_height,
    )

    items: list[_Line] = []
    y = 0

    def place(w: int) -> int:
        return (max_width - w) // 2 if align == "center" else 0

    if eyebrow_text:
        ew = _tracked_width(eyebrow_font, eyebrow_text, eyebrow_tracking)
        items.append(_Line("eyebrow", eyebrow_text, eyebrow_font, place(ew), y, ew, eyebrow_font.size, eyebrow_tracking))
        y += eyebrow_h
    for text in kicker_lines:
        assert kicker_font is not None
        kw = _text_width(kicker_font, text)
        items.append(_Line("kicker", text, kicker_font, place(kw), y, kw, kicker_font.size))
        y += int(kicker_font.size * 1.2)
    if kicker_lines:
        y += int(kicker_font.size * 0.15) if kicker_font else 0
    step = int(size * style.headline_line_height)
    for text in lines:
        lw = _text_width(font, text)
        items.append(_Line("headline", text, font, place(lw), y, lw, size))
        y += step
    if style.divider:
        rule_w = int(width * 0.07)
        items.append(_Line("divider", "", None, place(rule_w), y + divider_h // 2 - 1, rule_w, 2))
        y += divider_h
    block_w = max((i.x + i.width for i in items), default=0)
    return _Block(lines=tuple(items), width=max(block_w, 1), height=y, headline_size=size)


# --------------------------------------------------------------------------- #
# CTA
# --------------------------------------------------------------------------- #


def _render_cta(
    text: str, style: _TypeStyle, layout: _FormatLayout, *, fill: RGB, max_width: int, scale: int = 2
) -> Image.Image:
    """The brief's CTA, verbatim (casing aside), as a button that always fits.

    `pill`: solid accent pill with a soft shadow. `outline`: spaced capitals in a
    hairline rectangle -- the understated luxury treatment of the reference ads.
    Shrinks to fit `max_width`, and wraps to two lines only if it must. Drawn at 2x
    and downsampled for anti-aliased edges.
    """
    label = text.strip().upper() if style.cta_upper else text.strip()
    width = layout.size[0]
    size = int(width * layout.cta_size)
    min_size = int(width * 0.026)
    while True:
        font = _font(style.cta_font, size * scale, style.cta_weight)
        tracking = font.size * style.cta_tracking
        pad_x = int(font.size * (1.15 if style.cta_kind == "pill" else 1.5))
        lines = [label]
        if _tracked_width(font, label, tracking) + 2 * pad_x > max_width * scale and size <= min_size:
            lines = _balanced_wrap(font, label, max_width * scale - 2 * pad_x)[:2]
        text_w = max(_tracked_width(font, line, tracking) for line in lines)
        if text_w + 2 * pad_x <= max_width * scale or size <= min_size:
            break
        size = max(min_size, int(size * 0.92))

    ascent, descent = font.getmetrics()
    line_h = ascent + descent
    pad_y = int(font.size * (0.7 if style.cta_kind == "pill" else 0.85))
    btn_w = text_w + 2 * pad_x
    btn_h = line_h * len(lines) + 2 * pad_y
    shadow_pad = pad_y * 2
    canvas = Image.new("RGBA", (btn_w + 2 * shadow_pad, btn_h + 2 * shadow_pad), (0, 0, 0, 0))
    rect = (shadow_pad, shadow_pad, shadow_pad + btn_w, shadow_pad + btn_h)

    if style.cta_kind == "pill":
        radius = min(btn_h // 2, int(line_h * 0.9))
        shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        offset = pad_y // 3
        ImageDraw.Draw(shadow).rounded_rectangle(
            (rect[0], rect[1] + offset, rect[2], rect[3] + offset), radius=radius, fill=(0, 0, 0, 70)
        )
        canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(pad_y // 2)))
        ImageDraw.Draw(canvas).rounded_rectangle(rect, radius=radius, fill=(*fill, 255))
        label_colour = _WHITE if _contrast(_WHITE, fill) >= _contrast(_INK_DARK, fill) else _INK_DARK
    else:
        ImageDraw.Draw(canvas).rectangle(rect, outline=(*fill, 255), width=max(2, scale * 2))
        label_colour = fill

    draw = ImageDraw.Draw(canvas)
    y = shadow_pad + pad_y
    for line in lines:
        lw = _tracked_width(font, line, tracking)
        x = shadow_pad + (btn_w - lw) // 2 - font.getbbox(line)[0]
        _draw_tracked(draw, (x, y), line, font, (*label_colour, 255), tracking)
        y += line_h
    return canvas.resize((canvas.width // scale, canvas.height // scale), Image.LANCZOS)


# --------------------------------------------------------------------------- #
# Candidate layouts, scored against the image
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _Candidate:
    placement: Literal["top", "top_stack", "bottom", "side_left", "side_right"]
    align: Literal["center", "left"]
    block: _Block
    block_xy: tuple[int, int]
    cta: Image.Image  # geometry probe; the final button is re-rendered in colour
    cta_xy: tuple[int, int]
    cta_max_width: int

    def text_rects(self) -> list[tuple[int, int, int, int]]:
        bx, by = self.block_xy
        rects = [
            (bx + ln.x, by + ln.y, bx + ln.x + ln.width, by + ln.y + int(ln.height * 1.1))
            for ln in self.block.lines
        ]
        cx, cy = self.cta_xy
        rects.append((cx, cy, cx + self.cta.width, cy + self.cta.height))
        return rects

    def block_rect(self) -> tuple[int, int, int, int]:
        bx, by = self.block_xy
        left = min((ln.x for ln in self.block.lines), default=0)
        return (bx + left, by, bx + self.block.width, by + self.block.height)


@dataclass(frozen=True)
class LayoutReport:
    """Why the copy went where it did -- persisted with the asset for inspection."""

    style: str
    placement: str
    align: str
    headline_size: int
    subject_box: tuple[int, int, int, int] | None
    subject_overlap: float
    headline_overlap: float
    cta_overlap: float
    busyness: float
    contrast: float
    scrim_alpha: int
    collision: bool
    candidates_considered: int

    def as_dict(self) -> dict:
        return {k: (list(v) if isinstance(v, tuple) else v) for k, v in self.__dict__.items()}


def _candidates(
    spec: CreativeSpec,
    style: _TypeStyle,
    layout: _FormatLayout,
    subject: tuple[int, int, int, int] | None,
    mask: Image.Image,
) -> list[_Candidate]:
    width, height = layout.size
    safe_top = int(height * layout.safe_top)
    safe_bottom = height - int(height * layout.safe_bottom)
    margin = int(width * layout.side_margin)
    full_w = width - 2 * margin
    gap = int(height * 0.03)
    default_block = int(height * layout.headline_max_block)
    # The CTA's size does not depend on colour, so measure it once for geometry.
    cta_probe = _render_cta(spec.cta, style, layout, fill=_INK_DARK, max_width=full_w)

    out: list[_Candidate] = []

    def add(placement, align, max_w, max_h, x0, cta_max_w):
        block = _build_block(spec, style, layout, max_width=max_w, max_height=max_h, align=align)
        cta = cta_probe if cta_max_w == full_w else _render_cta(spec.cta, style, layout, fill=_INK_DARK, max_width=cta_max_w)
        if placement == "top":
            by = safe_top
            cy = safe_bottom - cta.height
        elif placement == "top_stack":  # headline and CTA together above the product
            by = safe_top
            cy = by + block.height + gap // 2
        elif placement == "bottom":
            cy = safe_bottom - cta.height
            by = cy - gap - block.height
        else:  # side column: block from the top, CTA right under it
            by = safe_top
            cy = min(by + block.height + gap, safe_bottom - cta.height)
        bx = x0  # lines are already aligned within the column by _build_block
        cx = x0 if align == "left" else x0 + (max_w - cta.width) // 2
        if by < safe_top - 1 or cy + cta.height > safe_bottom + 1:
            return  # does not fit inside the safe zones
        out.append(_Candidate(placement, align, block, (bx, by), cta, (cx, cy), cta_max_w))

    # Free space above/below the product across the text column, from the mask.
    room_above = _first_salient_row(mask, margin, width - margin) - safe_top - gap
    room_below = safe_bottom - cta_probe.height - gap - _first_salient_row(mask, margin, width - margin, from_top=False) - gap
    min_block = int(width * layout.headline_min * 1.5)
    for align in style.alignments:
        # Each placement at full size, plus a variant sized to the free space next
        # to the product; scoring then trades headline size against collision.
        stack_room = room_above - cta_probe.height - gap // 2
        for placement, room in (("top", room_above), ("top_stack", stack_room), ("bottom", room_below)):
            budgets = {default_block}
            if min_block <= room < default_block:
                budgets.add(room)
            for budget in budgets:
                add(placement, align, full_w, budget, margin, full_w)

    if subject is not None:
        col_w = int(width * 0.46)
        if subject[0] >= margin + col_w * 0.9:
            add("side_left", "left", col_w, int(height * 0.45), margin, col_w)
        if width - subject[2] >= margin + col_w * 0.9:
            add("side_right", "left", col_w, int(height * 0.45), width - margin - col_w, col_w)
    return out


def _score(
    cand: _Candidate, framed: Image.Image, mask: Image.Image, style: _TypeStyle, layout: _FormatLayout
) -> tuple[float, float, float, float]:
    """Higher is better. Returns (score, overlap, busyness, best contrast)."""
    rects = cand.text_rects()
    # The headline covering the product is worse than the CTA touching its base.
    overlap = 0.65 * _coverage(mask, rects[:-1]) + 0.35 * _coverage(mask, rects[-1:])
    means, busy_values = [], []
    for rect in rects:
        mean, busy = _region_stats(framed, rect)
        means.append(mean)
        busy_values.append(busy)
    busy = sum(busy_values) / len(busy_values)
    bg = tuple(int(sum(m[i] for m in means) / len(means)) for i in range(3))
    contrast = max(_contrast(_WHITE, bg), _contrast(_INK_DARK, bg))
    size_ratio = cand.block.headline_size / (layout.size[0] * layout.headline_max * style.headline_scale)
    prior = (0.15 if cand.align == style.alignments[0] else 0.0) + (0.1 if cand.placement == "top" else 0.0)
    score = (
        1.0 * size_ratio
        + 0.6 * min(contrast / 7.0, 1.0)
        - 0.6 * min(busy / 0.06, 1.0)
        - 6.0 * overlap
        + prior
    )
    return score, overlap, busy, contrast


# --------------------------------------------------------------------------- #
# Colour treatment
# --------------------------------------------------------------------------- #


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


# --------------------------------------------------------------------------- #
# Layered design
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _Design:
    framed: Image.Image  # RGB, the reframed hero
    headline_layer: Image.Image  # RGBA: legibility gradient + eyebrow/kicker/headline
    cta_layer: Image.Image  # RGBA: CTA gradient (if needed) + button
    placement: str
    report: LayoutReport


def _gradient(
    size: tuple[int, int],
    *,
    edge: Literal["top", "bottom", "left", "right"],
    solid_to: int,
    fade: int,
    color: RGB,
    max_alpha: int,
) -> Image.Image:
    """Gradient: `max_alpha` from the `edge` of the frame to `solid_to` (behind
    the text), then easing to 0 over `fade` px. Soft falloff reads as lighting,
    not as a pasted box."""
    width, height = size
    vertical = edge in ("top", "bottom")
    length = height if vertical else width
    fade = max(fade, 1)
    ramp = Image.new("L", (1, length) if vertical else (length, 1), 0)
    px = ramp.load()
    for i in range(length):
        distance = i - solid_to if edge in ("top", "left") else solid_to - i
        t = 1.0 if distance <= 0 else max(0.0, 1 - distance / fade)
        px[(0, i) if vertical else (i, 0)] = int(max_alpha * (t * t * (3 - 2 * t)))  # smoothstep
    layer = Image.new("RGBA", size, (*color, 0))
    layer.putalpha(ramp.resize(size))
    return layer


def _draw_block(
    target_size: tuple[int, int], cand: _Candidate, ink: RGB, accent: RGB, style: _TypeStyle
) -> Image.Image:
    layer = Image.new("RGBA", target_size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    bx, by = cand.block_xy
    for ln in cand.block.lines:
        x, y = bx + ln.x, by + ln.y
        if ln.kind == "divider":
            draw.rectangle((x, y, x + ln.width, y + ln.height), fill=(*accent, 255))
        elif ln.kind == "eyebrow":
            colour = accent if style.divider and _contrast(accent, _INK_DARK if ink == _WHITE else _WHITE) >= 3 else ink
            _draw_tracked(draw, (x, y), ln.text, ln.font, (*colour, 225), ln.tracking)
        else:
            assert ln.font is not None
            left, top, _, _ = ln.font.getbbox(ln.text)
            draw.text((x - left, y - top // 2), ln.text, font=ln.font, fill=(*ink, 255))
    return layer


@dataclass(frozen=True)
class _Rendered:
    headline_layer: Image.Image
    cta_layer: Image.Image
    scrim: Image.Image  # the headline's legibility gradient alone, for validation
    ink: RGB
    scrim_alpha: int


def _render(
    framed: Image.Image,
    cand: _Candidate,
    style: _TypeStyle,
    layout: _FormatLayout,
    palette: list[RGB],
    spec: CreativeSpec,
    *,
    extra_alpha: int = 0,
) -> _Rendered:
    """Draw one candidate: legibility gradients, type, and the CTA in colour."""
    size = layout.size
    width, height = size
    block_rect = cand.block_rect()
    bg_mean, busy = _region_stats(framed, block_rect)
    ink, scrim_color, scrim_alpha = _treatment(bg_mean, busy, palette)
    scrim_alpha = min(230, scrim_alpha + extra_alpha)
    fade = int(height * 0.12)

    headline_layer = Image.new("RGBA", size, (0, 0, 0, 0))
    if cand.placement == "top":
        g = _gradient(size, edge="top", solid_to=block_rect[3], fade=fade, color=scrim_color, max_alpha=scrim_alpha)
    elif cand.placement == "top_stack":
        g = _gradient(size, edge="top", solid_to=cand.cta_xy[1] + cand.cta.height, fade=fade,
                      color=scrim_color, max_alpha=scrim_alpha)
    elif cand.placement == "bottom":
        g = _gradient(size, edge="bottom", solid_to=block_rect[1], fade=fade, color=scrim_color, max_alpha=scrim_alpha)
    elif cand.placement == "side_left":
        g = _gradient(size, edge="left", solid_to=block_rect[2], fade=int(width * 0.15), color=scrim_color, max_alpha=scrim_alpha)
    else:
        g = _gradient(size, edge="right", solid_to=block_rect[0], fade=int(width * 0.15), color=scrim_color, max_alpha=scrim_alpha)
    headline_layer.alpha_composite(g)

    accent = _pick_accent(palette, bg_mean, ink)
    text_layer = _draw_block(size, cand, ink, accent, style)
    if ink == _WHITE:
        # Soft shadow lifts white type off the photo without a hard stroke.
        shadow = Image.new("RGBA", size, (0, 0, 0, 0))
        shadow.putalpha(text_layer.getchannel("A").point(lambda a: int(a * 0.45)))
        headline_layer.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(max(3, width // 240))))
    headline_layer.alpha_composite(text_layer)

    cta_layer = Image.new("RGBA", size, (0, 0, 0, 0))
    cx, cy = cand.cta_xy
    cta_rect = (cx, cy, cx + cand.cta.width, cy + cand.cta.height)
    cta_bg, cta_busy = _region_stats(framed, cta_rect)
    if cand.placement == "top":
        cta_ink, cta_scrim, cta_alpha = _treatment(cta_bg, cta_busy, palette)
        # The button carries its own fill, so the gradient only needs to calm a
        # busy area around it -- never more than the headline's treatment.
        if cta_busy > _CALM_BUSYNESS or style.cta_kind == "outline":
            cta_layer.alpha_composite(
                _gradient(size, edge="bottom", solid_to=cy, fade=fade, color=cta_scrim,
                          max_alpha=min(cta_alpha + extra_alpha, 160))
            )
    else:
        cta_ink = ink
    if style.cta_kind == "outline":
        # Outline CTAs are drawn in the accent when it reads, otherwise in the ink.
        fill = accent if _contrast(accent, cta_bg) >= 3.0 else cta_ink
    else:
        fill = _pick_accent(palette, cta_bg, cta_ink)
    button = _render_cta(spec.cta, style, layout, fill=fill, max_width=cand.cta_max_width)
    cta_layer.alpha_composite(button, (cx + (cand.cta.width - button.width) // 2, cy))
    return _Rendered(headline_layer, cta_layer, g, ink, scrim_alpha)


def _measured_contrast(
    framed: Image.Image, scrim: Image.Image, rects: Sequence[tuple[int, int, int, int]], ink: RGB
) -> float:
    """Post-render check: contrast between the ink and what is actually behind
    each line of type once the legibility gradient is applied (text pixels
    excluded). Returns the *worst* line, since one unreadable line fails the ad."""
    backdrop = framed.convert("RGBA")
    backdrop.alpha_composite(scrim)
    backdrop = backdrop.convert("RGB")
    return min(_contrast(ink, _region_stats(backdrop, r)[0]) for r in rects)


def _design(hero_image: bytes, target_size: tuple[int, int], spec: CreativeSpec) -> _Design:
    layout = _layout_for(target_size)
    style = _style_for(spec)
    with Image.open(io.BytesIO(hero_image)) as src:
        framed = _crop_to_fill(src.convert("RGB"), target_size)
    palette = _palette(spec)

    # 1. Vision check: where is the product in *this* image?
    mask = _subject_mask(framed)
    subject = _subject_box(framed)

    # 2. Candidate layouts, scored against the image; best one wins.
    candidates = _candidates(spec, style, layout, subject, mask)
    scored = sorted(
        ((_score(c, framed, mask, style, layout), c) for c in candidates),
        key=lambda item: item[0][0],
        reverse=True,
    )
    (score, overlap, busy, _), best = scored[0]
    headline_overlap = _coverage(mask, best.text_rects()[:-1])
    cta_overlap = _coverage(mask, best.text_rects()[-1:])
    collision = headline_overlap > 0.04 or cta_overlap > 0.15

    # 3. Render, then validate legibility against what is actually behind the
    #    text; strengthen the treatment if it falls short (at most twice).
    extra = 40 if collision else 0
    for _ in range(3):
        rendered = _render(framed, best, style, layout, palette, spec, extra_alpha=extra)
        contrast = _measured_contrast(framed, rendered.scrim, best.text_rects()[:-1], rendered.ink)
        if contrast >= _TARGET_CONTRAST or rendered.scrim_alpha >= 230:
            break
        extra += 50
    headline_layer, cta_layer, alpha = rendered.headline_layer, rendered.cta_layer, rendered.scrim_alpha

    report = LayoutReport(
        style=style.name,
        placement=best.placement,
        align=best.align,
        headline_size=best.block.headline_size,
        subject_box=subject,
        subject_overlap=round(overlap, 4),
        headline_overlap=round(headline_overlap, 4),
        cta_overlap=round(cta_overlap, 4),
        busyness=round(busy, 4),
        contrast=round(contrast, 2),
        scrim_alpha=alpha,
        collision=collision,
        candidates_considered=len(candidates),
    )
    return _Design(framed=framed, headline_layer=headline_layer, cta_layer=cta_layer, placement=best.placement, report=report)


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


def compose_with_report(
    hero_image: bytes, spec: CreativeSpec, target_size: tuple[int, int]
) -> tuple[bytes, dict]:
    """Compose one ad and return it with the layout report (why the copy went
    where it did), which the activity stores alongside the asset."""
    design = _design(hero_image, target_size, spec)
    canvas = design.framed.convert("RGBA")
    canvas.alpha_composite(design.headline_layer)
    canvas.alpha_composite(design.cta_layer)
    return _to_jpeg(canvas), design.report.as_dict()


def _composite(hero_image: bytes, target_size: tuple[int, int], spec: CreativeSpec) -> bytes:
    return compose_with_report(hero_image, spec, target_size)[0]


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


# =========================================================================== #
# LEGACY LAYOUT (commented out, kept for comparison / revert)
# =========================================================================== #
#
# The single-pass layout this module used before content-aware placement: a
# row-profile "subject" estimate, one decision between "split" and "stacked",
# one type system (Inter), and an LLM-worded CTA pill. To revert, restore these
# definitions in place of their replacements above (`_subject_box`/`_crop_box`,
# `_Design`, `_gradient`, `_render_cta`, `_design`).
#
# def _subject_rows(image: Image.Image) -> tuple[int, int] | None:
#     """Vertical extent (top, bottom rows) of the product, or None.
#
#     The hero prompt asks for one product on calm negative space, so the
#     product is where edge density is concentrated in the central columns.
#     Row profile = the central band's edge map averaged per row (a BOX resize
#     to 1 px wide does the averaging). Returns None for a flat image, and a
#     near-full-height extent for a busy one -- both mean "no clear subject",
#     which degrades to a plain centre crop.
#     """
#     width, height = image.size
#     probe_w = 160
#     probe_h = max(1, round(height * probe_w / width))
#     edges = image.convert("L").resize((probe_w, probe_h), Image.BILINEAR).filter(ImageFilter.FIND_EDGES)
#     band = edges.crop((int(probe_w * 0.2), 2, int(probe_w * 0.8), probe_h - 2))
#     column = band.resize((1, band.height), Image.BOX)
#     rows = [column.getpixel((0, y)) for y in range(column.height)]
#     peak = max(rows, default=0)
#     if peak < 8:
#         return None
#     active = [i for i, v in enumerate(rows) if v >= max(6, peak * 0.3)]
#     if not active:
#         return None
#     scale = height / probe_h
#     return int((active[0] + 2) * scale), int((active[-1] + 2) * scale)
#
# def _crop_box(image: Image.Image, target_size: tuple[int, int]) -> tuple[int, int, int, int]:
#     """Crop window matching the target aspect ratio.
#
#     Wider sources crop left/right about the centre. Taller sources (a 2:3
#     hero going to 1:1) pick the vertical offset that keeps the whole product
#     in frame *and* keeps headroom above it for the headline -- a plain centre
#     crop throws away most of the negative space the hero prompt reserved at
#     the top and parks the headline on the product.
#     """
#     target_w, target_h = target_size
#     target_ratio = target_w / target_h
#     src_w, src_h = image.size
#
#     if src_w / src_h > target_ratio:
#         new_w = int(src_h * target_ratio)
#         left = (src_w - new_w) // 2
#         return (left, 0, left + new_w, src_h)
#
#     crop_h = int(src_w / target_ratio)
#     slack = src_h - crop_h
#     top = slack // 2
#     subject = _subject_rows(image) if slack > 0 else None
#     if subject is not None:
#         subject_top, subject_bottom = subject
#         lowest = subject_bottom + _CROP_FOOTROOM * crop_h - crop_h  # keep the base in frame
#         highest = subject_top - _CROP_HEADROOM * crop_h  # keep headroom above
#         if lowest <= highest:
#             top = int(min(max(top, lowest), highest))
#         else:  # cannot have both: split the difference
#             top = int((lowest + highest) / 2)
#         top = min(max(top, 0), slack)
#     return (0, top, src_w, top + crop_h)
#
# @dataclass(frozen=True)
# class _Design:
#     framed: Image.Image  # RGB, the reframed hero
#     headline_layer: Image.Image  # RGBA: legibility gradient + eyebrow + headline
#     cta_layer: Image.Image  # RGBA: CTA gradient (if needed) + button
#     placement: Literal["split", "stacked"]
#
# def _gradient(
#     size: tuple[int, int],
#     *,
#     edge: Literal["top", "bottom"],
#     solid_to: int,
#     fade: int,
#     color: RGB,
#     max_alpha: int,
# ) -> Image.Image:
#     """Vertical gradient: `max_alpha` from the `edge` of the frame to the
#     row `solid_to` (i.e. behind the whole text block), then easing to 0 over
#     `fade` px. Soft falloff reads as lighting, not as a pasted box."""
#     _, height = size
#     layer = Image.new("RGBA", size, (*color, 0))
#     fade = max(fade, 1)
#     column = Image.new("L", (1, height), 0)
#     px = column.load()
#     for y in range(height):
#         distance = y - solid_to if edge == "top" else solid_to - y
#         t = 1.0 if distance <= 0 else max(0.0, 1 - distance / fade)
#         px[0, y] = int(max_alpha * (t * t * (3 - 2 * t)))  # smoothstep
#     layer.putalpha(column.resize(size))
#     return layer
#
# def _cta_button(text: str, font_size: int, fill: RGB, scale: int = 2) -> Image.Image:
#     """Pill button with a soft shadow, drawn at 2x and downsampled so the
#     rounded edges are anti-aliased."""
#     label = _WHITE if _contrast(_WHITE, fill) >= _contrast(_INK_DARK, fill) else _INK_DARK
#     big_font = _font(_UI_FONT, font_size * scale)
#     text_w = _text_width(big_font, text)
#     ascent, descent = big_font.getmetrics()
#     pad_x = int(big_font.size * 1.15)
#     pad_y = int(big_font.size * 0.7)
#     btn_w = text_w + 2 * pad_x
#     btn_h = ascent + descent + 2 * pad_y
#     # Room for the blurred shadow on every side, so it is never clipped.
#     shadow_pad = pad_y * 2
#     blur = pad_y // 2
#     offset = pad_y // 3
#
#     canvas = Image.new("RGBA", (btn_w + 2 * shadow_pad, btn_h + 2 * shadow_pad), (0, 0, 0, 0))
#     shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
#     ImageDraw.Draw(shadow).rounded_rectangle(
#         (shadow_pad, shadow_pad + offset, shadow_pad + btn_w, shadow_pad + btn_h + offset),
#         radius=btn_h // 2,
#         fill=(0, 0, 0, 70),
#     )
#     canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(blur)))
#     draw = ImageDraw.Draw(canvas)
#     draw.rounded_rectangle(
#         (shadow_pad, shadow_pad, shadow_pad + btn_w, shadow_pad + btn_h),
#         radius=btn_h // 2,
#         fill=(*fill, 255),
#     )
#     left = big_font.getbbox(text)[0]
#     draw.text(
#         (shadow_pad + pad_x - left, shadow_pad + pad_y),
#         text,
#         font=big_font,
#         fill=(*label, 255),
#     )
#     return canvas.resize((canvas.width // scale, canvas.height // scale), Image.LANCZOS)
#
# def _design(hero_image: bytes, target_size: tuple[int, int], spec: CreativeSpec) -> _Design:
#     layout = _layout_for(target_size)
#     width, height = target_size
#     with Image.open(io.BytesIO(hero_image)) as src:
#         framed = _crop_to_fill(src.convert("RGB"), target_size)
#
#     palette = _palette(spec)
#     safe_top = int(height * layout.safe_top)
#     safe_bottom = height - int(height * layout.safe_bottom)
#     margin_x = int(width * layout.side_margin)
#
#     # --- measure the copy ---------------------------------------------------
#     eyebrow_text = str((spec.product_identity or {}).get("name") or "").strip().upper()
#     eyebrow_font = _font(_UI_FONT, int(width * layout.eyebrow_size))
#     eyebrow_tracking = eyebrow_font.size * 0.22
#     eyebrow_h = int(eyebrow_font.size * 1.9) if eyebrow_text else 0
#     # Where the product starts, so a top headline can be sized to sit above it.
#     subject = _subject_rows(framed)
#     room_above_subject = (
#         subject[0] - safe_top - eyebrow_h - int(height * 0.025) if subject is not None else None
#     )
#     headline = _fit_headline(spec.hook.strip(), layout, room_above_subject)
#     if room_above_subject is not None and headline.height > room_above_subject:
#         # No size fits above the product (e.g. a legacy square hero with no
#         # headroom). Shrinking further only makes it small *and* overlapping,
#         # so keep the normal size and let the legibility treatment carry it.
#         headline = _fit_headline(spec.hook.strip(), layout)
#     headline_block_h = eyebrow_h + headline.height
#
#     cta_size = int(width * layout.cta_size)
#     cta_probe = _cta_button(spec.cta.strip(), cta_size, _INK_DARK)
#     gap = int(height * 0.03)
#
#     # --- decide placement from the image itself ------------------------------
#     top_zone = (margin_x, safe_top, width - margin_x, min(safe_top + headline_block_h, height))
#     bottom_block_h = headline_block_h + gap + cta_probe.height
#     bottom_zone = (margin_x, max(safe_bottom - bottom_block_h, 0), width - margin_x, safe_bottom)
#     _, top_busy = _region_stats(framed, top_zone)
#     _, bottom_busy = _region_stats(framed, bottom_zone)
#     # The hero prompt reserves the top for the headline, so "split" is the
#     # default; only move everything down when the top is clearly busier.
#     placement: Literal["split", "stacked"] = (
#         "stacked" if top_busy > max(bottom_busy * 1.35, _CALM_BUSYNESS) else "split"
#     )
#
#     if placement == "split":
#         headline_top = safe_top
#         cta_top = safe_bottom - cta_probe.height
#     else:
#         cta_top = safe_bottom - cta_probe.height
#         headline_top = cta_top - gap - headline_block_h
#
#     headline_box = (margin_x, headline_top, width - margin_x, headline_top + headline_block_h)
#     bg_mean, busy = _region_stats(framed, headline_box)
#     ink, scrim_color, scrim_alpha = _treatment(bg_mean, busy, palette)
#
#     # --- headline layer -------------------------------------------------------
#     headline_layer = Image.new("RGBA", target_size, (0, 0, 0, 0))
#     fade = int(height * 0.12)
#     if placement == "split":
#         headline_layer.alpha_composite(
#             _gradient(target_size, edge="top", solid_to=headline_box[3], fade=fade,
#                       color=scrim_color, max_alpha=scrim_alpha)
#         )
#     else:
#         headline_layer.alpha_composite(
#             _gradient(target_size, edge="bottom", solid_to=headline_box[1], fade=fade,
#                       color=scrim_color, max_alpha=scrim_alpha)
#         )
#
#     text_layer = Image.new("RGBA", target_size, (0, 0, 0, 0))
#     draw = ImageDraw.Draw(text_layer)
#     y = headline_top
#     if eyebrow_text:
#         ew = _tracked_width(eyebrow_font, eyebrow_text, eyebrow_tracking)
#         _draw_tracked(draw, ((width - ew) // 2, y), eyebrow_text, eyebrow_font,
#                       (*ink, 215), eyebrow_tracking)
#         y += eyebrow_h
#     for line in headline.lines:
#         left, top, right, _ = headline.font.getbbox(line)
#         x = (width - (right - left)) // 2 - left
#         draw.text((x, y - top // 2), line, font=headline.font, fill=(*ink, 255))
#         y += headline.line_height
#
#     if ink == _WHITE:
#         # Soft shadow lifts white type off the photo without a hard stroke.
#         shadow = Image.new("RGBA", target_size, (0, 0, 0, 0))
#         shadow.putalpha(text_layer.getchannel("A").point(lambda a: int(a * 0.45)))
#         headline_layer.alpha_composite(
#             shadow.filter(ImageFilter.GaussianBlur(max(3, width // 240)))
#         )
#     headline_layer.alpha_composite(text_layer)
#
#     # --- CTA layer -----------------------------------------------------------
#     cta_layer = Image.new("RGBA", target_size, (0, 0, 0, 0))
#     cta_zone = (margin_x, cta_top, width - margin_x, cta_top + cta_probe.height)
#     cta_bg, cta_busy = _region_stats(framed, cta_zone)
#     if placement == "split":
#         cta_ink, cta_scrim, cta_alpha = _treatment(cta_bg, cta_busy, palette)
#         # The button carries its own fill, so the gradient only needs to calm
#         # a busy area around it -- never more than the headline's treatment.
#         if cta_busy > _CALM_BUSYNESS:
#             cta_layer.alpha_composite(
#                 _gradient(target_size, edge="bottom", solid_to=cta_top, fade=fade,
#                           color=cta_scrim, max_alpha=min(cta_alpha, 140))
#             )
#     else:
#         cta_ink = ink
#     accent = _pick_accent(palette, cta_bg, cta_ink)
#     button = _cta_button(spec.cta.strip(), cta_size, accent)
#     cta_layer.alpha_composite(button, ((width - button.width) // 2, cta_top))
#
#     return _Design(framed=framed, headline_layer=headline_layer, cta_layer=cta_layer, placement=placement)
