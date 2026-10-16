"""Queue one ffmpeg workflow on a Comfy client and collect the saved file.

The queue callable is BatchComfyStreamClient.queue_once, running inside the
video-tool ProcessPoolExecutor. Files are staged in the Comfy input folder
so LoadVideo and LoadAudio can see them.
"""

from __future__ import annotations

import uuid
from io import BytesIO
from pathlib import Path
from typing import Any, Callable

from comfystream.ffmpeg.workflows import (
    compose,
    extract_frame,
    images_to_video,
    loudnorm,
    merge_audio_video,
    merge_videos,
    metadata,
    scale_video,
    trim_video,
)

QueuePrompt = Callable[[dict[str, Any]], Any]


class FfmpegJobError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def run_job(
    queue: QueuePrompt,
    workspace: str,
    tool: str,
    files: dict[str, Any],
    params: dict[str, Any],
) -> dict[str, Any]:
    root = Path(workspace)
    input_dir = root / "input" / "ffmpeg"
    input_dir.mkdir(parents=True, exist_ok=True)
    job_id = uuid.uuid4().hex
    prefix = f"ffmpeg/{job_id}"
    staged = _stage(input_dir, job_id, files)
    prompt = _prompt(tool, staged, prefix, params)
    queue(prompt)
    if tool == "metadata":
        return {"media": {"workflow": "GetVideoComponents", "file": staged["media"]}}
    saved = _find_output(root / "output", job_id)
    if saved is None:
        raise FfmpegJobError("workflow did not write an output file", status=500)
    content_type = "image/png" if saved.suffix.lower() == ".png" else "video/mp4"
    if tool == "loudnorm":
        content_type = "audio/flac" if saved.suffix.lower() == ".flac" else "audio/wav"
    return {
        "path": saved,
        "content_type": content_type,
        "bytes": saved.read_bytes(),
    }


def _prompt(
    tool: str, staged: dict[str, Any], prefix: str, params: dict[str, Any]
) -> dict[str, Any]:
    if tool == "scale-video":
        return scale_video(
            staged["video"],
            prefix,
            int(params.get("height") or 0),
            int(params.get("width") or 0),
            str(params.get("mode") or "pad"),
        )
    if tool == "trim-video":
        start = float(params.get("start_time") or 0)
        if params.get("duration") is not None:
            duration = float(params["duration"])
        elif params.get("end_time") is not None:
            duration = max(0.0, float(params["end_time"]) - start)
        else:
            duration = 0.0
        return trim_video(staged["video"], prefix, start, duration)
    if tool == "loudnorm":
        if "volume" in params:
            volume = int(params["volume"])
        else:
            target = float(params.get("integrated_loudness") or -18)
            volume = int(round(target - (-23)))
        return loudnorm(staged["audio"], prefix, volume)
    if tool == "extract-frame":
        index = 0
        if str(params.get("frame_type") or "first") == "last":
            index = -1
        return extract_frame(staged["video"], prefix, index)
    if tool == "metadata":
        return metadata(staged["media"])
    if tool == "merge-audio-video":
        return merge_audio_video(staged["video"], staged["audio"], prefix)
    if tool == "merge-videos":
        return merge_videos(list(staged["videos"]), prefix)
    if tool == "images-to-video":
        return images_to_video(
            list(staged["images"]),
            prefix,
            float(params.get("fps") or 4),
        )
    if tool == "compose":
        return compose(list(staged["sequence"]), prefix, float(params.get("fps") or 4))
    raise FfmpegJobError(f"unknown ffmpeg tool {tool}", status=404)


def _stage(folder: Path, job_id: str, files: dict[str, Any]) -> dict[str, Any]:
    staged: dict[str, Any] = {}
    for key, value in files.items():
        if isinstance(value, (bytes, bytearray)):
            name = f"{job_id}-{key}.bin"
            (folder / name).write_bytes(bytes(value))
            staged[key] = f"ffmpeg/{name}"
        elif isinstance(value, list):
            names = []
            image_dir = folder.parent
            image_dir.mkdir(parents=True, exist_ok=True)
            for index, item in enumerate(value):
                if key == "sequence":
                    kind = item[0]
                    payload = bytes(item[1])
                    if kind == "image":
                        payload = _even_image(payload)
                        name = f"{job_id}-image-{index}{_image_suffix(payload)}"
                        (image_dir / name).write_bytes(payload)
                        names.append(("image", name, int(item[2])))
                    else:
                        name = f"{job_id}-clip-{index}.mp4"
                        (folder / name).write_bytes(payload)
                        names.append(("video", f"ffmpeg/{name}"))
                    continue
                payload, extra = item if isinstance(item, tuple) else (item, 1)
                payload = bytes(payload)
                if key == "images":
                    payload = _even_image(payload)
                    name = f"{job_id}-{key}-{index}{_image_suffix(payload)}"
                    (image_dir / name).write_bytes(payload)
                    names.append((name, extra))
                else:
                    name = f"{job_id}-{key}-{index}.bin"
                    (folder / name).write_bytes(payload)
                    names.append(f"ffmpeg/{name}")
            staged[key] = names
        else:
            raise FfmpegJobError(f"missing bytes for {key}")
    return staged


def _even_image(payload: bytes) -> bytes:
    """Pad an odd width or height by one pixel.

    SaveVideo opens libx264 with yuv420p, and that encoder rejects odd sizes.
    """
    try:
        from PIL import Image
    except ImportError:
        return payload
    try:
        with Image.open(BytesIO(payload)) as image:
            width, height = image.size
            if width % 2 == 0 and height % 2 == 0:
                return payload
            canvas = Image.new(image.mode, (width + (width % 2), height + (height % 2)))
            canvas.paste(image, (0, 0))
            out = BytesIO()
            canvas.save(out, format=image.format or "PNG")
            return out.getvalue()
    except Exception:
        return payload


def _image_suffix(payload: bytes) -> str:
    if payload.startswith(b"\x89PNG"):
        return ".png"
    if payload.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if payload.startswith(b"GIF8"):
        return ".gif"
    if payload.startswith(b"RIFF") and payload[8:12] == b"WEBP":
        return ".webp"
    return ".png"


def _find_output(output_dir: Path, job_id: str) -> Path | None:
    folder = output_dir / "ffmpeg"
    if not folder.is_dir():
        return None
    matches = [path for path in folder.iterdir() if path.is_file() and job_id in path.name]
    if not matches:
        return None
    return max(matches, key=lambda path: path.stat().st_mtime)
