"""Stream a live ffmpeg filter and serve the file tools as single-shot routes.

The persistent app is comfystream/ffmpeg-live. Its worker runs the
LoadTensor, ImageRotate, ImageScale, SaveTensor graph through Pipeline.
Batch apps queue LoadVideo and the other core nodes on a ProcessPoolExecutor.
Both register with the same orchestrator.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import aiohttp
from aiohttp import web
from livepeer_swarm.publisher import SwarmPublisher, SwarmPublishError

from comfystream.ffmpeg.execute import FfmpegJobError
from comfystream.ffmpeg.schemas import registration_metadata, schema_bytes
from comfystream.ffmpeg.swarm import SwarmHold, SwarmProtocolError, file_descriptor
from comfystream.job_pool import JobPool, PoolSaturatedError
from comfystream.realtime.spec import load_realtime_config
from comfystream.realtime.supervisor import RealtimeSupervisor

MAX_MEDIA_BYTES = 64 * 1024 * 1024
WORKSPACE = os.environ.get("COMFYUI_CWD", "/workspace/ComfyUI")

log = logging.getLogger("comfystream.ffmpeg")

BATCH_TOOLS = (
    "scale-video",
    "trim-video",
    "loudnorm",
    "extract-frame",
    "metadata",
    "merge-audio-video",
    "merge-videos",
    "images-to-video",
    "compose",
)
PRICE = 0.000001
OUTPUT_ROOT = Path(os.environ.get("FFMPEG_OUTPUT_DIR", "/tmp/comfystream-ffmpeg"))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Local ffmpeg streaming and batch runner")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("FFMPEG_RUNNER_PORT", "8994"))
    )
    parser.add_argument(
        "--orchestrator",
        default=os.environ.get("LIVEPEER_ORCH_URL", "https://localhost:8935"),
    )
    parser.add_argument("--orchSecret", default=os.environ.get("LIVEPEER_ORCH_SECRET", ""))
    parser.add_argument(
        "--runner-url",
        default=os.environ.get("LIVEPEER_RUNNER_URL", "http://127.0.0.1:8994"),
    )
    parser.add_argument(
        "--realtime-config",
        default=os.environ.get(
            "COMFYSTREAM_FFMPEG_CONFIG",
            str(Path(__file__).resolve().parents[1] / "configs" / "realtime-ffmpeg.yaml"),
        ),
    )
    return parser.parse_args()


async def _download(session: aiohttp.ClientSession, url: str, dest: Path) -> Path:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise FfmpegJobError("media url must be http or https")
    dest.parent.mkdir(parents=True, exist_ok=True)
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=60)) as response:
        if response.status != 200:
            raise FfmpegJobError(f"download failed status={response.status}", status=502)
        size = 0
        with dest.open("wb") as handle:
            async for chunk in response.content.iter_chunked(1024 * 1024):
                size += len(chunk)
                if size > MAX_MEDIA_BYTES:
                    raise FfmpegJobError("media exceeds 64 MB", status=413)
                handle.write(chunk)
    if dest.stat().st_size == 0:
        raise FfmpegJobError("downloaded media is empty", status=400)
    return dest


def _is_image(payload: bytes) -> bool:
    return (
        payload.startswith(b"\x89PNG")
        or payload.startswith(b"\xff\xd8\xff")
        or payload.startswith(b"GIF8")
        or (len(payload) >= 12 and payload.startswith(b"RIFF") and payload[8:12] == b"WEBP")
    )


def _require(body: dict[str, Any], key: str) -> str:
    value = body.get(key)
    if not isinstance(value, str) or not value.strip():
        raise FfmpegJobError(f"{key} is required")
    return value.strip()


async def _collect(
    session: aiohttp.ClientSession, tool: str, body: dict[str, Any], work: Path
) -> dict[str, Any]:
    if tool in {"scale-video", "trim-video", "extract-frame"}:
        path = await _download(session, _require(body, "video_url"), work / "input.mp4")
        return {"video": path.read_bytes()}
    if tool == "loudnorm":
        path = await _download(session, _require(body, "audio_url"), work / "input-audio.bin")
        return {"audio": path.read_bytes()}
    if tool == "metadata":
        path = await _download(session, _require(body, "media_url"), work / "input.bin")
        return {"media": path.read_bytes()}
    if tool == "merge-audio-video":
        video = await _download(session, _require(body, "video_url"), work / "video.mp4")
        audio = await _download(session, _require(body, "audio_url"), work / "audio.bin")
        return {"video": video.read_bytes(), "audio": audio.read_bytes()}
    if tool == "merge-videos":
        urls = body.get("video_urls")
        if not isinstance(urls, list) or len(urls) < 2:
            raise FfmpegJobError("video_urls needs at least two urls")
        paths = []
        for index, url in enumerate(urls):
            if not isinstance(url, str):
                raise FfmpegJobError("video_urls must be strings")
            paths.append((await _download(session, url, work / f"part-{index}.mp4")).read_bytes())
        return {"videos": paths}
    if tool == "images-to-video":
        images = body.get("images")
        if not isinstance(images, list) or not images:
            raise FfmpegJobError("images is required")
        pairs = []
        for index, item in enumerate(images):
            if isinstance(item, str):
                url, frames = item, 1
            elif isinstance(item, dict):
                url = _require(item, "url")
                frames = int(item.get("frames") or 1)
            else:
                raise FfmpegJobError("images entries must be urls or {url, frames}")
            path = await _download(session, url, work / f"image-{index}.png")
            pairs.append((path.read_bytes(), frames))
        return {"images": pairs}
    if tool == "compose":
        tracks = body.get("tracks")
        if not isinstance(tracks, list) or not tracks:
            raise FfmpegJobError("tracks is required")
        sequence = []
        index = 0
        fps = float(body.get("fps") or 4)
        for track in tracks:
            if not isinstance(track, dict):
                raise FfmpegJobError("each track must be an object")
            kind = str(track.get("type") or "video")
            if kind == "audio":
                continue
            for keyframe in track.get("keyframes") or []:
                if not isinstance(keyframe, dict):
                    continue
                url = _require(keyframe, "url")
                payload = (await _download(session, url, work / f"clip-{index}.bin")).read_bytes()
                index += 1
                if kind == "image" or _is_image(payload):
                    duration_ms = float(keyframe.get("duration") or 0)
                    frames = (
                        max(1, int(round(duration_ms / 1000.0 * fps))) if duration_ms > 0 else 1
                    )
                    sequence.append(("image", payload, frames))
                else:
                    sequence.append(("video", payload))
        if not sequence:
            raise FfmpegJobError("tracks need a video or image keyframe")
        return {"sequence": sequence}
    raise FfmpegJobError(f"unknown ffmpeg tool {tool}", status=404)


def _public_file(runner_url: str, path: Path) -> dict[str, Any]:
    """File URL for the caller.

    ``runner_url`` is the Docker address the orchestrator uses. Browsers cannot
    open that name. ``FFMPEG_PUBLIC_URL`` is the public prefix nginx serves,
    without a trailing ``/files`` segment.
    """
    public = os.environ.get("FFMPEG_PUBLIC_URL", "").strip()
    base = public or f"{runner_url.rstrip('/')}/files"
    return {
        "url": f"{base.rstrip('/')}/{path.name}",
        "content_type": "video/mp4" if path.suffix == ".mp4" else "application/octet-stream",
        "file_name": path.name,
        "file_size": path.stat().st_size,
    }


class FfmpegRunner:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.supervisor: RealtimeSupervisor | None = None
        self.pool: JobPool | None = None
        self.bee_url = os.environ.get("BEE_URL", "").strip()
        self.hold = SwarmHold() if self.bee_url else None
        self.publisher = SwarmPublisher(self.bee_url or None)
        self._registrations: list[Any] = []
        self._http: aiohttp.ClientSession | None = None
        OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

    def build_app(self) -> web.Application:
        app = web.Application(client_max_size=MAX_MEDIA_BYTES + 1024 * 1024)
        app.router.add_get("/health", self.handle_health)
        app.router.add_get("/files/{name}", self.handle_file)
        app.router.add_get("/batch/{tool}/schema", self.handle_schema)
        app.router.add_get("/publish/schema", self.handle_publish_schema)
        app.router.add_post("/batch/{tool}", self.handle_batch)
        app.router.add_post("/publish", self.handle_publish)
        app.on_startup.append(self.start)
        app.on_cleanup.append(self.close)
        return app

    async def start(self, _app: web.Application) -> None:
        self._http = aiohttp.ClientSession(headers={"User-Agent": "comfystream-ffmpeg"})
        self.pool = JobPool(
            max_workers=1,
            initializer=_batch_worker_init,
            initargs=(
                {
                    "cwd": WORKSPACE,
                    "input_directory": str(Path(WORKSPACE) / "input"),
                    "output_directory": str(Path(WORKSPACE) / "output"),
                    "disable_cuda_malloc": True,
                    "gpu_only": False,
                    "preview_method": "none",
                },
            ),
            pool_worker=_batch_worker_execute,
            timeout_message="ffmpeg workflow deadline exceeded",
        )
        await self.pool.start()
        specs, capacity = load_realtime_config(self.args.realtime_config)
        runner = self.args.runner_url.rstrip("/")
        host = runner.rsplit(":", 1)[0]
        self.supervisor = RealtimeSupervisor(
            specs,
            orchestrator=self.args.orchestrator,
            orch_secret=self.args.orchSecret,
            runner_host=host,
            bind_host="0.0.0.0",
            capacity=capacity,
        )
        await self.supervisor.start()
        if not self.args.orchestrator or not self.args.orchSecret:
            log.warning("batch tools loaded without orchestrator registration")
            return
        from livepeer_gateway.live_runner import register_runner

        from comfystream.realtime.gpu import pinned_gpu

        gpu = pinned_gpu()
        for tool in BATCH_TOOLS:
            registration = await register_runner(
                self.args.orchestrator,
                secret=self.args.orchSecret,
                runner_url=f"{runner}/batch/{tool}",
                app=f"comfystream/ffmpeg-{tool}",
                mode="single-shot",
                capacity=1,
                price=PRICE,
                currency="usd",
                unit="fixed",
                label=tool,
                metadata=registration_metadata(tool),
                version="comfystream.ffmpeg.v1",
                gpu=gpu,
                auto_detect_gpu=gpu is not None,
            )
            self._registrations.append(registration)
            log.info("registered batch app=%s", f"comfystream/ffmpeg-{tool}")
        if self.bee_url:
            registration = await register_runner(
                self.args.orchestrator,
                secret=self.args.orchSecret,
                runner_url=f"{runner}/publish",
                app="comfystream/ffmpeg-publish",
                mode="single-shot",
                capacity=1,
                price=PRICE,
                currency="usd",
                unit="fixed",
                label="ffmpeg-publish",
                metadata=registration_metadata("publish"),
                version="comfystream.ffmpeg.v1",
                gpu=gpu,
                auto_detect_gpu=gpu is not None,
            )
            self._registrations.append(registration)
            log.info("registered batch app=comfystream/ffmpeg-publish")

    async def close(self, _app: web.Application) -> None:
        for registration in self._registrations:
            try:
                await registration.close()
            except Exception:
                log.debug("failed to close batch registration", exc_info=True)
        self._registrations.clear()
        if self.supervisor is not None:
            await self.supervisor.close()
        if self.pool is not None:
            await self.pool.close()
        if self._http is not None:
            await self._http.close()
        await self.publisher.aclose()

    async def handle_health(self, _request: web.Request) -> web.Response:
        return web.json_response({"status": "ok", "tools": list(BATCH_TOOLS)})

    async def handle_schema(self, request: web.Request) -> web.Response:
        tool = request.match_info["tool"]
        if tool not in BATCH_TOOLS:
            raise web.HTTPNotFound()
        return web.Response(body=schema_bytes(tool), content_type="application/json")

    async def handle_publish_schema(self, _request: web.Request) -> web.Response:
        return web.Response(body=schema_bytes("publish"), content_type="application/json")

    async def handle_file(self, request: web.Request) -> web.Response:
        name = request.match_info["name"]
        if "/" in name or name.startswith("."):
            raise web.HTTPNotFound()
        path = OUTPUT_ROOT / name
        if not path.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(path)

    async def handle_batch(self, request: web.Request) -> web.Response:
        tool = request.match_info["tool"]
        if tool not in BATCH_TOOLS:
            return web.json_response(
                {"error": {"message": f"unknown tool {tool}", "status": 404}}, status=404
            )
        try:
            body = await request.json()
        except Exception:
            return web.json_response(
                {"error": {"message": "body must be JSON", "status": 400}}, status=400
            )
        if not isinstance(body, dict):
            return web.json_response(
                {"error": {"message": "body must be a JSON object", "status": 400}}, status=400
            )
        job_id = uuid.uuid4().hex
        work = OUTPUT_ROOT / job_id
        try:
            files = await _collect(self._http, tool, body, work)
            result = await self.pool.submit(
                {"tool": tool, "files": files, "params": body},
                timeout=180,
            )
        except PoolSaturatedError:
            return web.json_response(
                {"error": {"message": "ffmpeg workflow pool is saturated", "status": 503}},
                status=503,
            )
        except FfmpegJobError as exc:
            return web.json_response(
                {"error": {"message": str(exc), "status": exc.status}},
                status=exc.status,
            )
        except Exception as exc:
            log.exception("ffmpeg batch failed tool=%s", tool)
            return web.json_response(
                {"error": {"message": str(exc) or type(exc).__name__, "status": 500}},
                status=500,
            )
        if isinstance(result, dict) and "error" in result:
            status = int(result["error"].get("status") or 500)
            return web.json_response(result, status=status)
        payload = {
            key: value
            for key, value in result.items()
            if key not in {"data", "suffix", "content_type"}
        }
        data = result.get("data")
        if isinstance(data, (bytes, bytearray)):
            content_type = str(result.get("content_type") or "application/octet-stream")
            if self.hold is None:
                suffix = str(result.get("suffix") or ".bin")
                published = OUTPUT_ROOT / f"{job_id}{suffix}"
                published.write_bytes(bytes(data))
                file_info = _public_file(self.args.runner_url, published)
                file_info["content_type"] = content_type
            else:
                file_info = file_descriptor(self.hold, bytes(data), content_type, "")
            if tool == "extract-frame":
                payload["images"] = [file_info]
            elif tool == "loudnorm":
                payload["audio"] = file_info
            else:
                payload["video"] = file_info
        return web.json_response(payload)

    async def handle_publish(self, request: web.Request) -> web.Response:
        if self.hold is None:
            return web.json_response(
                {"error": {"message": "BEE_URL is unset", "status": 404}},
                status=404,
            )
        try:
            body = await request.json()
        except Exception:
            return web.json_response(
                {"error": {"message": "body must be JSON", "status": 400}}, status=400
            )
        if not isinstance(body, dict):
            return web.json_response(
                {"error": {"message": "body must be a JSON object", "status": 400}}, status=400
            )
        request_id = body.get("request_id")
        stamps = body.get("stamps")
        if not isinstance(request_id, str) or not request_id:
            return web.json_response(
                {"error": {"message": "request_id is required", "status": 400}}, status=400
            )
        if not isinstance(stamps, list):
            return web.json_response(
                {"error": {"message": "stamps must be a list", "status": 400}}, status=400
            )
        try:
            result = await self.hold.publish(
                request_id, [str(item) for item in stamps], self.publisher
            )
        except SwarmProtocolError as exc:
            return web.json_response(
                {"error": {"message": exc.message, "status": exc.status}}, status=exc.status
            )
        except SwarmPublishError as exc:
            return web.json_response(
                {"error": {"message": str(exc), "status": 502}},
                status=502,
            )
        return web.json_response(result)


def _batch_worker_init(client_kwargs: dict[str, Any]) -> None:
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    _pin_comfy_input_directory(str(client_kwargs.get("cwd") or WORKSPACE))
    from comfystream.batch_client import BatchComfyStreamClient

    client = BatchComfyStreamClient(**client_kwargs)
    loop.run_until_complete(client.ensure_started())

    def _queue(prompt: dict[str, Any]) -> Any:
        return loop.run_until_complete(client.queue_once(prompt))

    _batch_worker_execute.queue = _queue
    _batch_worker_execute.workspace = str(client_kwargs.get("cwd") or WORKSPACE)


def _pin_comfy_input_directory(workspace: str) -> None:
    """LoadImage lists the input folder during validation.

    The validating interpreter may not be this worker. comfystream_ffmpeg_paths
    hooks every later import of Comfy's folder_paths module and returns the
    workspace directories from that process too.
    """
    os.environ["COMFYUI_CWD"] = workspace
    import comfystream_ffmpeg_paths

    comfystream_ffmpeg_paths.install()


def _batch_worker_execute(job: dict[str, Any]) -> dict[str, Any]:
    from comfystream.ffmpeg.execute import FfmpegJobError, run_job

    queue = getattr(_batch_worker_execute, "queue", None)
    workspace = getattr(_batch_worker_execute, "workspace", WORKSPACE)
    if queue is None:
        return {"error": {"message": "Comfy client is not running", "status": 503}}
    try:
        result = run_job(
            queue, workspace, str(job["tool"]), dict(job["files"]), dict(job["params"])
        )
    except FfmpegJobError as exc:
        return {"error": {"message": str(exc), "status": exc.status}}
    except Exception as exc:
        log.exception("ffmpeg workflow failed")
        return {"error": {"message": str(exc) or type(exc).__name__, "status": 500}}
    data = result.get("bytes")
    if isinstance(data, (bytes, bytearray)):
        path = result.get("path")
        suffix = path.suffix if isinstance(path, Path) else ".bin"
        return {"content_type": result.get("content_type"), "data": bytes(data), "suffix": suffix}
    return {key: value for key, value in result.items() if key != "path"}


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    args = _parse_args()
    web.run_app(FfmpegRunner(args).build_app(), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
