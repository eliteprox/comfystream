import asyncio
import logging
import os
from pathlib import Path
from typing import Optional

from runner.live.pipelines import Pipeline
from runner.live.trickle import VideoFrame, VideoOutput

# We need to ensure comfystream.pipeline is importable.
# Depending on python path, it should be under src/comfystream/pipeline.py
# So import comfystream.pipeline works if src is in path.
from comfystream.pipeline import Pipeline as ComfyPipeline
from comfystream.scripts.setup_models import setup_directories, setup_model_files
from comfy_runner.params.params import ComfyParams

logger = logging.getLogger(__name__)


class ComfyRunner(Pipeline):
    """
    ComfyStream runner integration for ai-runner.
    """

    def __init__(self):
        self.pipe: Optional[ComfyPipeline] = None
        self.params: Optional[ComfyParams] = None
        self.request_queue: asyncio.Queue[str] = asyncio.Queue()
        self._shutdown = False

    async def initialize(self, **params):
        """Initialize the pipeline with parameters."""
        logger.info(f"Initializing ComfyRunner with params: {params}")
        self.params = ComfyParams(**params)

        # Determine workspace from env or default
        # Ideally this matches what prepare_models uses or what the container sets up
        workspace = os.environ.get("COMFY_UI_WORKSPACE")
        if not workspace:
             # Fallback or check if we need to set it
             workspace = os.path.expanduser("~/comfyui")

        logger.info(f"Using ComfyUI workspace: {workspace}")

        # Initialize the ComfyStream pipeline
        self.pipe = ComfyPipeline(
            width=self.params.width,
            height=self.params.height,
            # We can pass other kwargs if ComfyPipeline supports them and we have them
            # For now, just width/height are the main ones from BaseParams
        )

        # Start the pipeline (bootstrap)
        # ComfyPipeline.initialize() runs bootstrap if enabled
        await self.pipe.initialize()

        # Apply prompts if provided in params
        if self.params.prompts:
            logger.info("Applying initial prompts")
            await self.pipe.set_prompts(self.params.prompts)

    async def update_params(self, **params):
        """Update pipeline parameters."""
        if not self.pipe:
            return

        logger.info(f"Updating params: {params}")
        new_params = ComfyParams(**params)
        
        # Check for resolution changes
        # ComfyPipeline doesn't have a direct 'resize' method but updating prompts might trigger it
        # or we might need to set width/height on the pipe object.
        if (new_params.width != self.params.width or 
            new_params.height != self.params.height):
            self.pipe.width = new_params.width
            self.pipe.height = new_params.height
            # ComfyStream pipeline uses these properties during processing/warmup
            
        # Update prompts if they changed
        if new_params.prompts:
            await self.pipe.update_prompts(new_params.prompts)
            
        self.params = new_params

    async def put_video_frame(self, frame: VideoFrame, request_id: str):
        """Queue a video frame for processing."""
        if not self.pipe or self._shutdown:
            return

        # Queue the request ID to match with output later
        await self.request_queue.put(request_id)
        
        # Convert or pass frame. 
        # ComfyPipeline expects av.VideoFrame. 
        # runner.live.trickle.VideoFrame is likely NOT av.VideoFrame directly but might be compatible 
        # or we might need to rely on duck typing if it behaves like one.
        # However, checking scope-runner again, it seems they construct VideoFrame from tensor.
        # If input frame is trickle.VideoFrame, does it have to_ndarray?
        # If not, we might need to convert.
        # Assuming for now it is compatible or we are in a context where it works (e.g. sharing same underlying object).
        # Use simple pass through for now.
        
        await self.pipe.put_video_frame(frame)

    async def get_processed_video_frame(self) -> VideoOutput:
        """Get the next processed video frame."""
        if not self.pipe or self._shutdown:
            # Return None or raise? Interface expects VideoOutput.
            # If shutdown, maybe raise CancelledError or similar.
            await asyncio.sleep(0.1)
            raise asyncio.CancelledError("Pipeline is shutting down")

        # Get processed frame from ComfyStream
        processed_frame = await self.pipe.get_processed_video_frame()
        
        # Match with request ID
        try:
            request_id = await self.request_queue.get()
        except asyncio.QueueEmpty:
            logger.warning("Received processed frame but request queue is empty")
            request_id = "unknown"

        return VideoOutput(processed_frame, request_id)

    async def stop(self):
        """Stop the pipeline."""
        logger.info("Stopping ComfyRunner")
        self._shutdown = True
        if self.pipe:
            await self.pipe.cleanup()
            self.pipe = None

    @classmethod
    def prepare_models(cls):
        """Download and setup required models."""
        logger.info("Preparing ComfyStream models")
        
        # Determine workspace
        workspace = os.environ.get("COMFY_UI_WORKSPACE")
        
        # Check if we are in ai-runner environment where MODEL_DIR might be set
        if not workspace and os.environ.get("MODEL_DIR"):
            workspace = str(Path(os.environ["MODEL_DIR"]) / "ComfyUI")
            os.environ["COMFY_UI_WORKSPACE"] = workspace
            logger.info(f"Setting COMFY_UI_WORKSPACE to {workspace} from MODEL_DIR")
            
        if not workspace:
            workspace = os.path.expanduser("~/comfyui")
            
        workspace_path = Path(workspace)
        
        logger.info(f"Setup models in workspace: {workspace_path}")
        
        # Run setup
        setup_directories(workspace_path)
        setup_model_files(workspace_path)
        
        logger.info("Model preparation complete")

