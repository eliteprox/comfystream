"""Live scale, rotate, and flip through the streaming Pipeline.

The graph is LoadTensor, ImageRotate, ImageScale, optional ImageFlip, and
SaveTensor. Those image nodes are Comfy core. The worker process is the
realtime supervisor's ProcessPoolExecutor, the same one as the other pipelines.
"""

from __future__ import annotations

import asyncio
from fractions import Fraction
from typing import Any

import av
import numpy as np

from comfystream.ffmpeg.workflows import live_filter
from comfystream.pipeline import Pipeline
from comfystream.realtime.params import ParamError
from comfystream.realtime.spec import RealtimePipelineSpec

ROTATIONS = (0, 90, 180, 270)
FIRST_FRAME_TIMEOUT_S = 120.0


class FfmpegWorkflowBackend:
    """Transform backend. One loaded Pipeline, frames in and frames out."""

    kind = "transform"
    swarm_audio = True

    def __init__(self, spec: RealtimePipelineSpec):
        options = spec.options
        self.width = int(options.get("width", 1280))
        self.height = int(options.get("height", 720))
        self.workspace = str(options.get("workspace", "/workspace/ComfyUI"))
        self.batch = 1
        declared = options.get("params")
        self.param_names = (
            [str(name) for name in declared] if declared else ["height", "rotation", "flip"]
        )
        self.params = self.defaults()
        self.pipeline: Pipeline | None = None
        self.applied: dict[str, Any] | None = None
        self._frame_lock = asyncio.Lock()

    def defaults(self) -> dict[str, Any]:
        return {"height": self.height, "rotation": 0, "flip": "none"}

    def reset(self) -> None:
        self.params = self.defaults()

    def render(self) -> dict[str, Any]:
        rotation = int(self.params.get("rotation") or 0)
        flip = str(self.params.get("flip") or "none")
        if rotation not in ROTATIONS:
            raise ParamError("invalid_param", "rotation", "rotation must be 0, 90, 180, or 270")
        if flip not in {"none", "horizontal", "vertical"}:
            raise ParamError("invalid_param", "flip", "flip must be none, horizontal, or vertical")
        return live_filter(int(self.params.get("height") or self.height), rotation, flip)

    async def load(self) -> None:
        self.pipeline = Pipeline(
            width=self.width,
            height=self.height,
            cwd=self.workspace,
            disable_cuda_malloc=True,
            gpu_only=False,
            preview_method="none",
            blacklist_custom_nodes=["ComfyUI-Manager"],
            bootstrap_default_prompt=False,
            max_workers=1,
        )
        self.applied = self.render()
        await self.pipeline.apply_prompts([self.applied], skip_warmup=True)
        frame = av.VideoFrame.from_ndarray(
            np.zeros((self.height, self.width, 3), dtype=np.uint8),
            format="rgb24",
        )
        frame.pts = 0
        frame.time_base = Fraction(1, 30)
        await asyncio.wait_for(self.process([frame]), timeout=FIRST_FRAME_TIMEOUT_S)
        await self.idle()

    async def scrub(self, session_id: str = "") -> None:
        self.reset()
        await self.apply(self.defaults(), session_id)

    async def idle(self) -> None:
        if self.pipeline is not None and self.pipeline.are_prompts_running():
            await self.pipeline.stop_streaming()

    async def apply(self, params: dict[str, Any], session_id: str = "") -> None:
        for key, value in params.items():
            if key in self.param_names:
                self.params[key] = value
        rendered = self.render()
        if self.pipeline is None or rendered == self.applied:
            self.applied = rendered
            return
        async with self._frame_lock:
            await self.pipeline.apply_prompts([rendered], skip_warmup=True)
        self.applied = rendered
        self.height = int(self.params.get("height") or self.height)

    def describe(self) -> dict[str, Any]:
        return {
            "workflow": "ffmpeg-live-filter",
            "height": self.params.get("height"),
            "rotation": self.params.get("rotation"),
            "flip": self.params.get("flip"),
        }

    async def process(
        self, frames: list[av.VideoFrame], session_id: str = ""
    ) -> list[av.VideoFrame]:
        if self.pipeline is None:
            raise RuntimeError("pipeline not loaded")
        async with self._frame_lock:
            for frame in frames:
                await self.pipeline.put_video_frame(frame)
            if not self.pipeline.are_prompts_running():
                await self.pipeline.start_streaming()
            return [await self.pipeline.get_processed_video_frame() for _ in frames]
