"""One-shot job handler for ComfyStream BYOC server.

Handles synchronous text-to-image, image-to-image, and text-to-video jobs
by executing ComfyUI workflows through the shared pipeline.
"""

import asyncio
import base64
import copy
import importlib
import io
import logging
from typing import Any, Dict, List

import av
import numpy as np
import torch
from aiohttp import web
from PIL import Image

from comfystream.modalities import detect_io_points, get_convertible_node_keys
from comfystream.pipeline_state import PipelineState
from comfystream.utils import create_load_tensor_node, create_save_tensor_node

logger = logging.getLogger(__name__)

DEFAULT_JOB_TIMEOUT = 120
MAX_JOB_TIMEOUT = 600


class JobHandler:
    """Orchestrates one-shot ComfyUI job execution through the shared pipeline.

    Uses the same ComfyStreamClient as the streaming pipeline but operates
    in a one-shot mode: set prompts, feed input (if needed), collect output,
    clean up.
    """

    def __init__(
        self,
        frame_processor,
    ):
        self._frame_processor = frame_processor
        self._job_lock = asyncio.Lock()

    @property
    def pipeline(self):
        return self._frame_processor.pipeline

    def _is_stream_active(self) -> bool:
        """Check if a streaming session is currently active."""
        processor = self._frame_processor._stream_processor
        if processor is None:
            return False
        server = processor.server
        return server.current_client is not None and server.state.active_client

    async def handle_process_request(self, request: web.Request) -> web.Response:
        """aiohttp handler for POST /process."""
        try:
            data = await request.json()
        except Exception as e:
            return web.json_response(
                {"status": "error", "message": f"Invalid JSON: {e}"},
                status=400,
            )

        try:
            result = await self.execute_job(data)
            return web.json_response(result)
        except web.HTTPConflict:
            return web.json_response(
                {"status": "error", "message": "Another job is already running"},
                status=409,
            )
        except web.HTTPServiceUnavailable:
            return web.json_response(
                {"status": "error", "message": "Pipeline is busy with a streaming session"},
                status=503,
            )
        except asyncio.TimeoutError:
            return web.json_response(
                {"status": "error", "message": "Job execution timed out"},
                status=504,
            )
        except ValueError as e:
            return web.json_response(
                {"status": "error", "message": str(e)},
                status=400,
            )
        except Exception as e:
            logger.exception("Job execution failed")
            return web.json_response(
                {"status": "error", "message": str(e)},
                status=500,
            )

    async def execute_job(self, request_data: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a one-shot ComfyUI job synchronously."""
        if self._job_lock.locked():
            raise web.HTTPConflict()

        async with self._job_lock:
            return await self._execute_job_locked(request_data)

    async def _execute_job_locked(self, request_data: Dict[str, Any]) -> Dict[str, Any]:
        """Execute the job while holding the lock."""
        pipeline = self.pipeline
        if pipeline is None:
            raise RuntimeError("Pipeline not initialized yet. Wait for server startup to complete.")

        if self._is_stream_active():
            raise web.HTTPServiceUnavailable()

        # Also check pipeline state
        if pipeline.state == PipelineState.STREAMING:
            raise web.HTTPServiceUnavailable()

        # Parse request
        workflow = request_data.get("workflow")
        if not workflow or not isinstance(workflow, dict):
            raise ValueError("'workflow' is required and must be a dict")

        images = request_data.get("images", {})
        width = int(request_data.get("width", pipeline.width))
        height = int(request_data.get("height", pipeline.height))
        timeout = min(
            int(request_data.get("timeout", DEFAULT_JOB_TIMEOUT)),
            MAX_JOB_TIMEOUT,
        )

        # Convert workflow for one-shot execution (relaxed input validation)
        converted_workflow = _convert_workflow_for_job(workflow)

        # Detect I/O capabilities from the converted workflow
        io_caps = detect_io_points(converted_workflow)
        needs_video_input = io_caps["video"]["input"]
        produces_video_output = io_caps["video"]["output"]
        produces_text_output = io_caps["text"]["output"]

        if needs_video_input and not images:
            raise ValueError("Workflow requires image input but no 'images' provided")

        if not produces_video_output and not produces_text_output:
            raise ValueError("Workflow does not produce any capturable output (video or text)")

        # Save original state
        original_width = pipeline.width
        original_height = pipeline.height
        client = pipeline.client

        try:
            pipeline.width = width
            pipeline.height = height

            # Set prompts directly on the client, bypassing Pipeline.set_prompts()
            # which calls convert_prompt() with strict input validation that
            # rejects text-to-image workflows without input nodes.
            await _set_job_prompts(client, converted_workflow)

            # Feed input image if workflow needs it
            if needs_video_input and images:
                input_tensor = _decode_input_image(images, height, width)
                frame = av.VideoFrame()
                frame.side_data.input = input_tensor
                client.put_video_input(frame)

            # Collect outputs with timeout
            result_images = []
            result_text = None

            async def _collect_outputs():
                nonlocal result_images, result_text

                if produces_video_output:
                    output_tensor = await client.get_video_output()
                    result_images = _encode_output_tensor(output_tensor)

                if produces_text_output:
                    # Give text output a moment to arrive after video
                    await asyncio.sleep(0.1)
                    text = await client.get_text_output()
                    if text:
                        result_text = text

            await asyncio.wait_for(_collect_outputs(), timeout=timeout)

            response: Dict[str, Any] = {"status": "success"}
            if result_images:
                response["images"] = result_images
            if result_text is not None:
                response["text"] = result_text

            return response

        finally:
            # Clean up: stop runner, clear queues, restore state
            try:
                await client.stop_runner()
                await client.cleanup_queues()
                client.current_prompts = []
            except Exception:
                logger.warning("Error during job cleanup", exc_info=True)

            pipeline.width = original_width
            pipeline.height = original_height
            # Reset cached modalities since we changed prompts
            pipeline._cached_modalities = None
            pipeline._cached_io_capabilities = None


async def _set_job_prompts(client, converted_workflow: Dict[str, Any]) -> None:
    """Set prompts directly on the client for one-shot execution.

    This bypasses Pipeline.set_prompts() and ComfyStreamClient.set_prompts()
    because those call convert_prompt() which enforces streaming constraints
    (e.g., requiring input nodes). One-shot workflows like text-to-image
    may have no input nodes at all.
    """
    await client.stop_runner()
    await client.cleanup_queues()

    # Set prompts directly (already converted by our job-specific converter)
    client.current_prompts = [converted_workflow]

    # Start the runner
    await client.set_running(True)

    logger.info("Job prompts set and runner started")


def _convert_workflow_for_job(workflow: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a ComfyUI workflow for one-shot job execution.

    Unlike convert_prompt() for streaming, this does NOT require input nodes.
    It converts:
      - SaveImage/PreviewImage -> SaveTensor (to capture output)
      - LoadImage/PrimaryInputLoadImage -> LoadTensor (if present, for image-to-image)
    """
    try:
        importlib.import_module("comfy.api.components.schema.prompt_node")
    except Exception:
        pass

    from comfy.api.components.schema.prompt import Prompt

    Prompt.validate(workflow)
    workflow = copy.deepcopy(workflow)

    convertible = get_convertible_node_keys(workflow)

    # Convert primary input nodes (PrimaryInputLoadImage -> LoadTensor)
    for key in convertible.get("PrimaryInputLoadImage", []):
        workflow[key] = create_load_tensor_node()

    # Convert LoadImage -> LoadTensor only if no primary input and exactly one LoadImage
    primary_count = len(convertible.get("PrimaryInputLoadImage", []))
    load_image_keys = convertible.get("LoadImage", [])
    if primary_count == 0 and len(load_image_keys) == 1:
        workflow[load_image_keys[0]] = create_load_tensor_node()

    # Convert output nodes (SaveImage/PreviewImage -> SaveTensor)
    for key in convertible.get("PreviewImage", []) + convertible.get("SaveImage", []):
        node = workflow[key]
        workflow[key] = create_save_tensor_node(node["inputs"])

    return workflow


def _decode_input_image(
    images: Dict[str, str],
    height: int,
    width: int,
) -> torch.Tensor:
    """Decode base64 image(s) to a tensor for pipeline input.

    Returns tensor of shape [1, H, W, 3] with float32 values in range [0, 1].
    """
    image_b64 = next(iter(images.values()))

    # Strip data URI prefix if present (e.g. "data:image/png;base64,...")
    if "," in image_b64:
        image_b64 = image_b64.split(",", 1)[1]

    image_bytes = base64.b64decode(image_b64)
    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    image = image.resize((width, height), Image.LANCZOS)

    image_np = np.array(image).astype(np.float32) / 255.0
    return torch.from_numpy(image_np).unsqueeze(0)


def _encode_output_tensor(tensor) -> List[Dict[str, str]]:
    """Encode output tensor to a list of base64 PNG image dicts.

    Handles both single images (batch=1) and multi-frame video (batch>1).
    """
    if isinstance(tensor, np.ndarray):
        tensor = torch.from_numpy(tensor)

    tensor = tensor.cpu()

    # Ensure 4D: [B, H, W, C]
    if tensor.dim() == 3:
        tensor = tensor.unsqueeze(0)

    results = []
    for i in range(tensor.shape[0]):
        frame = tensor[i]
        frame_np = (frame * 255.0).clamp(0, 255).to(dtype=torch.uint8).numpy()

        image = Image.fromarray(frame_np)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        b64 = base64.b64encode(buffer.getvalue()).decode("utf-8")

        results.append({"url": f"data:image/png;base64,{b64}"})

    return results
