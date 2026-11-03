import base64
import json
from pathlib import Path

import aiohttp
import pytest
from aiohttp.test_utils import TestServer
from stream_pack.catalog import load_catalog

from comfystream.server.catalog_runner import DEFAULT_CAPABILITIES, create_app, declarations


class Client:
    def __init__(self, tmp_path):
        self.catalog = load_catalog()
        self.input_dir = str(tmp_path / "inputs")
        self.output_dir = str(tmp_path / "outputs")
        Path(self.input_dir).mkdir()
        Path(self.output_dir).mkdir()
        self.calls = []
        self.validated = []
        self.closed = False

    async def validate_capabilities(self, names):
        self.validated = names

    async def execute(self, name, inputs, *, timeout):
        self.calls.append((name, inputs))
        if name == "whisper":
            assert (Path(self.input_dir) / inputs["audio"]).read_bytes() == b"wav fixture"
            return {"text": "transcribed", "artifacts": []}
        directory = Path(self.output_dir) / "request_fixture"
        directory.mkdir()
        path = directory / "result.png"
        path.write_bytes(b"image fixture")
        return {"artifacts": [{"path": str(path), "media_type": "image/png"}]}

    async def close(self):
        self.closed = True


def config():
    return {
        "image": "example/runner@sha256:" + "a" * 64,
        "capacity_group": "ada0",
        "models": {
            name: {"app": "catalog-" + name, "runner_id": "ada-" + name, "price": "0.01"}
            for name in DEFAULT_CAPABILITIES
        },
    }


@pytest.mark.asyncio
async def test_exact_model_registrations_dispatch_sdk_shapes_and_cleanup(tmp_path):
    client = Client(tmp_path)
    registrations, closed = [], []

    async def register(orchestrator, **kwargs):
        import inspect

        from livepeer_gateway.live_runner import register_runner

        inspect.signature(register_runner).bind(orchestrator, **kwargs)
        assert client.validated == list(DEFAULT_CAPABILITIES)
        registrations.append(kwargs)

        class Registration:
            async def close(self):
                closed.append(kwargs["runner_id"])

        return Registration()

    app = create_app(
        client,
        config(),
        orchestrator="https://orch",
        secret="secret",
        runner_url="http://127.0.0.1:8993",
        register_fn=register,
    )
    async with TestServer(app) as server:
        assert len(registrations) == 5
        assert all(r["mode"] == "single-shot" and r["unit"] == "fixed" for r in registrations)
        metadata = [json.loads(r["metadata"]) for r in registrations]
        assert {m["capacity_group"]["id"] for m in metadata} == {"ada0"}
        assert len({m["model_name"] for m in metadata}) == 5
        assert not any("Qwen" in m["model_name"] or "Wan" in m["model_name"] for m in metadata)
        async with aiohttp.ClientSession() as http:
            async with http.get(server.make_url("/openapi.json")) as response:
                schema = await response.json()
                assert schema["openapi"] == "3.1.0" and len(schema["paths"]) == 5
                assert all("requestBody" in path["post"] for path in schema["paths"].values())
                transcription = schema["paths"]["/catalog/whisper/v1/audio/transcriptions"]["post"][
                    "requestBody"
                ]["content"]
                assert set(transcription) == {"application/json", "multipart/form-data"}
            model = client.catalog["flux-image"].document["model"]["source"]
            async with http.post(
                server.make_url("/catalog/flux-image/v1/images/generations"),
                json={"model": model, "prompt": "cat", "size": "256x256"},
            ) as response:
                assert response.status == 200
                assert (await response.json())["data"] == [
                    {"b64_json": base64.b64encode(b"image fixture").decode()}
                ]
            assert client.calls[0][1] == {"prompt": "cat", "width": 256, "height": 256}
            form = aiohttp.FormData()
            form.add_field("model", client.catalog["whisper"].document["model"]["source"])
            form.add_field(
                "file", b"wav fixture", filename="../../input.wav", content_type="audio/wav"
            )
            async with http.post(
                server.make_url("/catalog/whisper/v1/audio/transcriptions"), data=form
            ) as response:
                assert response.status == 200 and await response.json() == {"text": "transcribed"}
            for payload in (
                {"model": model, "prompt": "cat", "unknown": True},
                {"model": "other", "prompt": "cat"},
            ):
                async with http.post(
                    server.make_url("/catalog/flux-image/v1/images/generations"), json=payload
                ) as response:
                    assert response.status == 400
            async with http.post(
                server.make_url("/catalog/birefnet/v1/image-transformations"),
                json={
                    "model": client.catalog["birefnet"].document["model"]["source"],
                    "image": "/etc/passwd",
                },
            ) as response:
                assert response.status == 400
        assert len(client.calls) == 2
        assert list(Path(client.input_dir).iterdir()) == []
        assert list(Path(client.output_dir).iterdir()) == []
    assert len(closed) == 5 and client.closed


@pytest.mark.asyncio
async def test_partial_registration_failure_unregisters_and_closes_client(tmp_path):
    client = Client(tmp_path)
    closed = []
    calls = []

    async def register(*args, **kwargs):
        calls.append(kwargs)
        if len(calls) == 2:
            raise RuntimeError("registration failed")

        class Registration:
            async def close(self):
                closed.append(True)

        return Registration()

    app = create_app(
        client,
        config(),
        orchestrator="https://orch",
        secret="secret",
        runner_url="http://127.0.0.1:8993",
        register_fn=register,
    )
    with pytest.raises(RuntimeError):
        async with TestServer(app):
            pass
    assert closed == [True] and client.closed


def test_deployment_rejects_omitted_models_and_mutable_images(tmp_path):
    client = Client(tmp_path)
    value = config()
    value["models"]["qwen-chat"] = {"app": "chat", "runner_id": "chat", "price": "1"}
    with pytest.raises(ValueError):
        declarations(client, value)
    value = config()
    value["image"] = "example:latest"
    with pytest.raises(ValueError):
        declarations(client, value)
