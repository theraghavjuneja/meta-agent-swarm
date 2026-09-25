"""Problem 3: distortion-free motion and a Meta-ready encode."""
import asyncio
import re
import shutil
import subprocess

import pytest
from PIL import Image, ImageDraw

from app.assets.adapters import video_ffmpeg
from app.assets.compositing import render_video_layers
from app.assets.ports import VideoRenderSpec


def test_zoom_preserves_size_and_aspect():
    # A circle must stay a circle: zoompan squashed square heroes into 9:16.
    src = Image.new("L", (1080, 1920), 0)
    ImageDraw.Draw(src).ellipse((440, 860, 640, 1060), fill=255)
    out = video_ffmpeg._zoomed(src, 1.2, (0.5, 0.5))
    assert out.size == (1080, 1920)
    left, top, right, bottom = out.point(lambda v: 255 if v > 128 else 0).getbbox()
    assert abs((right - left) - (bottom - top)) <= 2
    assert abs((right - left) - 240) <= 3  # 200 px * 1.2


def test_zoom_window_is_clamped_inside_the_source():
    src = Image.new("RGB", (100, 200), (255, 0, 0))
    out = video_ffmpeg._zoomed(src, 1.5, (0.0, 0.0))  # centre pushed off-frame
    # No black edge pulled in from outside the image.
    assert out.getpixel((0, 0)) == (255, 0, 0)


def test_timeline_frames_have_target_size(spec, hero):
    layers = render_video_layers(hero, spec, (540, 960))
    render_spec = VideoRenderSpec(
        headline_text=spec.hook, cta_text=spec.cta, target_width=540, target_height=960,
        target_duration_seconds=8.0, background_frame=layers.background_frame,
        headline_layer=layers.headline_layer, end_card_frame=layers.end_card_frame,
        cta_layer=layers.cta_layer,
    )
    loaded = video_ffmpeg._load_layers(hero, render_spec)
    for t in (0.0, 1.0, 3.36, 4.5, 5.6, 7.99):
        assert video_ffmpeg._frame_at(loaded, t, 8.0).size == (540, 960)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_render_produces_h264_faststart_with_audio(spec, hero, tmp_path):
    layers = render_video_layers(hero, spec, (540, 960))
    render_spec = VideoRenderSpec(
        headline_text=spec.hook, cta_text=spec.cta, target_width=540, target_height=960,
        target_duration_seconds=2.0, background_frame=layers.background_frame,
        headline_layer=layers.headline_layer, end_card_frame=layers.end_card_frame,
        cta_layer=layers.cta_layer,
    )
    video = asyncio.run(video_ffmpeg.FfmpegVideoRenderAdapter().render(hero, render_spec, "test"))
    path = tmp_path / "v.mp4"
    path.write_bytes(video.read_bytes())

    probe = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path)], capture_output=True, text=True).stderr
    assert "Video: h264 (High)" in probe
    assert "540x960" in probe
    assert "Audio: aac" in probe
    duration = re.search(r"Duration: 00:00:(\d+\.\d+)", probe)
    assert duration and abs(float(duration.group(1)) - 2.0) < 0.1
    # +faststart: the moov atom precedes mdat.
    data = path.read_bytes()
    assert data.index(b"moov") < data.index(b"mdat")


def test_headline_with_apostrophe_needs_no_escaping(hero):
    # drawtext broke on "You're"; text is now pre-rendered by Pillow.
    from tests.conftest import make_spec

    layers = render_video_layers(hero, make_spec(hook="You're one scoop away."), (540, 960))
    assert layers.headline_layer
