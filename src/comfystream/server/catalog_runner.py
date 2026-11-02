"""Synchronous Livepeer catalog runner; inference stays in CapabilityClient."""

from __future__ import annotations

import argparse
import asyncio
import base64
import binascii
import copy
import hashlib
import json
import os
import re
import shutil
import tempfile
import time
from decimal import Decimal
from pathlib import Path

from aiohttp import web
from jsonschema import ValidationError

from comfystream.capability_client import CapabilityClient, CapacityUnavailable

DEFAULT_CAPABILITIES = ("flux-image", "birefnet", "whisper", "ace-music", "triposr")
PROFILES = {
    "images_generations": ("/v1/images/generations", "openai.images.v1"),
    "audio_transcriptions": ("/v1/audio/transcriptions", "openai.audio.transcriptions.v1"),
    "image_transformations": ("/v1/image-transformations", "gateway.image.transformations.v1"),
    "music_generations": ("/v1/music/generations", "gateway.music.v1"),
    "meshes_generations": ("/v1/meshes/generations", "gateway.meshes.v1"),
}


def declarations(client, config):
    """Validate all operator configuration before advertising any model."""
    if set(config) != {"image", "capacity_group", "models"}:
        raise ValueError("Unexpected deployment configuration")
    if not re.fullmatch(r".+@sha256:[0-9a-f]{64}", config["image"]):
        raise ValueError("An immutable image digest is required")
    group = config["capacity_group"]
    if not isinstance(group, str) or not 1 <= len(group) <= 64:
        raise ValueError("A shared capacity identity is required")
    result = {}
    for name, settings in config["models"].items():
        if name not in DEFAULT_CAPABILITIES or set(settings) != {"app", "runner_id", "price"}:
            raise ValueError("Unsupported model deployment")
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", settings["runner_id"]):
            raise ValueError("Invalid runner identity")
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", settings["app"]):
            raise ValueError("Invalid application identity")
        price = Decimal(str(settings["price"]))
        if not price.is_finite() or price <= 0:
            raise ValueError("An explicit positive fixed price is required")
        spec = client.catalog[name]
        operation = spec.document["operation"]
        path, profile = PROFILES[operation]
        metadata = {
            "inference_contract": 2,
            "runner_id": settings["runner_id"],
            "model_name": spec.document["model"]["source"],
            "runtime": {"name": "comfystream", "image": config["image"]},
            "deployment_revision": "sha256:"
            + hashlib.sha256((config["image"] + ":" + spec.digest).encode()).hexdigest(),
            "endpoints": {
                operation: {
                    "path": path,
                    "profile": profile,
                    "usage": "media.measured.v1",
                    "stream_usage": "unsupported",
                }
            },
            "capacity_group": {"id": group, "slots": 1},
            "admission": "candidate",
        }
        encoded = json.dumps(metadata, separators=(",", ":"))
        if len(encoded.encode()) > 1024:
            raise ValueError("Registration metadata exceeds 1024 bytes")
        result[name] = (settings, spec, path, encoded)
    if not result or len({r[0]["runner_id"] for r in result.values()}) != len(result):
        raise ValueError("Nonempty deployment and unique runner identities required")
    if len({r[0]["app"] for r in result.values()}) != len(result):
        raise ValueError("Each model requires its own application identity")
    return result


def create_app(client, config, *, orchestrator, secret, runner_url, register_fn=None):
    entries = declarations(client, config)
    if not runner_url.startswith("http://127.0.0.1:"):
        raise ValueError("This runner must share the orchestrator host and use loopback")
    registrations = []
    app = web.Application(client_max_size=32 * 1024 * 1024)

    async def lifecycle(app):
        try:
            await client.validate_capabilities(list(entries))
            register = register_fn
            if register is None:
                from livepeer_gateway.live_runner import register_runner

                register = register_runner
            for name, (settings, spec, path, metadata) in entries.items():
                registration = await register(
                    orchestrator,
                    secret=secret,
                    runner_url=runner_url.rstrip("/") + "/catalog/" + name,
                    app=settings["app"],
                    runner_id=settings["runner_id"],
                    mode="single-shot",
                    capacity=1,
                    price=settings["price"],
                    currency="usd",
                    unit="fixed",
                    metadata=metadata,
                    version="inference.runner.v2",
                )
                registrations.append(registration)
            yield
        finally:
            for registration in reversed(registrations):
                try:
                    await registration.close()
                except Exception:
                    pass
            await client.close()

    app.cleanup_ctx.append(lifecycle)

    def handler(name, spec):
        async def execute(request):
            staging = None
            output_directories = set()
            try:
                if (
                    request.query
                    or request.headers.get("Content-Encoding", "identity") != "identity"
                ):
                    raise ValueError()
                staging = Path(tempfile.mkdtemp(prefix="upload_", dir=client.input_dir))
                inputs = {}
                if request.content_type == "multipart/form-data":
                    reader = await request.multipart()
                    size = 0
                    async for part in reader:
                        key = part.name
                        if not key or key in inputs or len(inputs) >= 32:
                            raise ValueError()
                        data = bytearray()
                        while chunk := await part.read_chunk():
                            size += len(chunk)
                            if size > 32 * 1024 * 1024:
                                raise web.HTTPRequestEntityTooLarge(
                                    max_size=32 * 1024 * 1024, actual_size=size
                                )
                            data.extend(chunk)
                        if key in {"file", "image"}:
                            suffix = ".wav" if name == "whisper" else ".png"
                            target = staging / ("media" + suffix)
                            target.write_bytes(data)
                            inputs[key] = str(target.relative_to(Path(client.input_dir)))
                        else:
                            inputs[key] = data.decode("utf-8")
                elif request.content_type == "application/json":

                    def pairs(items):
                        value = {}
                        for key, item in items:
                            if key in value:
                                raise ValueError()
                            value[key] = item
                        return value

                    inputs = json.loads(await request.read(), object_pairs_hook=pairs)
                    if not isinstance(inputs, dict):
                        raise ValueError()
                    for key in ("image", "file"):
                        if key in inputs:
                            # Never accept URLs or worker filesystem paths.
                            value = inputs[key]
                            if not isinstance(value, str) or not value.startswith("data:"):
                                raise ValueError()
                            header, encoded = value.split(",", 1)
                            if not header.endswith(";base64"):
                                raise ValueError()
                            data = base64.b64decode(encoded, validate=True)
                            target = staging / ("media.wav" if name == "whisper" else "media.png")
                            target.write_bytes(data)
                            inputs[key] = str(target.relative_to(Path(client.input_dir)))
                else:
                    raise web.HTTPUnsupportedMediaType()
                if inputs.pop("model", None) != spec.document["model"]["source"]:
                    raise ValueError()
                if name == "whisper":
                    if "audio" in inputs:
                        raise ValueError()
                    inputs["audio"] = inputs.pop("file")
                    if inputs.pop("response_format", "json") != "json":
                        raise ValueError()
                if name == "flux-image":
                    if (
                        inputs.pop("n", 1) != 1
                        or inputs.pop("response_format", "b64_json") != "b64_json"
                    ):
                        raise ValueError()
                    if "size" in inputs:
                        width, height = inputs.pop("size").split("x")
                        if "width" in inputs or "height" in inputs:
                            raise ValueError()
                        inputs.update(width=int(width), height=int(height))
                spec.validate(inputs)
                result = await client.execute(name, inputs, timeout=600)
                artifacts = []
                for artifact in result.get("artifacts", []):
                    path = Path(artifact["path"]).resolve()
                    root = Path(client.output_dir).resolve()
                    if (
                        not path.is_relative_to(root)
                        or path.parent.parent != root
                        or not path.parent.name.startswith("request_")
                    ):
                        raise RuntimeError("Invalid artifact location")
                    output_directories.add(path.parent)
                    if path.stat().st_size > 64 * 1024 * 1024:
                        raise RuntimeError("Output limit exceeded")
                    artifacts.append(
                        {
                            "b64_json": base64.b64encode(path.read_bytes()).decode(),
                            "media_type": artifact["media_type"],
                        }
                    )
                if name == "whisper":
                    return web.json_response({"text": result["text"]})
                if name == "flux-image":
                    return web.json_response(
                        {
                            "created": int(time.time()),
                            "data": [{"b64_json": a["b64_json"]} for a in artifacts],
                        }
                    )
                return web.json_response(
                    {
                        "created": int(time.time()),
                        "model": spec.document["model"]["source"],
                        "data": artifacts,
                    }
                )
            except (ValueError, TypeError, KeyError, ValidationError, binascii.Error):
                return web.json_response(
                    {
                        "error": {
                            "type": "invalid_request_error",
                            "message": "Unsupported or invalid input",
                        }
                    },
                    status=400,
                )
            except CapacityUnavailable:
                return web.json_response(
                    {"error": {"type": "api_error", "message": "Capacity unavailable"}}, status=503
                )
            except TimeoutError:
                return web.json_response(
                    {"error": {"type": "timeout_error", "message": "Execution deadline exceeded"}},
                    status=504,
                )
            except web.HTTPException:
                raise
            except asyncio.CancelledError:
                raise
            except Exception:
                return web.json_response(
                    {"error": {"type": "api_error", "message": "Model execution failed"}},
                    status=502,
                )
            finally:
                if staging:
                    shutil.rmtree(staging, ignore_errors=True)
                for directory in output_directories:
                    shutil.rmtree(directory, ignore_errors=True)

        return execute

    for name, (_, spec, path, _) in entries.items():
        app.router.add_post("/catalog/" + name + path, handler(name, spec))

    async def health(request):
        return web.json_response({"status": "ready", "capabilities": list(entries)})

    app.router.add_get("/healthz", health)

    async def openapi(request):
        paths = {}
        for name, (_, spec, path, _) in entries.items():
            schema = copy.deepcopy(spec.document["input_schema"])
            schema["properties"]["model"] = {
                "type": "string",
                "const": spec.document["model"]["source"],
            }
            schema["required"].append("model")
            if name == "whisper":
                schema["properties"].pop("audio")
                schema["required"].remove("audio")
                schema["required"].append("file")
                schema["properties"]["file"] = {"type": "string", "format": "binary"}
                schema["properties"]["response_format"] = {"const": "json", "type": "string"}
                mime = "multipart/form-data"
                response_schema = {
                    "type": "object",
                    "required": ["text"],
                    "properties": {"text": {"type": "string"}},
                }
            else:
                mime = "application/json"
                response_schema = {
                    "type": "object",
                    "required": ["created", "data"],
                    "properties": {
                        "created": {"type": "integer"},
                        "model": {"type": "string"},
                        "data": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "required": ["b64_json"],
                                "properties": {
                                    "b64_json": {"type": "string", "contentEncoding": "base64"},
                                    "media_type": {"type": "string"},
                                },
                            },
                        },
                    },
                }
                if "image" in schema["properties"]:
                    schema["properties"]["image"]["description"] = (
                        "Inline data URL with base64 encoded image; paths and remote URLs are rejected."
                    )
            if name == "flux-image":
                schema["properties"].update(
                    {
                        "size": {"type": "string", "pattern": "^[0-9]+x[0-9]+$"},
                        "n": {"type": "integer", "const": 1},
                        "response_format": {"type": "string", "const": "b64_json"},
                    }
                )
            error = {
                "description": "Request, capacity or execution failure",
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "required": ["error"],
                            "properties": {
                                "error": {
                                    "type": "object",
                                    "required": ["type", "message"],
                                    "properties": {
                                        "type": {"type": "string"},
                                        "message": {"type": "string"},
                                    },
                                }
                            },
                        }
                    }
                },
            }
            paths["/catalog/" + name + path] = {
                "post": {
                    "operationId": name,
                    "description": spec.document["description"],
                    "requestBody": {"required": True, "content": {mime: {"schema": schema}}},
                    "responses": {
                        "200": {
                            "description": "Completed model response",
                            "content": {"application/json": {"schema": response_schema}},
                        },
                        **{str(status): error for status in (400, 413, 415, 502, 503, 504)},
                    },
                }
            }
        return web.json_response(
            {
                "openapi": "3.1.0",
                "info": {"title": "ComfyStream synchronous catalog", "version": "2.0.0"},
                "paths": paths,
            }
        )

    app.router.add_get("/openapi.json", openapi)
    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--port", type=int, default=8993)
    args = parser.parse_args()
    state = Path(os.environ.get("COMFYSTREAM_CAPABILITY_STATE", "/state"))
    for folder in ("inputs", "outputs"):
        (state / folder).mkdir(parents=True, exist_ok=True)
    client = CapabilityClient(
        workspace=os.environ["COMFYUI_CWD"],
        input_dir=str(state / "inputs"),
        output_dir=str(state / "outputs"),
        capacity_file=os.environ["COMFYSTREAM_CAPACITY_FILE"],
        gpu=os.environ.get("CUDA_VISIBLE_DEVICES", "0"),
    )
    app = create_app(
        client,
        json.loads(Path(args.config).read_text()),
        orchestrator=os.environ["LIVEPEER_ORCH_URL"],
        secret=os.environ["LIVEPEER_ORCH_SECRET"],
        runner_url=f"http://127.0.0.1:{args.port}",
    )
    web.run_app(
        app,
        host="127.0.0.1",
        port=args.port,
        handler_cancellation=True,
        access_log=None,
        max_field_size=262144,
        max_line_size=262144,
    )


if __name__ == "__main__":
    main()
