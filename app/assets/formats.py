"""Deriving ad formats from one finished master ad (AD_TEXT_MODE=model).

In ``model`` mode the image model designs a single 1:1 master ad with the copy
already rendered. Every other format is derived from that master instead of being
designed again, because each extra design pass is another chance for the model to
misspell the headline or restyle the product.

Three ways to get from a square master to 9:16 / 4:5 were weighed:

* **Smart crop** -- free, but a square has no pixels to spare for a taller frame:
  cropping the master's width to 9:16 cuts straight through the headline and CTA.
  Usable only for formats *wider* than the master would be, and none are.
* **Re-compose with the model, master as reference** -- the model redraws the whole
  ad, so all copy is re-rendered (new spelling risk, new layout, a different-looking
  product). Consistency across formats is lost, which is the point of a master.
* **Outpaint (chosen)** -- the master is placed, scaled down, in the middle of a
  taller canvas and the model fills only the empty bands with more of the same
  scene. The copy is never re-rendered: after the model returns, the master's own
  pixels are pasted back over the centre, so even a model that "touches up" the
  masked-in region cannot change a letter. The model only paints background.

Where text can still be cut off or distorted (also in the module's tests):

* The master is scaled down (9:16: to 84% of the frame width), so its type is
  ~16% smaller than in the 1:1 ad; very small copy (badges) loses legibility first.
* The 84% width is deliberate: the video's product push-in zooms up to 1.22x
  (crops ~9% per side). A full-width master would lose the left/right edges of the
  headline during that scene; at 84% the master's own ~7% text margin survives.
* The outpainted bands are new pixels: the model may add props or *text-like*
  marks there despite the prompt. They contain no copy by design, so this is a
  visual-quality risk, not a spelling risk.
* A seam can show where the pasted-back master meets the outpainted band; the paste
  uses a feathered edge to hide it.
"""
from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFilter

__all__ = ["OutpaintPlan", "finish_outpaint", "plan_outpaint", "prepare_outpaint", "square_from_master"]

# Canvas sizes the OpenAI image models accept for edits.
_PORTRAIT_CANVAS = (1024, 1536)
_LANDSCAPE_CANVAS = (1536, 1024)
# Master width as a fraction of the derived frame's width (see module docstring).
_MASTER_WIDTH_FRACTION = 0.84
# ...and at most this share of its height, so taller formats keep real space above
# and below the master for the outpainted scene (and Meta's Stories safe zones).
_MASTER_HEIGHT_FRACTION = 0.62
_FEATHER_FRACTION = 0.02


@dataclass(frozen=True)
class OutpaintPlan:
    canvas_size: tuple[int, int]
    master_box: tuple[int, int, int, int]  # where the master sits on the canvas
    crop_box: tuple[int, int, int, int]  # the target-aspect window cut from the canvas


def plan_outpaint(target_size: tuple[int, int]) -> OutpaintPlan:
    """Canvas, master placement and final crop for a target format."""
    tw, th = target_size
    target_ratio = tw / th
    canvas = _PORTRAIT_CANVAS if target_ratio < 1 else _LANDSCAPE_CANVAS
    cw, ch = canvas
    # Largest window of the target aspect, centred on the canvas.
    if cw / ch > target_ratio:
        crop_w, crop_h = round(ch * target_ratio), ch
    else:
        crop_w, crop_h = cw, round(cw / target_ratio)
    crop_l, crop_t = (cw - crop_w) // 2, (ch - crop_h) // 2
    side = int(min(crop_w * _MASTER_WIDTH_FRACTION, crop_h * _MASTER_HEIGHT_FRACTION))
    left = crop_l + (crop_w - side) // 2
    top = crop_t + (crop_h - side) // 2
    return OutpaintPlan(
        canvas_size=canvas,
        master_box=(left, top, left + side, top + side),
        crop_box=(crop_l, crop_t, crop_l + crop_w, crop_t + crop_h),
    )


def _master(master_png: bytes, side: int) -> Image.Image:
    with Image.open(io.BytesIO(master_png)) as img:
        return img.convert("RGB").resize((side, side), Image.LANCZOS)


def prepare_outpaint(master_png: bytes, plan: OutpaintPlan) -> tuple[bytes, bytes]:
    """(image, mask) PNGs for an images.edit outpaint call: the master on a
    transparent canvas, and a mask whose transparent pixels are the bands to fill."""
    left, top, right, bottom = plan.master_box
    canvas = Image.new("RGBA", plan.canvas_size, (0, 0, 0, 0))
    canvas.paste(_master(master_png, right - left).convert("RGBA"), (left, top))
    mask = Image.new("RGBA", plan.canvas_size, (0, 0, 0, 0))
    ImageDraw.Draw(mask).rectangle((left, top, right - 1, bottom - 1), fill=(0, 0, 0, 255))
    return _png(canvas), _png(mask)


def finish_outpaint(
    generated: bytes, master_png: bytes, plan: OutpaintPlan, target_size: tuple[int, int]
) -> bytes:
    """Paste the untouched master back over the model's canvas (feathered edge), cut
    the target window and resize to the exact output size. Returns JPEG bytes."""
    with Image.open(io.BytesIO(generated)) as img:
        canvas = img.convert("RGB").resize(plan.canvas_size, Image.LANCZOS)
    left, top, right, bottom = plan.master_box
    side = right - left
    feather = max(2, int(side * _FEATHER_FRACTION))
    alpha = Image.new("L", (side, side), 0)
    ImageDraw.Draw(alpha).rectangle((feather, feather, side - 1 - feather, side - 1 - feather), fill=255)
    alpha = alpha.filter(ImageFilter.GaussianBlur(feather / 2))
    canvas.paste(_master(master_png, side), (left, top), alpha)
    out = canvas.crop(plan.crop_box).resize(target_size, Image.LANCZOS)
    return _jpeg(out)


def square_from_master(master_png: bytes, target_size: tuple[int, int]) -> bytes:
    """The 1:1 ad is the master itself, resized -- nothing is cropped."""
    with Image.open(io.BytesIO(master_png)) as img:
        return _jpeg(img.convert("RGB").resize(target_size, Image.LANCZOS))


def _png(image: Image.Image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def _jpeg(image: Image.Image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=95, subsampling=0, optimize=True)
    return buf.getvalue()
