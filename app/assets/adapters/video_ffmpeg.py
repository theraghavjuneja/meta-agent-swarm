"""Real VideoRenderPort adapter: an FFmpeg pipeline that animates the hero
image with a Ken Burns pan/zoom, reveals the headline text, and ends on a
CTA end frame.

The actual FFmpeg command construction is isolated to `_build_filter_graph`
and `_build_command` below -- this is the one place in the module that
shells out to ffmpeg, and the one place that would need to change to alter
the pan/zoom/reveal recipe.
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from app.assets.ports import GeneratedVideo, VideoRenderSpec
from app.common.exceptions import InfrastructureError
from app.common.logging import get_logger

logger = get_logger(__name__)

_CTA_HOLD_SECONDS = 1.5
_FPS = 30
_HEADLINE_FADE_IN_SECONDS = 1.5


def _escape_drawtext(text: str) -> str:
    """Escapes characters that are meaningful inside an ffmpeg
    drawtext/filter expression."""
    return text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def _build_filter_graph(spec: VideoRenderSpec, pan_zoom_duration: float) -> str:
    """Ken Burns pan/zoom on the hero image, with the headline text fading
    in over the first `_HEADLINE_FADE_IN_SECONDS` and held for the rest of
    the pan/zoom segment."""
    width, height = spec.target_width, spec.target_height
    headline = _escape_drawtext(spec.headline_text)
    zoom_frames = max(int(pan_zoom_duration * _FPS), 1)

    zoompan = (
        f"scale=8000:-1,"
        f"zoompan=z='min(zoom+0.0008,1.15)':d={zoom_frames}:s={width}x{height}:fps={_FPS}"
    )
    headline_reveal = (
        f"drawtext=text='{headline}':fontcolor=white:fontsize={int(height * 0.06)}:"
        f"borderw=3:bordercolor=black@0.7:x=(w-text_w)/2:y=h*0.08:"
        f"alpha='if(lt(t,{_HEADLINE_FADE_IN_SECONDS}),t/{_HEADLINE_FADE_IN_SECONDS},1)'"
    )
    return f"{zoompan},{headline_reveal}"


def _build_command(hero_path: Path, output_path: Path, spec: VideoRenderSpec, pan_zoom_duration: float) -> list[str]:
    width, height = spec.target_width, spec.target_height
    cta = _escape_drawtext(spec.cta_text)
    filter_graph = _build_filter_graph(spec, pan_zoom_duration)

    # CTA end frame: a plain color card with the CTA text, concatenated
    # after the pan/zoom segment.
    cta_filter = (
        f"color=c=black:s={width}x{height}:d={_CTA_HOLD_SECONDS},"
        f"drawtext=text='{cta}':fontcolor=white:fontsize={int(height * 0.05)}:"
        f"x=(w-text_w)/2:y=(h-text_h)/2"
    )

    return [
        "ffmpeg",
        "-y",
        "-loop",
        "1",
        "-i",
        str(hero_path),
        "-f",
        "lavfi",
        "-i",
        cta_filter,
        "-filter_complex",
        f"[0:v]{filter_graph}[panzoom];[panzoom][1:v]concat=n=2:v=1:a=0[outv]",
        "-map",
        "[outv]",
        "-pix_fmt",
        "yuv420p",
        "-t",
        str(pan_zoom_duration + _CTA_HOLD_SECONDS),
        str(output_path),
    ]


class FfmpegVideoRenderAdapter:
    """Real VideoRenderPort implementation shelling out to ffmpeg."""

    async def render(
        self, hero_image: bytes | Path, spec: VideoRenderSpec, idempotency_key: str
    ) -> GeneratedVideo:
        pan_zoom_duration = max(spec.target_duration_seconds - _CTA_HOLD_SECONDS, 1.0)

        with tempfile.TemporaryDirectory(prefix="assets-ffmpeg-") as tmp:
            tmp_dir = Path(tmp)
            if isinstance(hero_image, (bytes, bytearray)):
                hero_path = tmp_dir / "hero.jpg"
                hero_path.write_bytes(hero_image)
            else:
                hero_path = Path(hero_image)

            output_path = tmp_dir / f"{idempotency_key}.mp4"
            command = _build_command(hero_path, output_path, spec, pan_zoom_duration)

            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await process.communicate()
            if process.returncode != 0:
                raise InfrastructureError(
                    f"ffmpeg render failed (exit {process.returncode}): "
                    f"{stderr.decode(errors='replace')[-2000:]}"
                )

            data = output_path.read_bytes()

        logger.info("ffmpeg_video_rendered", idempotency_key=idempotency_key, duration=spec.target_duration_seconds)
        return GeneratedVideo(
            data=data,
            duration_seconds=spec.target_duration_seconds,
            width=spec.target_width,
            height=spec.target_height,
            provider_request_id=None,
        )