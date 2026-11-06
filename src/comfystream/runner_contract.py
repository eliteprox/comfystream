"""Compact runner-contract v1 metadata for opt-in immutable deployments."""

import hashlib
import json
import os
import re


def enabled():
    return bool(os.environ.get("COMFYSTREAM_RUNTIME_IMAGE"))


def envelope(
    profile, operations, *, model=None, settings=None, capacity_group=None, attributes=None
):
    image = os.environ["COMFYSTREAM_RUNTIME_IMAGE"]
    if not re.fullmatch(r".+@sha256:[0-9a-f]{64}", image):
        raise ValueError("COMFYSTREAM_RUNTIME_IMAGE must be a full digest-pinned image reference")
    endpoints = {name: {"profile": profile, **options} for name, options in operations.items()}
    identity = {"images": [image], "profiles": [profile], "settings": settings or {}}
    if model:
        identity["model"] = {"source": model["source"], "revision": model.get("revision")}
    canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    result = {
        "runner_contract": 1,
        "deployment_revision": "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "endpoints": endpoints,
        "runtime": {"image": image.rsplit("@", 1)[1]},
    }
    if model:
        result["model"] = model
    if capacity_group:
        result["capacity_group"] = {"id": capacity_group, "slots": 1}
    if attributes:
        result["attributes"] = attributes
    raw = json.dumps(result, separators=(",", ":"), ensure_ascii=False)
    if len(raw.encode("utf-8")) > 1024:
        raise ValueError("Registration metadata exceeds 1024 UTF-8 bytes")
    return raw


def realtime_envelope(spec, *, compute, startup_s):
    settings = spec.to_dict()
    for key in ("app", "price", "currency", "unit", "label", "capacity", "port", "gpu", "python"):
        settings.pop(key, None)
    model = None
    if spec.model and spec.backend != "ffmpeg_filter":
        revisions = json.loads(os.environ.get("COMFYSTREAM_MODEL_REVISIONS", "{}"))
        revision = revisions.get(spec.model)
        if not revision:
            raise ValueError(f"Immutable live model revision is required: {spec.model}")
        model = {"name": spec.model, "source": "hf:" + spec.model, "revision": revision}
    operations = {
        name: {}
        for name in (
            "stream",
            "update",
            "pause",
            "resume",
            "stop",
            "session",
            "status",
            "stats",
            "sse",
        )
    }
    operations["stream"] = {"stop": "/stop", "channels": {"in": ["video"], "out": ["video"]}}
    attributes = {"compute": compute, "estimated_startup_s": startup_s}
    return envelope(
        "comfystream.live.v1",
        operations,
        model=model,
        settings=settings,
        capacity_group=(os.environ.get("COMFYSTREAM_GPU_CAPACITY_GROUP") if spec.gpu else None),
        attributes=attributes,
    )


def workflow_envelope():
    # The WebSocket is a companion transport documented by this profile, not
    # mislabeled as HTTP binary framing (v1 has no WebSocket framing enum).
    return envelope(
        "comfystream.workflow.v1",
        {name: {} for name in ("analyze", "start_stream", "update_stream", "text")},
        settings={"transport": ["trickle", "websocket-jpeg"], "workflow_driven": True},
        capacity_group=os.environ.get("COMFYSTREAM_GPU_CAPACITY_GROUP"),
    )
