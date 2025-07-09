"""
Prompt-Worker Pipeline for ComfyStream

This module implements a pipeline that uses the prompt-per-worker architecture,
providing complete isolation between prompts and easy switching.
"""

import av
import torch
import numpy as np
import asyncio
import logging
from typing import Any, Dict, Union, List, Optional

from comfystream.prompt_worker_client import PromptWorkerClient
from comfystream.server.utils import temporary_log_level

WARMUP_RUNS = 5

logger = logging.getLogger(__name__)


class PromptWorkerPipeline:
    """A pipeline that uses dedicated workers per prompt for complete isolation.
    
    This pipeline provides:
    - Complete isolation between prompts (each gets its own worker)
    - Easy switching between prompts without cancellation issues
    - Clean prompt updates without background processes
    - Support for multiple prompts with instant switching
    """
    
    def __init__(self, width: int = 512, height: int = 512, 
                 comfyui_inference_log_level: Optional[int] = None, **kwargs):
        """Initialize the prompt-worker pipeline.
        
        Args:
            width: Width of the video frames (default: 512)
            height: Height of the video frames (default: 512)
            comfyui_inference_log_level: The logging level for ComfyUI inference
            **kwargs: Additional arguments to pass to the PromptWorkerClient
        """
        self.client = PromptWorkerClient(**kwargs)
        self.width = width
        self.height = height

        self.video_incoming_frames = asyncio.Queue()
        self.audio_incoming_frames = asyncio.Queue()

        self.processed_audio_buffer = np.array([], dtype=np.int16)
        self._comfyui_inference_log_level = comfyui_inference_log_level
        
        logger.info("[PromptWorkerPipeline] Initialized with prompt-per-worker architecture")

    async def warm_video(self):
        """Warm up the video processing pipeline with dummy frames."""
        # Create dummy frame with the CURRENT resolution settings
        dummy_frame = av.VideoFrame()
        dummy_frame.side_data.input = torch.randn(1, self.height, self.width, 3)  # type: ignore
        
        logger.info(f"[PromptWorkerPipeline] Warming video pipeline with resolution {self.width}x{self.height}")

        for _ in range(WARMUP_RUNS):
            self.client.put_video_input(dummy_frame)
            await self.client.get_video_output()
            
        logger.info("[PromptWorkerPipeline] Video warmup complete")

    async def wait_for_first_processed_frame(self, timeout: float = 30.0) -> bool:
        """Wait for the first successful model-processed frame to ensure pipeline is ready.
        
        Args:
            timeout: Maximum time to wait in seconds
            
        Returns:
            True if first frame was processed successfully, False on timeout
        """
        logger.info("[PromptWorkerPipeline] Waiting for first processed frame...")
        
        start_time = asyncio.get_event_loop().time()
        
        # Create a test frame
        test_frame = av.VideoFrame()
        test_frame.side_data.input = torch.randn(1, self.height, self.width, 3)  # type: ignore
        
        while True:
            current_time = asyncio.get_event_loop().time()
            if current_time - start_time > timeout:
                logger.error(f"[PromptWorkerPipeline] Timeout waiting for first processed frame after {timeout}s")
                return False
                
            try:
                # Put test frame through pipeline
                self.client.put_video_input(test_frame)
                
                # Try to get output with a short timeout
                output = await asyncio.wait_for(self.client.get_video_output(), timeout=5.0)
                
                logger.info("[PromptWorkerPipeline] First processed frame received successfully - pipeline is ready")
                return True
                
            except asyncio.TimeoutError:
                logger.warning("[PromptWorkerPipeline] Timeout waiting for processed frame, retrying...")
                continue
            except Exception as e:
                logger.error(f"[PromptWorkerPipeline] Error processing test frame: {e}")
                await asyncio.sleep(1.0)
                continue

    async def warm_audio(self):
        """Warm up the audio processing pipeline with dummy frames."""
        dummy_frame = av.AudioFrame()
        dummy_frame.side_data.input = np.random.randint(-32768, 32767, int(48000 * 0.5), dtype=np.int16)  # type: ignore
        dummy_frame.sample_rate = 48000

        for _ in range(WARMUP_RUNS):
            self.client.put_audio_input(dummy_frame)
            await self.client.get_audio_output()

    async def set_prompts(self, prompts: Union[Dict[Any, Any], List[Dict[Any, Any]]]):
        """Set the processing prompts for the pipeline.
        
        Args:
            prompts: Either a single prompt dictionary or a list of prompt dictionaries
        """
        logger.info(f"[PromptWorkerPipeline] Setting prompts")
        if isinstance(prompts, list):
            await self.client.set_prompts(prompts)
        else:
            await self.client.set_prompts([prompts])

    async def update_prompts(self, prompts: Union[Dict[Any, Any], List[Dict[Any, Any]]]):
        """Update the existing processing prompts.
        
        This is much simpler with prompt-per-worker architecture - no cancellation issues!
        
        Args:
            prompts: Either a single prompt dictionary or a list of prompt dictionaries
        """
        logger.info(f"[PromptWorkerPipeline] Updating prompts (clean worker replacement)")
        
        if isinstance(prompts, list):
            await self.client.update_prompts(prompts)
        else:
            await self.client.update_prompts([prompts])
            
        logger.info(f"[PromptWorkerPipeline] Prompts updated successfully")

    async def switch_prompt(self, prompt_id: str):
        """Switch to a different prompt by ID.
        
        Args:
            prompt_id: ID of the prompt to switch to
        """
        logger.info(f"[PromptWorkerPipeline] Switching to prompt {prompt_id}")
        await self.client.switch_prompt(prompt_id)
        logger.info(f"[PromptWorkerPipeline] Switched to prompt {prompt_id}")

    def get_available_prompts(self) -> List[str]:
        """Get list of available prompt IDs."""
        return self.client.get_prompt_list()

    def get_active_prompt_id(self) -> Optional[str]:
        """Get the currently active prompt ID."""
        return self.client.active_prompt_id

    async def put_video_frame(self, frame: av.VideoFrame):
        """Queue a video frame for processing.
        
        Args:
            frame: The video frame to process
        """
        frame.side_data.input = self.video_preprocess(frame)  # type: ignore
        frame.side_data.skipped = True  # type: ignore
        self.client.put_video_input(frame)
        await self.video_incoming_frames.put(frame)

    async def put_audio_frame(self, frame: av.AudioFrame):
        """Queue an audio frame for processing.
        
        Args:
            frame: The audio frame to process
        """
        frame.side_data.input = self.audio_preprocess(frame)  # type: ignore
        frame.side_data.skipped = True  # type: ignore
        self.client.put_audio_input(frame)
        await self.audio_incoming_frames.put(frame)

    def video_preprocess(self, frame: av.VideoFrame) -> Union[torch.Tensor, np.ndarray]:
        """Preprocess a video frame before processing.
        
        Args:
            frame: The video frame to preprocess
            
        Returns:
            The preprocessed frame as a tensor or numpy array
        """
        frame_np = frame.to_ndarray(format="rgb24").astype(np.float32) / 255.0
        return torch.from_numpy(frame_np).unsqueeze(0)
    
    def audio_preprocess(self, frame: av.AudioFrame) -> Union[torch.Tensor, np.ndarray]:
        """Preprocess an audio frame before processing.
        
        Args:
            frame: The audio frame to preprocess
            
        Returns:
            The preprocessed frame as a tensor or numpy array
        """
        return frame.to_ndarray().ravel().reshape(-1, 2).mean(axis=1).astype(np.int16)
    
    def video_postprocess(self, output: Union[torch.Tensor, np.ndarray]) -> av.VideoFrame:
        """Postprocess a video frame after processing.
        
        Args:
            output: The processed output tensor or numpy array
            
        Returns:
            The postprocessed video frame
        """
        if isinstance(output, np.ndarray):
            output = torch.from_numpy(output)
            
        return av.VideoFrame.from_ndarray(
            (output * 255.0).clamp(0, 255).to(dtype=torch.uint8).squeeze(0).cpu().numpy()
        )

    def audio_postprocess(self, output: Union[torch.Tensor, np.ndarray]) -> av.AudioFrame:
        """Postprocess an audio frame after processing.
        
        Args:
            output: The processed output tensor or numpy array
            
        Returns:
            The postprocessed audio frame
        """
        if isinstance(output, torch.Tensor):
            output = output.cpu().numpy()
            
        return av.AudioFrame.from_ndarray(np.repeat(output, 2).reshape(1, -1))
    
    async def get_processed_video_frame(self) -> av.VideoFrame:
        """Get the next processed video frame.
        
        Returns:
            The processed video frame
        """
        if self._comfyui_inference_log_level is not None:
            async with temporary_log_level("comfy", self._comfyui_inference_log_level):
                out_tensor = await self.client.get_video_output()
        else:
            out_tensor = await self.client.get_video_output()
            
        frame = await self.video_incoming_frames.get()
        while frame.side_data.skipped:  # type: ignore
            frame = await self.video_incoming_frames.get()

        processed_frame = self.video_postprocess(out_tensor)
        
        # Copy timing information from original frame if available
        if frame.pts is not None:
            processed_frame.pts = frame.pts
        if frame.time_base is not None:
            processed_frame.time_base = frame.time_base
        
        return processed_frame

    async def get_processed_audio_frame(self) -> av.AudioFrame:
        """Get the next processed audio frame.
        
        Returns:
            The processed audio frame
        """
        frame = await self.audio_incoming_frames.get()
        if frame.samples > len(self.processed_audio_buffer):
            if self._comfyui_inference_log_level is not None:
                async with temporary_log_level("comfy", self._comfyui_inference_log_level):
                    out_tensor = await self.client.get_audio_output()
            else:
                out_tensor = await self.client.get_audio_output()
            self.processed_audio_buffer = np.concatenate([self.processed_audio_buffer, out_tensor])
        out_data = self.processed_audio_buffer[:frame.samples]
        self.processed_audio_buffer = self.processed_audio_buffer[frame.samples:]

        processed_frame = self.audio_postprocess(out_data)
        
        # Copy timing information from original frame if available
        if frame.pts is not None:
            processed_frame.pts = frame.pts
        if frame.time_base is not None:
            processed_frame.time_base = frame.time_base
        processed_frame.sample_rate = frame.sample_rate
        
        return processed_frame
    
    async def get_nodes_info(self) -> Dict[str, Any]:
        """Get information about all nodes in the current prompt including metadata.
        
        Returns:
            Dictionary containing node information
        """
        nodes_info = await self.client.get_available_nodes()
        # Ensure the return type is Dict[str, Any] by converting keys to strings
        if isinstance(nodes_info, dict):
            return {str(k): v for k, v in nodes_info.items()}
        return {}

    def get_workspace_info(self) -> Dict[str, Any]:
        """Get workspace configuration information for debugging.
        
        Returns:
            Dictionary containing workspace and configuration details
        """
        return self.client.get_workspace_info()

    def get_worker_status(self) -> Dict[str, Any]:
        """Get status of all workers.
        
        Returns:
            Dictionary containing worker status information
        """
        return self.client.get_worker_status()
    
    async def cleanup(self):
        """Clean up resources used by the pipeline."""
        logger.info("[PromptWorkerPipeline] Starting cleanup")
        await self.client.cleanup()
        logger.info("[PromptWorkerPipeline] Cleanup complete") 