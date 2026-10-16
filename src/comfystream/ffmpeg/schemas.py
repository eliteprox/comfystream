"""Request schemas advertised with each ffmpeg capability registration.

Orchestrator metadata is limited to 1024 bytes, so registration carries the
required field names, a sha256, and ``schema_url``. Callers resolve that URL
against the runner and use the document to build a valid body.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

METADATA_LIMIT_BYTES = 1024
SCHEMA_URL = "./schema"
FORMAT = "comfystream.ffmpeg.request.v1"

_URL = {"type": "string", "minLength": 1}


def _document(tool: str, required: list[str], properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "additionalProperties": True,
        "format": FORMAT,
        "properties": properties,
        "required": required,
        "tool": tool,
        "type": "object",
    }


def _image_item() -> dict[str, Any]:
    return {
        "oneOf": [
            _URL,
            {
                "additionalProperties": True,
                "properties": {
                    "frames": {"default": 1, "minimum": 1, "type": "integer"},
                    "url": _URL,
                },
                "required": ["url"],
                "type": "object",
            },
        ]
    }


def _keyframe() -> dict[str, Any]:
    return {
        "additionalProperties": True,
        "properties": {
            "duration": {
                "description": "Length of this keyframe in milliseconds.",
                "minimum": 0,
                "type": "number",
            },
            "timestamp": {"minimum": 0, "type": "number"},
            "url": _URL,
        },
        "required": ["url"],
        "type": "object",
    }


def _track() -> dict[str, Any]:
    return {
        "additionalProperties": True,
        "properties": {
            "id": {"type": "string"},
            "keyframes": {"items": _keyframe(), "minItems": 1, "type": "array"},
            "type": {"enum": ["video", "audio", "image"], "type": "string"},
        },
        "required": ["keyframes"],
        "type": "object",
    }


_SCHEMAS: dict[str, dict[str, Any]] = {
    "scale-video": _document(
        "scale-video",
        ["video_url"],
        {
            "height": {"minimum": 0, "type": "integer"},
            "mode": {"default": "pad", "enum": ["pad", "crop", "stretch"], "type": "string"},
            "video_url": _URL,
            "width": {"minimum": 0, "type": "integer"},
        },
    ),
    "trim-video": _document(
        "trim-video",
        ["video_url"],
        {
            "duration": {"minimum": 0, "type": "number"},
            "end_time": {"minimum": 0, "type": "number"},
            "start_time": {"default": 0, "minimum": 0, "type": "number"},
            "video_url": _URL,
        },
    ),
    "loudnorm": _document(
        "loudnorm",
        ["audio_url"],
        {
            "audio_url": _URL,
            "integrated_loudness": {"default": -18, "type": "number"},
            "volume": {"type": "integer"},
        },
    ),
    "extract-frame": _document(
        "extract-frame",
        ["video_url"],
        {
            "frame_type": {"default": "first", "enum": ["first", "last"], "type": "string"},
            "video_url": _URL,
        },
    ),
    "metadata": _document("metadata", ["media_url"], {"media_url": _URL}),
    "merge-audio-video": _document(
        "merge-audio-video",
        ["video_url", "audio_url"],
        {"audio_url": _URL, "video_url": _URL},
    ),
    "merge-videos": _document(
        "merge-videos",
        ["video_urls"],
        {"video_urls": {"items": _URL, "minItems": 2, "type": "array"}},
    ),
    "images-to-video": _document(
        "images-to-video",
        ["images"],
        {
            "fps": {"default": 4, "exclusiveMinimum": 0, "type": "number"},
            "images": {"items": _image_item(), "minItems": 1, "type": "array"},
        },
    ),
    "compose": _document(
        "compose",
        ["tracks"],
        {
            "fps": {"default": 4, "exclusiveMinimum": 0, "type": "number"},
            "tracks": {"items": _track(), "minItems": 1, "type": "array"},
        },
    ),
    "publish": _document(
        "publish",
        ["request_id", "stamps"],
        {
            "request_id": _URL,
            "stamps": {"items": {"minLength": 1, "type": "string"}, "minItems": 1, "type": "array"},
        },
    ),
}


def schema_document(tool: str) -> dict[str, Any]:
    try:
        return _SCHEMAS[tool]
    except KeyError as error:
        raise KeyError(tool) from error


def schema_bytes(tool: str) -> bytes:
    return json.dumps(schema_document(tool), separators=(",", ":"), sort_keys=True).encode("utf-8")


def schema_sha256(tool: str) -> str:
    return hashlib.sha256(schema_bytes(tool)).hexdigest()


def registration_metadata(tool: str) -> str:
    document = schema_document(tool)
    kind = "ffmpeg-swarm-publish" if tool == "publish" else "ffmpeg-batch"
    metadata = json.dumps(
        {
            "kind": kind,
            "required": list(document["required"]),
            "schema_sha256": schema_sha256(tool),
            "schema_url": SCHEMA_URL,
            "streaming": False,
            "tool": tool,
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    size = len(metadata.encode("utf-8"))
    if size > METADATA_LIMIT_BYTES:
        raise ValueError(
            f"{tool} registration metadata is {size} bytes; limit is {METADATA_LIMIT_BYTES}"
        )
    return metadata
