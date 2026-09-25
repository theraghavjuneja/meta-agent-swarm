"""Real VideoRenderPort adapter: a three-scene vertical ad, encoded by FFmpeg.

Timeline (for the default 8 s; every boundary scales with the duration):

    0.0s ─ hook ─────────────┐  slow push-in on the hero; eyebrow + headline
                             │  rise and fade in at 0.25 s (readable by ~1 s,
    3.4s ─ product ──────────┤  i.e. inside the first-seconds hook window)
                             │  crossfade to a tighter, drifting product shot
    5.6s ─ end card ─────────┤  crossfade to the 9:16 ad design; the CTA pill
                             │  pops in and holds for >= 2 s
    8.0s ────────────────────┘

Why frames are rendered in Python instead of an FFmpeg filter graph
--------------------------------------------------------------------
The previous pipeline (`zoompan` + `drawtext`) had several defects that no
amount of parameter tuning fixes:

* `zoompan` resamples its window straight to `s=WxH`, so a square hero was
  squashed into 9:16 -- visibly distorted products. Its x/y defaulted to 0,
  so it zoomed into the top-left corner, and it snaps to whole pixels,
  which is the well-known zoompan jitter (the `scale=8000` "fix" made every
  frame an 8000 px image and the render slow).
* `drawtext` rendered the headline as one unwrapped line -- any real hook
  ran off both edges of a 1080 px frame -- and apostrophes broke the filter
  escaping ("You're" -> ffmpeg error).
* One static shot, a hard cut to a black card with plain text, no audio
  track, and no `+faststart`.

Here every frame is an affine transform with sub-pixel precision (smooth,
distortion-free motion), and all type is the pre-rendered layers from
compositing.py (identical to the still ads). FFmpeg only encodes: H.264
High, yuv420p, BT.709, 30 fps, `+faststart`, plus a silent AAC track --
the delivery profile Meta recommends for Feed/Stories/Reels.
"""
from __future__ import annotations

import asyncio
import io
import os
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from app.assets.ports import GeneratedVideo, VideoRenderSpec
from app.common.exceptions import InfrastructureError
from app.common.logging import get_logger

logger = get_logger(__name__)

_FPS = 30
_CROSSFADE_SECONDS = 0.35

# Scene boundaries as fractions of the total duration.
_HOOK_END = 0.42
_PRODUCT_END = 0.70

# Hook: gentle push-in. Product: tighter, drifting slightly downward onto the
# product (the hero prompt places it in the middle band). End card: settles.
_HOOK_ZOOM = (1.00, 1.07)
_PRODUCT_ZOOM = (1.14, 1.22)
_PRODUCT_CENTER = ((0.5, 0.50), (0.5, 0.54))
_END_ZOOM = (1.05, 1.00)

_HEADLINE_IN_AT = 0.25
_HEADLINE_IN_SECONDS = 0.6
_CTA_IN_DELAY = 0.35
_CTA_IN_SECONDS = 0.45

# Pillow's transform/composite release the GIL, so frames render in parallel
# threads. Frames are produced in bounded batches (in order) so at most
# `_FRAME_BATCH` decoded 1080x1920 frames are held in memory at once.
_RENDER_THREADS = max(1, min(4, os.cpu_count() or 1))
_FRAME_BATCH = _RENDER_THREADS * 4


# --------------------------------------------------------------------------- #
# Easing / motion helpers
# --------------------------------------------------------------------------- #


def _clamp01(t: float) -> float:
    return 0.0 if t < 0 else 1.0 if t > 1 else t


def _ease_in_out(t: float) -> float:
    t = _clamp01(t)
    return t * t * (3 - 2 * t)


def _ease_out_cubic(t: float) -> float:
    t = _clamp01(t)
    return 1 - (1 - t) ** 3


def _ease_out_back(t: float) -> float:
    t = _clamp01(t)
    c1 = 1.4
    return 1 + (c1 + 1) * (t - 1) ** 3 + c1 * (t - 1) ** 2


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _zoomed(image: Image.Image, zoom: float, center: tuple[float, float]) -> Image.Image:
    """Crop-zoom `image` about a normalised `center`, with sub-pixel precision.

    The window is clamped inside the source, so the frame never shows an
    edge, and aspect ratio is preserved by construction (same scale on both
    axes) -- the two things zoompan got wrong.
    """
    width, height = image.size
    zoom = max(zoom, 1.0)
    half_w, half_h = width / (2 * zoom), height / (2 * zoom)
    cx = min(max(center[0] * width, half_w), width - half_w)
    cy = min(max(center[1] * height, half_h), height - half_h)
    inv = 1 / zoom
    return image.transform(
        (width, height),
        Image.AFFINE,
        (inv, 0, cx - half_w, 0, inv, cy - half_h),
        resample=Image.BICUBIC,
    )


def _with_opacity(layer: Image.Image, opacity: float) -> Image.Image:
    if opacity >= 1:
        return layer
    faded = layer.copy()
    faded.putalpha(layer.getchannel("A").point(lambda a: int(a * opacity)))
    return faded


def _scaled_about_center(layer: Image.Image, scale: float, anchor_y: int) -> Image.Image:
    """Scale a full-frame RGBA layer about (frame centre x, anchor_y)."""
    if abs(scale - 1) < 1e-3:
        return layer
    width, height = layer.size
    inv = 1 / scale
    cx = width / 2
    return layer.transform(
        (width, height),
        Image.AFFINE,
        (inv, 0, cx - cx * inv, 0, inv, anchor_y - anchor_y * inv),
        resample=Image.BICUBIC,
    )


def _alpha_bbox_center_y(layer: Image.Image) -> int:
    bbox = layer.getchannel("A").getbbox()
    return (bbox[1] + bbox[3]) // 2 if bbox else layer.height // 2


# --------------------------------------------------------------------------- #
# Scene timeline
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _Layers:
    background: Image.Image  # RGB
    headline: Image.Image | None  # RGBA
    end_card: Image.Image  # RGB
    cta: Image.Image | None  # RGBA
    cta_anchor_y: int


def _decode(data: bytes, mode: str) -> Image.Image:
    with Image.open(io.BytesIO(data)) as src:
        return src.convert(mode)


def _fit(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Crop-to-fill without distortion (fallback when no layers were given)."""
    target_w, target_h = size
    scale = max(target_w / image.width, target_h / image.height)
    resized = image.resize((round(image.width * scale), round(image.height * scale)), Image.LANCZOS)
    left, top = (resized.width - target_w) // 2, (resized.height - target_h) // 2
    return resized.crop((left, top, left + target_w, top + target_h))


def _load_layers(hero_image: bytes | Path, spec: VideoRenderSpec) -> _Layers:
    size = (spec.target_width, spec.target_height)
    if spec.background_frame is not None:
        background = _decode(spec.background_frame, "RGB")
    else:
        hero_bytes = hero_image if isinstance(hero_image, (bytes, bytearray)) else Path(hero_image).read_bytes()
        background = _fit(_decode(bytes(hero_bytes), "RGB"), size)
    if background.size != size:
        background = _fit(background, size)

    headline = _decode(spec.headline_layer, "RGBA") if spec.headline_layer else None
    end_card = _decode(spec.end_card_frame, "RGB") if spec.end_card_frame else background
    cta = _decode(spec.cta_layer, "RGBA") if spec.cta_layer else None
    return _Layers(
        background=background,
        headline=headline,
        end_card=end_card,
        cta=cta,
        cta_anchor_y=_alpha_bbox_center_y(cta) if cta else size[1] // 2,
    )


def _hook_frame(layers: _Layers, t: float, hook_end: float) -> Image.Image:
    zoom = _lerp(*_HOOK_ZOOM, _ease_in_out(t / hook_end))
    frame = _zoomed(layers.background, zoom, (0.5, 0.5)).convert("RGBA")
    if layers.headline is not None:
        progress = _ease_out_cubic((t - _HEADLINE_IN_AT) / _HEADLINE_IN_SECONDS)
        if progress > 0:
            rise = int((1 - progress) * frame.height * 0.02)
            headline = _with_opacity(layers.headline, progress)
            frame.alpha_composite(headline, (0, rise))
    return frame


def _product_frame(layers: _Layers, t: float, start: float, end: float) -> Image.Image:
    p = _ease_in_out((t - start) / max(end - start, 1e-6))
    zoom = _lerp(*_PRODUCT_ZOOM, p)
    (x0, y0), (x1, y1) = _PRODUCT_CENTER
    return _zoomed(layers.background, zoom, (_lerp(x0, x1, p), _lerp(y0, y1, p))).convert("RGBA")


def _end_frame(layers: _Layers, t: float, start: float, duration: float) -> Image.Image:
    p = _ease_out_cubic((t - start) / max(duration - start, 1e-6))
    frame = _zoomed(layers.end_card, _lerp(*_END_ZOOM, p), (0.5, 0.5)).convert("RGBA")
    if layers.cta is not None:
        progress = (t - start - _CTA_IN_DELAY) / _CTA_IN_SECONDS
        if progress > 0:
            scale = _lerp(0.82, 1.0, _ease_out_back(progress))
            cta = _scaled_about_center(layers.cta, scale, layers.cta_anchor_y)
            frame.alpha_composite(_with_opacity(cta, _ease_out_cubic(progress)))
    return frame


def _frame_at(layers: _Layers, t: float, duration: float) -> Image.Image:
    hook_end = duration * _HOOK_END
    product_end = duration * _PRODUCT_END
    half = _CROSSFADE_SECONDS / 2

    if t < hook_end - half:
        return _hook_frame(layers, t, hook_end)
    if t < hook_end + half:
        mix = _ease_in_out((t - (hook_end - half)) / _CROSSFADE_SECONDS)
        return Image.blend(
            _hook_frame(layers, t, hook_end),
            _product_frame(layers, t, hook_end - half, product_end),
            mix,
        )
    if t < product_end - half:
        return _product_frame(layers, t, hook_end - half, product_end)
    if t < product_end + half:
        mix = _ease_in_out((t - (product_end - half)) / _CROSSFADE_SECONDS)
        return Image.blend(
            _product_frame(layers, t, hook_end - half, product_end),
            _end_frame(layers, t, product_end - half, duration),
            mix,
        )
    return _end_frame(layers, t, product_end - half, duration)


# --------------------------------------------------------------------------- #
# Encoding
# --------------------------------------------------------------------------- #


def _build_command(output_path: Path, width: int, height: int, duration: float) -> list[str]:
    return [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel", "error",
        # Raw RGB frames from this process on stdin.
        "-f", "rawvideo",
        "-pix_fmt", "rgb24",
        "-s", f"{width}x{height}",
        "-r", str(_FPS),
        "-i", "pipe:0",
        # Silent stereo track: some placements/players treat a video with no
        # audio stream as broken, and it gives editors a track to replace.
        "-f", "lavfi",
        "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-map", "0:v",
        "-map", "1:a",
        "-vf", "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p",
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "18",
        "-profile:v", "high",
        "-level:v", "4.2",
        "-g", str(_FPS * 2),
        "-colorspace", "bt709",
        "-color_primaries", "bt709",
        "-color_trc", "bt709",
        "-c:a", "aac",
        "-b:a", "128k",
        "-t", f"{duration:.3f}",
        "-shortest",
        # moov atom up front: playback starts before the download finishes.
        "-movflags", "+faststart",
        str(output_path),
    ]


def _render_sync(hero_image: bytes | Path, spec: VideoRenderSpec, output_path: Path) -> None:
    layers = _load_layers(hero_image, spec)
    duration = spec.target_duration_seconds
    total_frames = round(duration * _FPS)
    command = _build_command(output_path, spec.target_width, spec.target_height, duration)

    with tempfile.TemporaryFile() as stderr_file:
        try:
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=stderr_file)
        except FileNotFoundError as exc:
            raise InfrastructureError("ffmpeg is not installed or not on PATH") from exc

        def render_frame(index: int) -> bytes:
            return _frame_at(layers, index / _FPS, duration).convert("RGB").tobytes()

        try:
            assert process.stdin is not None
            with ThreadPoolExecutor(max_workers=_RENDER_THREADS) as pool:
                for start in range(0, total_frames, _FRAME_BATCH):
                    batch = range(start, min(start + _FRAME_BATCH, total_frames))
                    for frame_bytes in pool.map(render_frame, batch):
                        process.stdin.write(frame_bytes)
            process.stdin.close()
            returncode = process.wait()
        except BrokenPipeError:
            returncode = process.wait()
        except BaseException:
            process.kill()
            process.wait()
            raise

        if returncode != 0:
            stderr_file.seek(0)
            stderr = stderr_file.read().decode(errors="replace")
            raise InfrastructureError(f"ffmpeg render failed (exit {returncode}): {stderr[-2000:]}")


class FfmpegVideoRenderAdapter:
    """Real VideoRenderPort implementation: Pillow frames, FFmpeg encode."""

    async def render(
        self, hero_image: bytes | Path, spec: VideoRenderSpec, idempotency_key: str
    ) -> GeneratedVideo:
        with tempfile.TemporaryDirectory(prefix="assets-ffmpeg-") as tmp:
            output_path = Path(tmp) / f"{idempotency_key}.mp4"
            # Frame rendering is CPU-bound; run it off the event loop so the
            # worker keeps serving the concurrent compose activities.
            await asyncio.to_thread(_render_sync, hero_image, spec, output_path)
            data = output_path.read_bytes()

        logger.info(
            "ffmpeg_video_rendered",
            idempotency_key=idempotency_key,
            duration=spec.target_duration_seconds,
            width=spec.target_width,
            height=spec.target_height,
            used_design_layers=spec.headline_layer is not None,
        )
        return GeneratedVideo(
            data=data,
            duration_seconds=spec.target_duration_seconds,
            width=spec.target_width,
            height=spec.target_height,
            provider_request_id=None,
        )
