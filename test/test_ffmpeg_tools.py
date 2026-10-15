"""Workflow graphs for the ffmpeg capabilities. No ffmpeg binary and no GPU."""

from __future__ import annotations

from comfystream.ffmpeg.workflows import live_filter, loudnorm, scale_video, trim_video
from comfystream.realtime.spec import load_realtime_config


def test_live_filter_uses_core_image_nodes() -> None:
    plain = live_filter(720, 0, "none")
    assert [node["class_type"] for node in plain.values()] == [
        "LoadTensor",
        "ImageRotate",
        "ImageScale",
        "SaveTensor",
    ]
    assert plain["3"]["inputs"]["height"] == 720
    assert plain["3"]["inputs"]["width"] == 0
    flipped = live_filter(480, 90, "horizontal")
    assert flipped["2"]["inputs"]["rotation"] == "90 degrees"
    assert flipped["4"]["class_type"] == "ImageFlip"
    assert flipped["5"]["inputs"]["images"] == ["4", 0]


def test_batch_graphs_use_core_video_nodes() -> None:
    scaled = scale_video("ffmpeg/in.mp4", "ffmpeg/job", 360, 0, "pad")
    assert scaled["1"]["class_type"] == "LoadVideo"
    assert scaled["3"]["class_type"] == "ImageScale"
    assert scaled["3"]["inputs"]["height"] == 360
    assert scaled["5"]["class_type"] == "SaveVideo"
    trimmed = trim_video("ffmpeg/in.mp4", "ffmpeg/job", 1.5, 4)
    assert trimmed["2"]["class_type"] == "Video Slice"
    assert trimmed["2"]["inputs"]["start_time"] == 1.5
    audio = loudnorm("ffmpeg/in.bin", "ffmpeg/job", 5)
    assert audio["2"]["class_type"] == "AudioAdjustVolume"
    assert audio["3"]["class_type"] == "SaveAudio"


def test_ffmpeg_live_spec_loads() -> None:
    specs, capacity = load_realtime_config("configs/realtime-ffmpeg.yaml")
    spec = specs[0]
    assert spec.backend == "ffmpeg_filter"
    assert spec.options["workspace"] == "/workspace/ComfyUI"
    assert "ImageScale" not in spec.metadata()
    assert capacity.max_loaded == 1
