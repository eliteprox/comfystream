"""
ProcessPoolExecutor-based Pipeline for Trickle Integration.

This module provides a ProcessPoolExecutor-based pipeline that enables true parallelization
for multiple ComfyUI workflows, specifically designed for trickle streaming integration.
"""

import av
import torch
import numpy as np
import asyncio
import logging
import time
import os
import multiprocessing as mp
from collections import OrderedDict
from typing import Any, Dict, Union, List, Optional

from comfystream.client import ComfyStreamClient
from comfystream.server.utils import temporary_log_level
from comfystream.frame_proxy import FrameProxy

WARMUP_RUNS = 5

logger = logging.getLogger(__name__)


class ProcessPoolPipeline:
    """A ProcessPoolExecutor-based pipeline for processing video frames using ComfyUI.
    
    This class provides a dedicated worker process per stream, enabling parallel processing
    of multiple streams without CUDA conflicts. Each stream gets its own isolated ComfyUI instance.
    """
    
    def __init__(self, 
                 width: int = 512, 
                 height: int = 512,
                 stream_id: str = "default",
                 comfyui_inference_log_level: Optional[int] = None, 
                 **kwargs):
        """Initialize the ProcessPoolExecutor-based pipeline with one dedicated worker.
        
        Args:
            width: Width of the video frames (default: 512)
            height: Height of the video frames (default: 512)
            stream_id: Unique identifier for this stream (default: "default")
            comfyui_inference_log_level: The logging level for ComfyUI inference
            **kwargs: Additional arguments to pass to the ComfyStreamClient
        """
        logger.info(f"[ProcessPoolPipeline] Initializing dedicated worker for stream {stream_id}")
        
        self.width = width
        self.height = height
        self.stream_id = stream_id
        self.max_workers = 1  # Always one worker per stream
        self._comfyui_inference_log_level = comfyui_inference_log_level
        
        # Initialize ProcessPoolExecutor-based client with single worker
        self.client = ComfyStreamClient(
            max_workers=1,  # One dedicated worker per stream
            width=width,
            height=height,
            **kwargs
        )
        
        # Frame tracking
        self.video_incoming_frames = asyncio.Queue()
        self.audio_incoming_frames = asyncio.Queue()
        self.input_frame_counter = 0
        self.running = True
        
        # Audio buffer for audio processing
        self.processed_audio_buffer = np.array([], dtype=np.int16)
        
        logger.info(f"[ProcessPoolPipeline] Initialized successfully")

    async def initialize(self, prompts):
        """Initialize the pipeline with prompts and warm up workers."""
        logger.info("[ProcessPoolPipeline] Initializing pipeline")
        await self.set_prompts(prompts)
        await self.warm_video()
        logger.info("[ProcessPoolPipeline] Pipeline initialized")

    async def warm_video(self):
        """Warm up the video processing pipeline with dummy frames."""
        logger.info(f"[ProcessPoolPipeline] Starting warmup with {WARMUP_RUNS} frames")
        
        for i in range(WARMUP_RUNS):
            dummy_tensor = torch.randn(1, self.height, self.width, 3)
            dummy_proxy = FrameProxy(
                tensor=dummy_tensor,
                width=self.width,
                height=self.height,
                pts=None,
                time_base=None
            )
            dummy_proxy.side_data.frame_id = -(i + 1)  # type: ignore  # Negative ID for warmup
            
            logger.debug(f"[ProcessPoolPipeline] Sending warmup frame {i+1}/{WARMUP_RUNS}")
            self.client.put_video_input(dummy_proxy)
            
            # Wait for output to ensure processing is working
            try:
                await asyncio.wait_for(self.client.get_video_output(), timeout=30.0)
                logger.debug(f"[ProcessPoolPipeline] Received warmup output {i+1}/{WARMUP_RUNS}")
            except asyncio.TimeoutError:
                logger.warning(f"[ProcessPoolPipeline] Warmup frame {i+1} timed out")
                
        logger.info("[ProcessPoolPipeline] Warmup complete")

    async def warm_audio(self):
        """Warm up the audio processing pipeline with dummy frames."""
        logger.info("[ProcessPoolPipeline] Audio warmup not implemented yet")
        pass

    async def set_prompts(self, prompts: Union[Dict[Any, Any], List[Dict[Any, Any]]]):
        """Set the processing prompts for the pipeline.
        
        Args:
            prompts: Either a single prompt dictionary or a list of prompt dictionaries
        """
        logger.info("[ProcessPoolPipeline] Setting prompts")
        if isinstance(prompts, list):
            await self.client.set_prompts(prompts)  # type: ignore
        else:
            await self.client.set_prompts([prompts])  # type: ignore

    async def update_prompts(self, prompts: Union[Dict[Any, Any], List[Dict[Any, Any]]]):
        """Update the existing processing prompts.
        
        Args:
            prompts: Either a single prompt dictionary or a list of prompt dictionaries
        """
        logger.info("[ProcessPoolPipeline] Updating prompts with enhanced worker management")
        
        # Check if we need to restart the worker due to parameter changes
        # For now, we always restart to ensure clean state, but this could be optimized
        logger.info(f"[ProcessPoolPipeline] Restarting worker to ensure clean state for stream {self.stream_id}")
        
        # Step 1: Stop processing and clear all queues
        self.running = False
        
        # Step 2: Cancel any running prompts and clear queues aggressively
        try:
            await asyncio.wait_for(self.client.cancel_running_prompts(), timeout=5.0)
            logger.info(f"[ProcessPoolPipeline] Cancelled running prompts for stream {self.stream_id}")
        except asyncio.TimeoutError:
            logger.warning(f"[ProcessPoolPipeline] Timeout cancelling prompts for stream {self.stream_id}")
        except Exception as e:
            logger.warning(f"[ProcessPoolPipeline] Error cancelling prompts: {e}")
        
        # Step 3: Aggressively clear all queues
        try:
            for i in range(3):
                await self.client.cleanup_queues()
                await self.client._aggressive_queue_cleanup()
                await asyncio.sleep(0.1)
            logger.info(f"[ProcessPoolPipeline] Cleared all queues for stream {self.stream_id}")
        except Exception as e:
            logger.warning(f"[ProcessPoolPipeline] Error clearing queues: {e}")
        
        # Step 4: Restart the worker to ensure completely fresh state
        await self.restart_worker()
        
        # Step 5: Set new prompts on the fresh worker
        logger.info(f"[ProcessPoolPipeline] Setting new prompts on fresh worker for stream {self.stream_id}")
        await self.set_prompts(prompts)
        
        # Step 6: Check if we need to warm up the pipeline
        # For ProcessPoolPipeline, we should warm up after prompt changes to ensure readiness
        logger.info(f"[ProcessPoolPipeline] Warming up pipeline after prompt update for stream {self.stream_id}")
        try:
            await asyncio.wait_for(self.warm_video(), timeout=60.0)
            logger.info(f"[ProcessPoolPipeline] Pipeline warmed up successfully for stream {self.stream_id}")
        except asyncio.TimeoutError:
            logger.warning(f"[ProcessPoolPipeline] Warmup timeout for stream {self.stream_id}")
        except Exception as e:
            logger.warning(f"[ProcessPoolPipeline] Error during warmup: {e}")
            
        logger.info(f"[ProcessPoolPipeline] Prompts updated successfully for stream {self.stream_id}")

    def requires_warmup(self, new_params: Dict[str, Any]) -> bool:
        """Check if parameter changes require pipeline warmup."""
        warmup_params = {'width', 'height', 'cwd', 'disable_cuda_malloc', 'gpu_only'}
        
        for param in warmup_params:
            if param in new_params:
                current_value = getattr(self, param, None)
                if current_value != new_params[param]:
                    logger.info(f"[ProcessPoolPipeline] Parameter {param} changed from {current_value} to {new_params[param]} - warmup required")
                    return True
        
        return False

    async def update_parameters(self, new_params: Dict[str, Any]):
        """Update pipeline parameters and restart worker if needed."""
        logger.info(f"[ProcessPoolPipeline] Updating parameters: {new_params}")
        
        needs_warmup = self.requires_warmup(new_params)
        
        # Update parameters
        for param, value in new_params.items():
            if hasattr(self, param):
                setattr(self, param, value)
                logger.info(f"[ProcessPoolPipeline] Updated {param} to {value}")
        
        # If parameters changed that require warmup, restart worker
        if needs_warmup:
            logger.info(f"[ProcessPoolPipeline] Parameter changes require worker restart and warmup")
            await self.restart_worker()
            await self.warm_video()
        
        logger.info(f"[ProcessPoolPipeline] Parameters updated successfully")

    async def put_video_frame(self, frame: av.VideoFrame):
        """Queue a video frame for processing.
        
        Args:
            frame: The video frame to process
        """
        current_time = time.time()
        
        # Preprocess frame
        frame.side_data.input = self.video_preprocess(frame)  # type: ignore
        frame.side_data.skipped = True  # type: ignore
        frame.side_data.frame_received_time = current_time  # type: ignore
        
        # Assign frame ID
        frame_id = self.input_frame_counter
        frame.side_data.frame_id = frame_id  # type: ignore
        frame.side_data.client_index = -1  # type: ignore
        self.input_frame_counter += 1
        
        # Send to worker processes
        self.client.put_video_input(frame)
        
        # Add to incoming frames queue
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
            output: The output tensor from processing
            
        Returns:
            The postprocessed video frame
        """
        # First ensure we have a tensor
        if isinstance(output, np.ndarray):
            output = torch.from_numpy(output)
        
        # Handle different tensor formats
        if len(output.shape) == 4:  # BCHW or BHWC format
            if output.shape[1] != 3:  # If BHWC format
                output = output.permute(0, 3, 1, 2)  # Convert BHWC to BCHW
            output = output[0]  # Take first image from batch -> CHW
        elif len(output.shape) != 3:  # Should be CHW at this point
            raise ValueError(f"Unexpected tensor shape after batch removal: {output.shape}")
        
        # Convert CHW to HWC for video frame
        output = output.permute(1, 2, 0)  # CHW -> HWC
        
        # Convert to numpy and create video frame
        return av.VideoFrame.from_ndarray(
            (output * 255.0).clamp(0, 255).to(dtype=torch.uint8).cpu().numpy(),
            format='rgb24'
        )

    def audio_postprocess(self, output: Union[torch.Tensor, np.ndarray]) -> av.AudioFrame:
        """Postprocess an audio frame after processing.
        
        Args:
            output: The output tensor from processing
            
        Returns:
            The postprocessed audio frame
        """
        # Convert to numpy if needed
        if isinstance(output, torch.Tensor):
            output_np = output.cpu().numpy()
        else:
            output_np = output
            
        return av.AudioFrame.from_ndarray(
            output_np.reshape(-1, 1), format="s16", layout="mono"
        )

    async def get_processed_video_frame(self) -> av.VideoFrame:
        """Get the next processed video frame.
        
        Returns:
            The processed video frame
        """
        # Get processed output first (like regular Pipeline)
        if self._comfyui_inference_log_level is not None:
            async with temporary_log_level("comfy", self._comfyui_inference_log_level):
                out_tensor = await self.client.get_video_output()
        else:
            out_tensor = await self.client.get_video_output()
        
        # Get input frame for timing
        frame = await self.video_incoming_frames.get()
        while frame.side_data.skipped:
            frame = await self.video_incoming_frames.get()
        
        # Process and return
        processed_frame = self.video_postprocess(out_tensor)
        
        # Copy timing information
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
        
        # Copy timing information
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
        return await self.client.get_available_nodes()

    async def cleanup(self):
        """Clean up resources used by the pipeline."""
        logger.info("[ProcessPoolPipeline] Starting cleanup")
        
        # Stop processing
        self.running = False
        
        # Clean up the client (this will gracefully shutdown workers)
        await self.client.cleanup()
        
        logger.info("[ProcessPoolPipeline] Cleanup complete")

    def get_worker_count(self) -> int:
        """Get the number of worker processes (always 1 for dedicated worker per stream)."""
        return 1

    def get_worker_status(self) -> Dict[str, Any]:
        """Get the status of the dedicated worker process."""
        return {
            'stream_id': self.stream_id,
            'worker_count': 1,
            'dedicated_worker': True,
            'running': self.running,
            'input_frame_counter': self.input_frame_counter
        }

    async def restart_worker(self):
        """Restart the dedicated worker process."""
        logger.info(f"[ProcessPoolPipeline] Restarting dedicated worker for stream {self.stream_id}")
        
        # Stop current worker
        await self.client.cleanup()
        
        # Reinitialize client with same parameters
        self.client = ComfyStreamClient(
            max_workers=1,
            width=self.width,
            height=self.height
        )
        
        # Reset state
        self.running = True
        
        logger.info(f"[ProcessPoolPipeline] Dedicated worker restarted for stream {self.stream_id}")

    # Backward compatibility alias
    async def restart_workers(self):
        """Restart the worker process (backward compatibility)."""
        await self.restart_worker() 