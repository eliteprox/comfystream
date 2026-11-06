"""Export candidate native profiles from installed runner route schemas.

These are local additions, not an upstream registry release or proof of execution.
Existing upstream profile files are never replaced.
"""

import argparse
import asyncio
import copy
import hashlib
import json
import tempfile
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer
from jsonschema import Draft202012Validator


def operation_extensions(errors):
    return {
        "framing": "json",
        "success": {"status": [200]},
        "errors": [{"status": code, "kind": kind} for code, kind in errors],
    }


def document(profile, capability, paths):
    return {
        "openapi": "3.1.0",
        "info": {
            "title": profile,
            "version": "1",
            "description": "Native ComfyStream HTTP contract. Local candidate; execution conformance must be verified separately.",
        },
        "paths": paths,
        "x-livepeer": {
            "registry_entry": 1,
            "profile": profile,
            "kind": "profile",
            "mode": "single-shot",
            "capabilities": [capability],
            "admission": {"default": "candidate"},
            "attempts": "caller",
        },
    }


async def catalog_profiles():
    from stream_pack.catalog import load_catalog

    from comfystream.server.catalog_runner import DEFAULT_CAPABILITIES, PROFILES, create_app

    class Client:
        catalog = load_catalog()

        async def validate_capabilities(self, _names):
            pass

        async def close(self):
            pass

    async def register(*_args, **_kwargs):
        class Registration:
            async def close(self):
                pass

        return Registration()

    with tempfile.TemporaryDirectory() as temp:
        client = Client()
        client.input_dir = client.output_dir = temp
        config = {
            "image": "local/schema-export@sha256:" + "0" * 64,
            "capacity_group": "schema-export",
            "models": {
                name: {"app": name, "runner_id": name, "price": "0.02"}
                for name in DEFAULT_CAPABILITIES
            },
        }
        app = create_app(
            client,
            config,
            orchestrator="http://127.0.0.1:1",
            secret="unused",
            runner_url="http://127.0.0.1:8993",
            register_fn=register,
        )
        async with TestClient(TestServer(app)) as http:
            response = await http.get("/openapi.json")
            routes = (await response.json())["paths"]
    results = {}
    capabilities = {"birefnet": "image-edit", "ace-music": "music", "triposr": "image-to-3d"}
    for name, capability in capabilities.items():
        path, profile, operation_id = PROFILES[client.catalog[name].document["operation"]]
        operation = copy.deepcopy(routes["/catalog/" + name + path]["post"])
        operation["operationId"] = operation_id
        # model selection belongs to the runner envelope, not a model-specific registry entry.
        for media in operation["requestBody"]["content"].values():
            media["schema"]["properties"]["model"] = {"type": "string", "minLength": 1}
        operation["x-livepeer"] = operation_extensions(
            [
                (400, "input_invalid"),
                (413, "input_unsupported"),
                (415, "input_unsupported"),
                (502, "upstream_failed"),
                (503, "busy"),
                (504, "deadline_exceeded"),
            ]
        )
        results[profile] = document(profile, capability, {path: {"post": operation}})
    return results


def ffmpeg_profile():
    from comfystream.ffmpeg.schemas import schema_bytes
    from comfystream.server.ffmpeg_runner import BATCH_TOOLS

    paths = {}
    for tool in (*BATCH_TOOLS, "publish"):
        schema = json.loads(schema_bytes(tool))
        # Internal schema identity keys are not request constraints.
        for key in ("format", "tool"):
            schema.pop(key, None)
        path = "/publish" if tool == "publish" else "/batch/" + tool
        paths[path] = {
            "post": {
                "operationId": tool.replace("-", "_"),
                "summary": "Native FFmpeg " + tool,
                "requestBody": {
                    "required": True,
                    "content": {"application/json": {"schema": schema}},
                },
                "responses": {
                    "200": {
                        "description": "Tool result: metadata or media receipt; publication uses caller-supplied stamps",
                        "content": {"application/json": {"schema": {"type": "object"}}},
                    },
                    "default": {"description": "Error object with error.message and error.status"},
                },
                "x-livepeer": operation_extensions(
                    [
                        (400, "input_invalid"),
                        (404, "unavailable"),
                        (413, "input_unsupported"),
                        (500, "internal"),
                        (502, "upstream_failed"),
                        (503, "busy"),
                        (504, "deadline_exceeded"),
                    ]
                ),
            }
        }
    return document("comfystream.ffmpeg.v1", "media-tools", paths)


def live_profile(workflow=False):
    routes = (
        {"analyze": "post", "start_stream": "post", "update_stream": "post", "text": "get"}
        if workflow
        else {
            name: ("get" if name in {"session", "status", "stats", "sse"} else "post")
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
    )
    paths = {}
    for name, method in routes.items():
        operation = {
            "operationId": name,
            "summary": "ComfyStream session " + name,
            "description": "Existing session control protocol. Session identity is supplied by the orchestrator; parameters depend on the operator-installed pipeline. See the runner /status for its enforced parameter schema.",
            "responses": {
                "200": {
                    "description": "Session control result",
                    "content": {"application/json": {"schema": {"type": "object"}}},
                },
                "default": {"description": "Declared session or parameter error"},
            },
            "x-livepeer": operation_extensions(
                [
                    (400, "input_invalid"),
                    (404, "unavailable"),
                    (409, "busy"),
                    (413, "input_unsupported"),
                    (422, "input_invalid"),
                    (500, "internal"),
                    (502, "upstream_failed"),
                    (503, "unavailable"),
                    (504, "deadline_exceeded"),
                ]
            ),
        }
        if method == "post":
            operation["requestBody"] = {
                "content": {"application/json": {"schema": {"type": "object"}}}
            }
        if name in {"stream", "start_stream"}:
            operation["description"] += (
                " Returns trickle input/output channel URLs; media is transported over those channels."
            )
        if name == "sse":
            operation["x-livepeer"]["framing"] = "sse"
            operation["responses"]["200"]["content"] = {
                "text/event-stream": {"schema": {"type": "string"}}
            }
        if workflow and name == "text":
            operation["responses"]["200"]["content"] = {"application/json": {"schema": {}}}
        paths["/" + name] = {method: operation}
    profile = "comfystream.workflow.v1" if workflow else "comfystream.live.v1"
    result = document(profile, "live-video-to-video", paths)
    result["x-livepeer"]["mode"] = "persistent"
    if workflow:
        result["info"]["description"] += (
            " Companion GET /ws_stream upgrades to WebSocket: client JSON start, server JSON ready, binary JPEG frames in both directions, text eos to end. The standard has no WebSocket framing enum; this companion transport is preserved but is not advertised as an HTTP binary-framed operation."
        )
    return result


def swarm_profile():
    request_schema = {
        "type": "object",
        "required": ["owner", "batch_id"],
        "properties": {
            "owner": {"type": "string", "pattern": "^0x[0-9a-fA-F]{40}$"},
            "batch_id": {"type": "string", "pattern": "^[0-9a-fA-F]{64}$"},
        },
    }
    response_schema = {
        "type": "object",
        "required": ["session", "protocol", "in", "events", "stamps", "control"],
        "properties": {
            "session": {"type": "string"},
            "protocol": {"const": 1},
            **{
                name: {"type": "string", "format": "uri", "x-media": {"by": "trickle"}}
                for name in ("in", "events", "stamps", "control")
            },
        },
    }
    operation = {
        "operationId": "start",
        "summary": "Start caller-stamped live Swarm publishing",
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": request_schema}},
        },
        "responses": {
            "200": {
                "description": "Session and in/events/stamps/control trickle channel URLs",
                "content": {"application/json": {"schema": response_schema}},
            },
            "default": {
                "description": "Invalid publisher, occupied session, or unavailable Bee/funding"
            },
        },
        "x-livepeer": operation_extensions(
            [(400, "input_invalid"), (409, "busy"), (503, "unavailable")]
        ),
    }
    result = document(
        "comfystream.swarm-publish.v1", "live-media-publish", {"/start": {"post": operation}}
    )
    result["x-livepeer"]["mode"] = "persistent"
    result["info"]["description"] += (
        " Explicit local capability addition: live-media-publish. Signed postage belongs to the caller. Companion GET /ingest uses stamped binary WebSocket frames and text eos; it is not mislabeled as HTTP binary framing."
    )
    return result


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--standard", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    schema = json.loads((args.standard / "schemas/registry-entry.v1.schema.json").read_text())
    root_validator = Draft202012Validator(schema)
    operation_validator = Draft202012Validator(
        {"$ref": "#/$defs/operation", "$defs": schema["$defs"]}
    )
    profiles = await catalog_profiles()
    ffmpeg = ffmpeg_profile()
    profiles[ffmpeg["x-livepeer"]["profile"]] = ffmpeg
    for entry in (live_profile(), live_profile(workflow=True), swarm_profile()):
        profiles[entry["x-livepeer"]["profile"]] = entry
    index = {"registry_index": 1, "entries": {}}
    for profile, entry in profiles.items():
        root_validator.validate(entry["x-livepeer"])
        for item in entry["paths"].values():
            for operation in item.values():
                operation_validator.validate(operation["x-livepeer"])
        namespace, name, version = profile.split(".")
        relative = Path("registry") / namespace / (name + "." + version + ".json")
        destination = args.output / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        data = (json.dumps(entry, indent=2, ensure_ascii=False) + "\n").encode()
        destination.write_bytes(data)
        index["entries"][profile] = {
            "path": relative.as_posix(),
            "sha256": hashlib.sha256(data).hexdigest(),
            "status": "active",
            "aliases": [],
            "steward": ["@eliteprox"],
        }
    (args.output / "registry/index.json").write_text(json.dumps(index, indent=2) + "\n")
    (args.output / "capabilities.additions.json").write_text(
        json.dumps(
            {
                "base_registry_commit": "6eb406e09bc4dee16f9091634b34322c807ba0a1",
                "upstream_release": False,
                "capabilities": [
                    {
                        "name": "live-media-publish",
                        "group": "live",
                        "mode": "persistent",
                        "summary": "Publish caller-stamped live media to Swarm",
                    }
                ],
            },
            indent=2,
        )
        + "\n"
    )
    print(f"Exported {len(profiles)} local candidate profiles; upstream registry unchanged")


if __name__ == "__main__":
    asyncio.run(main())
