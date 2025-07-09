#!/usr/bin/env python3
"""
Basic ComfyStream Trickle Integration Example

This example demonstrates how to start and manage a trickle stream
using the ComfyStream API.
"""

import asyncio
import json
import aiohttp
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class BasicTrickleExample:
    """Basic trickle integration example."""
    
    def __init__(self, host="localhost", port=8889):
        self.host = host
        self.port = port
        self.base_url = f"http://{host}:{port}"
        
    async def start_stream(self, request_id: str, subscribe_url: str, 
                          publish_url: str, width: int = 512, height: int = 512):
        """Start a basic trickle stream."""
        
        # Basic workflow: LoadTensor -> SaveTensor
        workflow = {
            "1": {
                "inputs": {
                    "images": ["2", 0]
                },
                "class_type": "SaveTensor"
            },
            "2": {
                "inputs": {},
                "class_type": "LoadTensor"
            }
        }
        
        request_data = {
            "subscribe_url": subscribe_url,
            "publish_url": publish_url,
            "gateway_request_id": request_id,
            "params": {
                "width": width,
                "height": height,
                "prompt": json.dumps(workflow)
            }
        }
        
        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(
                    f"{self.base_url}/stream/start",
                    json=request_data,
                    headers={'Content-Type': 'application/json'}
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        logger.info(f"Stream started successfully: {result}")
                        return True
                    else:
                        error_text = await response.text()
                        logger.error(f"Failed to start stream: {error_text}")
                        return False
                        
            except Exception as e:
                logger.error(f"Error starting stream: {e}")
                return False
    
    async def get_stream_status(self, request_id: str):
        """Get the status of a stream."""
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(f"{self.base_url}/stream/{request_id}/status") as response:
                    if response.status == 200:
                        result = await response.json()
                        logger.info(f"Stream status: {result}")
                        return result
                    else:
                        logger.error(f"Failed to get stream status: {response.status}")
                        return None
                        
            except Exception as e:
                logger.error(f"Error getting stream status: {e}")
                return None
    
    async def stop_stream(self, request_id: str):
        """Stop a stream."""
        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(f"{self.base_url}/stream/{request_id}/stop") as response:
                    if response.status == 200:
                        result = await response.json()
                        logger.info(f"Stream stopped successfully: {result}")
                        return True
                    else:
                        logger.error(f"Failed to stop stream: {response.status}")
                        return False
                        
            except Exception as e:
                logger.error(f"Error stopping stream: {e}")
                return False
    
    async def list_streams(self):
        """List all active streams."""
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(f"{self.base_url}/streams") as response:
                    if response.status == 200:
                        result = await response.json()
                        logger.info(f"Active streams: {result}")
                        return result
                    else:
                        logger.error(f"Failed to list streams: {response.status}")
                        return {}
                        
            except Exception as e:
                logger.error(f"Error listing streams: {e}")
                return {}

async def main():
    """Main example function."""
    client = BasicTrickleExample()
    
    # Example stream configuration
    request_id = "basic-example-stream"
    subscribe_url = "http://localhost:5678/input"
    publish_url = "http://localhost:5679/output"
    
    logger.info("Starting basic trickle integration example...")
    
    # Start stream
    if await client.start_stream(request_id, subscribe_url, publish_url):
        logger.info("Stream started successfully")
        
        # Wait a bit
        await asyncio.sleep(2)
        
        # Check status
        await client.get_stream_status(request_id)
        
        # List all streams
        await client.list_streams()
        
        # Stop stream
        await client.stop_stream(request_id)
        
        logger.info("Example completed successfully")
    else:
        logger.error("Failed to start stream")

if __name__ == "__main__":
    asyncio.run(main())