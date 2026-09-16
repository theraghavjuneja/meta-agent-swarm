"""Deterministic, non-AI compositing: crop/recompose + text overlay.

Pure functions -- bytes/data in, bytes out. No provider calls and no I/O
beyond image processing, so these stay trivially reusable from an activity
without needing any port. This is what turns one hero-image generation into
two coordinated ad formats, rather than two independent generations.
"""
from __future__ import annotations

import io
from typing import TYPE_CHECKING

from PIL import Image, ImageDraw, ImageFont

if TYPE_CHECKING:
    from app.creative.models import CreativeSpec

_1X1_SIZE = (1080, 1080)
_9X16_SIZE = (1080, 1920)


def _crop_to_fill(image: Image.Image, target_size: tuple[int, int]) -> Image.Image:
    """Center-crop-then-resize so the hero image exactly fills target_size
    without distortion: crop first to match the target aspect ratio, then
    resize to the exact target pixel dimensions."""
    target_w, target_h = target_size
    target_ratio = target_w / target_h
    src_w, src_h = image.size
    src_ratio = src_w / src_h

    if src_ratio > target_ratio:
        # Source is relatively wider than target -> crop the left/right.
        new_w = int(src_h * target_ratio)
        left = (src_w - new_w) // 2
        box = (left, 0, left + new_w, src_h)
    else:
        # Source is relatively taller than target -> crop the top/bottom.
        new_h = int(src_w / target_ratio)
        top = (src_h - new_h) // 2
        box = (0, top, src_w, top + new_h)

    return image.crop(box).resize(target_size, Image.LANCZOS)


def _load_font(size: int):
    try:
        return ImageFont.truetype("DejaVuSans-Bold.ttf", size)
    except OSError:
        return ImageFont.load_default()


def _draw_wrapped_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    *,
    center_x: int,
    top_y: int,
    max_width: int,
    font,
    fill: tuple[int, int, int, int],
    stroke_fill: tuple[int, int, int, int] = (0, 0, 0, 255),
    stroke_width: int = 3,
    line_spacing: int = 8,
) -> int:
    """Word-wraps `text` to `max_width`, draws it centered on `center_x`
    starting at `top_y`, and returns the y-coordinate just below the last
    drawn line."""
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        bbox = draw.textbbox((0, 0), candidate, font=font, stroke_width=stroke_width)
        if bbox[2] - bbox[0] <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)

    y = top_y
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font, stroke_width=stroke_width)
        line_w = bbox[2] - bbox[0]
        line_h = bbox[3] - bbox[1]
        x = center_x - line_w // 2
        draw.text((x, y), line, font=font, fill=fill, stroke_width=stroke_width, stroke_fill=stroke_fill)
        y += line_h + line_spacing
    return y


def _composite(hero_image: bytes, target_size: tuple[int, int], spec: CreativeSpec) -> bytes:
    with Image.open(io.BytesIO(hero_image)) as src:
        base = _crop_to_fill(src.convert("RGB"), target_size).convert("RGBA")

    draw = ImageDraw.Draw(base)
    width, height = target_size

    guidance = spec.composition_guidance or {}
    hook_font_size = int(guidance.get("hook_font_size", width * 0.07))
    cta_font_size = int(guidance.get("cta_font_size", width * 0.045))
    margin = int(width * 0.08)

    # Hook text near the top third.
    _draw_wrapped_text(
        draw,
        spec.hook,
        center_x=width // 2,
        top_y=int(height * 0.06),
        max_width=width - 2 * margin,
        font=_load_font(hook_font_size),
        fill=(255, 255, 255, 255),
    )

    # CTA pinned near the bottom, over a subtle scrim for legibility.
    scrim_height = int(height * 0.16)
    scrim = Image.new("RGBA", (width, scrim_height), (0, 0, 0, 140))
    base.alpha_composite(scrim, (0, height - scrim_height))

    _draw_wrapped_text(
        draw,
        spec.cta,
        center_x=width // 2,
        top_y=height - scrim_height + int(scrim_height * 0.25),
        max_width=width - 2 * margin,
        font=_load_font(cta_font_size),
        fill=(255, 255, 255, 255),
    )

    out = io.BytesIO()
    base.convert("RGB").save(out, format="JPEG", quality=92)
    return out.getvalue()


def compose_1x1(hero_image: bytes, spec: CreativeSpec) -> bytes:
    """Crop/recompose the hero image to exactly 1080x1080 and overlay
    hook/cta text per composition_guidance."""
    return _composite(hero_image, _1X1_SIZE, spec)


def compose_9x16(hero_image: bytes, spec: CreativeSpec) -> bytes:
    """Crop/recompose the hero image to exactly 1080x1920 and overlay
    hook/cta text per composition_guidance."""
    return _composite(hero_image, _9X16_SIZE, spec)
