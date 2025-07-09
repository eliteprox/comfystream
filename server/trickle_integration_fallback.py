"""
Integration module for trickle-app with ComfyStream.

This module provides simplified integration that works even if trickle-app 
is not fully available, allowing graceful degradation.
"""

import asyncio
import logging
from typing import Dict, Optional, Any
from comfystream.pipeline import Pipeline

logger = logging.getLogger(__name__)

class MockTrickleIntegration:
    """Mock implementation when trickle-app is not available."""
    
    def __init__(self):
        self.streams: Dict[str, Dict] = {}
        self.lock = asyncio.Lock()
    
    async def create_stream(self, request_id: str, subscribe_url: str, 
                          publish_url: str, pipeline: Pipeline, 
                          width: int = 512, height: int = 512) -> bool:
        """Mock stream creation with pipeline processing simulation."""
        async with self.lock:
            try:
                # Set initial resolution
                pipeline.width = width
                pipeline.height = height
                
                # Warm up the pipeline
                await pipeline.warm_video()
                
                self.streams[request_id] = {
                    'subscribe_url': subscribe_url,
                    'publish_url': publish_url,
                    'pipeline': pipeline,
                    'width': width,
                    'height': height,
                    'status': 'running',
                    'frame_count': 0
                }
                
                return True
                
            except Exception as e:
                logger.error(f"Mock: Failed to create stream {request_id}: {e}")
                await pipeline.cleanup()
                return False
    
    async def stop_stream(self, request_id: str) -> bool:
        """Mock stream stopping with proper cleanup."""
        async with self.lock:
            if request_id in self.streams:
                stream_info = self.streams[request_id]
                
                # Cleanup pipeline
                if 'pipeline' in stream_info:
                    await stream_info['pipeline'].cleanup()
                
                del self.streams[request_id]
                return True
            return False
    
    async def get_stream_status(self, request_id: str) -> Optional[Dict]:
        """Mock stream status."""
        async with self.lock:
            if request_id in self.streams:
                stream_info = self.streams[request_id]
                return {
                    'request_id': request_id,
                    'running': True,
                    'subscribe_url': stream_info['subscribe_url'],
                    'publish_url': stream_info['publish_url'],
                    'width': stream_info['width'],
                    'height': stream_info['height'],
                    'frame_count': 0
                }
            return None
    
    async def list_streams(self) -> Dict[str, Dict]:
        """Mock stream listing."""
        async with self.lock:
            result = {}
            for request_id, stream_info in self.streams.items():
                result[request_id] = {
                    'request_id': request_id,
                    'running': True,
                    'subscribe_url': stream_info['subscribe_url'],
                    'publish_url': stream_info['publish_url'],
                    'width': stream_info['width'],
                    'height': stream_info['height'],
                    'frame_count': 0
                }
            return result
    
    async def cleanup_all(self):
        """Mock cleanup with proper task cancellation."""
        async with self.lock:
            for request_id in list(self.streams.keys()):
                await self.stop_stream(request_id)
            self.streams.clear()

# Try to import the real trickle integration, fall back to mock
try:
    from trickle_integration import TrickleStreamManager
    logger.info("Using real trickle integration")
except ImportError as e:
    logger.warning(f"Trickle integration not available ({e}), using mock implementation")
    TrickleStreamManager = MockTrickleIntegration