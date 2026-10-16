"""Workflow graphs for the ffmpeg capabilities. No ffmpeg binary and no GPU."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import pytest

from comfystream.ffmpeg.workflows import compose, live_filter, loudnorm, merge_videos, scale_video, trim_video
from comfystream.realtime.spec import load_realtime_config


def test_registration_advertises_request_schema() -> None:
    from comfystream.ffmpeg.schemas import (
        METADATA_LIMIT_BYTES,
        registration_metadata,
        schema_bytes,
        schema_sha256,
    )

    tools = (
        "scale-video",
        "trim-video",
        "loudnorm",
        "extract-frame",
        "metadata",
        "merge-audio-video",
        "merge-videos",
        "images-to-video",
        "compose",
        "publish",
    )
    images = json.loads(registration_metadata("images-to-video"))
    assert images["required"] == ["images"]
    assert images["schema_url"] == "./schema"
    assert "tracks" not in images["required"]
    composed = json.loads(registration_metadata("compose"))
    assert composed["required"] == ["tracks"]
    assert "images" not in composed["required"]
    for tool in tools:
        encoded = registration_metadata(tool)
        assert len(encoded.encode("utf-8")) <= METADATA_LIMIT_BYTES
        meta = json.loads(encoded)
        assert meta["schema_sha256"] == schema_sha256(tool)
        assert hashlib.sha256(schema_bytes(tool)).hexdigest() == meta["schema_sha256"]
        assert meta["required"]


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
    joined = merge_videos(["ffmpeg/a.mp4", "ffmpeg/b.mp4"], "ffmpeg/job")
    assert joined["3"]["inputs"]["videos.video0"] == ["1", 0]
    assert joined["3"]["inputs"]["videos.video1"] == ["2", 0]
    still = compose([("image", "map.png", 8)], "ffmpeg/job", 4)
    assert still["1"]["class_type"] == "LoadImage"
    assert still["1"]["inputs"]["image"] == "map.png"
    assert still["2"]["class_type"] == "RepeatImageBatch"
    assert still["3"]["inputs"]["fps"] == 4


def test_comfy_path_pin_replaces_input_lookup(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("COMFYUI_CWD", str(tmp_path))
    from comfystream_ffmpeg_paths import _patch

    class Names:
        def __init__(self) -> None:
            self.base_paths: list[Path] = []
            self.application_paths = type(
                "ApplicationPaths",
                (),
                {"input_directory": Path("input"), "output_directory": Path("output")},
            )()

    from functools import wraps

    def getter(func):
        @wraps(func)
        def wrapper():
            return func()

        return wrapper

    @getter
    def _base_path() -> Path:
        raise IndexError("list index out of range")

    module = type("folder_paths", (), {})()
    module._comfystream_pinned = False
    module._folder_names_and_paths = Names
    module.get_input_directory = lambda mkdirs=True: (_ for _ in ()).throw(IndexError("list index out of range"))
    module.get_output_directory = lambda: "relative-output"
    module.get_temp_directory = lambda: "relative-temp"
    module._base_path = _base_path
    _patch(module)
    assert module.get_input_directory() == str(tmp_path / "input")
    assert module._base_path() == tmp_path
    assert _base_path() == tmp_path
    assert (tmp_path / "input").is_dir()


def test_odd_images_are_padded_for_h264(tmp_path) -> None:
    pytest.importorskip("PIL")
    from PIL import Image

    from comfystream.ffmpeg.execute import _stage

    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    )
    staged = _stage(
        tmp_path / "input" / "ffmpeg",
        "job",
        {"images": [(png, 1)]},
    )
    name, _frames = staged["images"][0]
    with Image.open(tmp_path / "input" / name) as image:
        assert image.size == (2, 2)


def test_images_are_staged_for_loadimage(tmp_path) -> None:
    from comfystream.ffmpeg.execute import _stage

    staged = _stage(
        tmp_path / "input" / "ffmpeg",
        "job",
        {"images": [(b"\x89PNG\r\n\x1a\nrest", 3)]},
    )
    name, frames = staged["images"][0]
    assert "/" not in name
    assert name.endswith(".png")
    assert frames == 3
    assert (tmp_path / "input" / name).read_bytes().startswith(b"\x89PNG")


def test_ffmpeg_live_spec_loads() -> None:
    specs, capacity = load_realtime_config("configs/realtime-ffmpeg.yaml")
    spec = specs[0]
    assert spec.backend == "ffmpeg_filter"
    assert spec.options["workspace"] == "/workspace/ComfyUI"
    assert "ImageScale" not in spec.metadata()
    assert capacity.max_loaded == 1
