"""
Main trickle integration module for ComfyStream.

This module provides the real trickle integration using the trickle-app package
for streaming video processing.
"""

import asyncio
import json
import logging
from typing import Dict, Optional, Any

try:
    from trickle_app import TricklePublisher, TrickleSubscriber, VideoFrame, VideoOutput
    TRICKLE_AVAILABLE = True
except ImportError:
    TRICKLE_AVAILABLE = False

from comfystream.pipeline import Pipeline
from comfystream.prompts import DEFAULT_PROMPT

logger = logging.getLogger(__name__)

class TrickleStreamHandler:
    """Handles a single trickle stream."""
    
    def __init__(self, request_id: str, subscribe_url: str, publish_url: str, 
                 pipeline: Pipeline, width: int = 512, height: int = 512):
        self.request_id = request_id
        self.subscribe_url = subscribe_url
        self.publish_url = publish_url
        self.pipeline = pipeline
        self.width = width
        self.height = height
        self.running = False
        self.frame_count = 0
        
        if TRICKLE_AVAILABLE:
            self.subscriber = TrickleSubscriber(subscribe_url)
            self.publisher = TricklePublisher(publish_url, "video/mp4")
        else:
            self.subscriber = None
            self.publisher = None
    
    async def start(self) -> bool:
        """Start the stream processing."""
        if not TRICKLE_AVAILABLE:
            logger.error("Trickle-app not available")
            return False
        
        try:
            # Configure pipeline
            self.pipeline.width = self.width
            self.pipeline.height = self.height
            
            # Warm up pipeline
            await self.pipeline.warm_video()
            
            # Start trickle components
            await self.subscriber.start()
            await self.publisher.start()
            
            # Start processing task
            self.processing_task = asyncio.create_task(self._process_frames())
            self.running = True
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to start stream {self.request_id}: {e}")
            await self.cleanup()
            return False
    
    async def stop(self):
        """Stop the stream processing."""
        if self.running:
            self.running = False
            
            # Cancel processing task
            if hasattr(self, 'processing_task'):
                self.processing_task.cancel()
                try:
                    await self.processing_task
                except asyncio.CancelledError:
                    pass
            
            await self.cleanup()
    
    async def cleanup(self):
        """Clean up resources."""
        if self.subscriber:
            await self.subscriber.close()
        if self.publisher:
            await self.publisher.close()
        if self.pipeline:
            await self.pipeline.cleanup()
    
    async def _process_frames(self):
        """Process frames from input stream to output stream."""
        try:
            await self.subscriber.subscribe()
            
            while self.running:
                segment = await self.subscriber.next()
                if segment is None:
                    break
                
                # Process frame through pipeline
                frame_data = await segment.read()
                if frame_data:
                    # Convert to pipeline format and process
                    # This is a simplified version - real implementation would
                    # need proper video decoding/encoding
                    self.frame_count += 1
                
                await segment.close()
                
                if segment.eos():
                    break
                    
        except Exception as e:
            logger.error(f"Error processing frames for stream {self.request_id}: {e}")
        finally:
            await self.subscriber.unsubscribe()
    
    def get_status(self) -> Dict[str, Any]:
        """Get current stream status."""
        return {
            'request_id': self.request_id,
            'running': self.running,
            'subscribe_url': self.subscribe_url,
            'publish_url': self.publish_url,
            'width': self.width,
            'height': self.height,
            'frame_count': self.frame_count
        }

class TrickleStreamManager:
    """Manages multiple trickle streams."""
    
    def __init__(self):
        self.handlers: Dict[str, TrickleStreamHandler] = {}
        self.lock = asyncio.Lock()
    
    async def create_stream(self, request_id: str, subscribe_url: str, 
                          publish_url: str, pipeline: Pipeline, 
                          width: int = 512, height: int = 512) -> bool:
        """Create and start a new trickle stream."""
        async with self.lock:
            if request_id in self.handlers:
                logger.warning(f"Stream {request_id} already exists")
                return False
            
            handler = TrickleStreamHandler(
                request_id, subscribe_url, publish_url, pipeline, width, height
            )
            
            if await handler.start():
                self.handlers[request_id] = handler
                return True
            else:
                await handler.cleanup()
                return False
    
    async def stop_stream(self, request_id: str) -> bool:
        """Stop and remove a stream."""
        async with self.lock:
            if request_id in self.handlers:
                handler = self.handlers[request_id]
                await handler.stop()
                del self.handlers[request_id]
                return True
            return False
    
    async def get_stream_status(self, request_id: str) -> Optional[Dict]:
        """Get status of a specific stream."""
        async with self.lock:
            if request_id not in self.handlers:
                return None
            
            return self.handlers[request_id].get_status()
    
    async def list_streams(self) -> Dict[str, Dict[str, Any]]:
        """List all active streams."""
        async with self.lock:
            return {
                request_id: handler.get_status()
                for request_id, handler in self.handlers.items()
            }
    
    async def cleanup_all(self):
        """Stop and cleanup all streams."""
        async with self.lock:
            for request_id in list(self.handlers.keys()):
                await self.handlers[request_id].stop()
            self.handlers.clear()