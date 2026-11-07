"""Private, authenticated transport for operator-installed local capabilities."""

from __future__ import annotations

import argparse
import asyncio
import hmac
import os
from importlib.metadata import version
from pathlib import Path

from aiohttp import web
from jsonschema import ValidationError

from comfystream.capability_client import CapabilityClient, CapacityUnavailable


def create_app(client, *, profile: str, token: str) -> web.Application:
    if profile not in {"media", "chat"} or not token:
        raise ValueError("An explicit profile and private transport token are required")
    catalog = {
        name: spec
        for name, spec in client.catalog.items()
        if (spec.document["dependency_group"] == "chat") == (profile == "chat")
    }

    @web.middleware
    async def authenticate(request, handler):
        supplied = request.headers.get("Authorization", "").removeprefix("Bearer ")
        if not hmac.compare_digest(supplied.encode(), token.encode()):
            return web.json_response({"error": "unauthorized"}, status=401)
        return await handler(request)

    app = web.Application(middlewares=[authenticate], client_max_size=1024 * 1024)

    async def discover(request):
        return web.json_response(
            {
                "capabilities": [
                    {"id": spec.id, "digest": spec.digest, "model": spec.document["model"]}
                    for spec in catalog.values()
                ]
            }
        )

    async def execute(request):
        capability = request.match_info["capability"]
        if capability not in catalog:
            return web.json_response({"error": "capability_not_installed"}, status=404)
        try:
            body = await request.json()
            if not isinstance(body, dict) or set(body) != {"input", "timeout"}:
                raise ValueError()
            timeout = body["timeout"]
            if type(timeout) not in {int, float} or not 0 < timeout <= 3600:
                raise ValueError()
            catalog[capability].validate(body["input"])
            result = await client.execute(capability, body["input"], timeout=timeout)
            return web.json_response(result)
        except (ValueError, TypeError, ValidationError):
            return web.json_response({"error": "invalid_input"}, status=400)
        except CapacityUnavailable:
            return web.json_response({"error": "capacity_unavailable"}, status=503)
        except TimeoutError:
            return web.json_response({"error": "deadline_exceeded"}, status=504)
        except asyncio.CancelledError:
            raise
        except Exception:
            return web.json_response({"error": "execution_failed"}, status=502)

    async def close(app):
        await client.close()

    async def validate(app):
        await client.validate_capabilities(list(catalog))

    app.router.add_get("/internal/capabilities", discover)
    app.router.add_post("/internal/capabilities/{capability}/execute", execute)
    app.on_cleanup.append(close)
    app.on_startup.append(validate)
    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8992)
    parser.add_argument("--profile", choices=("media", "chat"), required=True)
    args = parser.parse_args()
    major = int(version("transformers").split(".")[0])
    if (major >= 5) != (args.profile == "chat"):
        raise SystemExit("Installed Transformers does not match the requested capability profile")
    state = Path(os.environ.get("COMFYSTREAM_CAPABILITY_STATE", "/state"))
    client = CapabilityClient(
        workspace=os.environ.get("COMFYUI_CWD", "/workspace/ComfyUI"),
        input_dir=str(state / "inputs"),
        output_dir=str(state / "outputs"),
        capacity_file=os.environ["COMFYSTREAM_CAPACITY_FILE"],
        gpu=os.environ.get("CUDA_VISIBLE_DEVICES", "0"),
    )
    app = create_app(client, profile=args.profile, token=os.environ["COMFYSTREAM_CAPABILITY_TOKEN"])
    # A disconnected/cancelled gateway request cancels execution; the client waits
    # for its assigned CUDA worker to terminate before releasing GPU admission.
    web.run_app(app, host=args.host, port=args.port, handler_cancellation=True, access_log=None)


if __name__ == "__main__":
    main()
