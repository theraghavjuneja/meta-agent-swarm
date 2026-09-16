"""Fixture VideoRenderPort adapter.

Returns a small, clearly-labeled bundled sample MP4, with a forced-failure-
once mode, mirroring Module 4's fixture adapter pattern.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Union

from app.assets.ports import GeneratedVideo, VideoRenderSpec
from app.common.exceptions import InfrastructureError
from app.common.logging import get_logger

logger = get_logger(__name__)

_FIXTURE_DIR = Path(__file__).parent / "fixtures"
_FIXTURE_VIDEO_PATH = _FIXTURE_DIR / "sample_video.mp4"


def _ensure_fixture_video(width: int, height: int, duration: float) -> Path:
    """Lazily renders (and caches) a tiny, clearly-labeled placeholder MP4
    via ffmpeg so no binary asset needs to ship in source control."""
    if _FIXTURE_VIDEO_PATH.exists():
        return _FIXTURE_VIDEO_PATH

    _FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    command = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c=0x282c44:s={width}x{height}:d={duration}",
        "-vf",
        "drawtext=text='FIXTURE VIDEO':fontcolor=white:fontsize=48:x=(w-text_w)/2:y=(h-text_h)/2",
        "-pix_fmt",
        "yuv420p",
        str(_FIXTURE_VIDEO_PATH),
    ]
    try:
        subprocess.run(command, check=True, capture_output=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise InfrastructureError(f"Fixture video adapter: failed to render placeholder MP4: {exc}") from exc
    return _FIXTURE_VIDEO_PATH


class FixtureVideoRenderAdapter:
    """Fixture VideoRenderPort implementation.

    `force_fail_once=True` makes exactly one call to `render` raise
    `InfrastructureError` before subsequent calls succeed -- used to
    exercise the retry / attempt-recording path deterministically.
    """

    def __init__(self, force_fail_once: bool = False) -> None:
        self._pending_failure = force_fail_once

    async def render(
        self, hero_image: Union[bytes, Path], spec: VideoRenderSpec, idempotency_key: str
    ) -> GeneratedVideo:
        if self._pending_failure:
            self._pending_failure = False
            logger.info("fixture_video_forced_failure", idempotency_key=idempotency_key)
            raise InfrastructureError("Fixture video adapter: forced failure before success")

        path = _ensure_fixture_video(spec.target_width, spec.target_height, spec.target_duration_seconds)
        logger.info("fixture_video_generated", idempotency_key=idempotency_key)
        return GeneratedVideo(
            path=path,
            duration_seconds=spec.target_duration_seconds,
            width=spec.target_width,
            height=spec.target_height,
            provider_request_id=f"fixture-video-{idempotency_key}",
        )
