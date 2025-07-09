#!/usr/bin/env python3
"""
Example usage of ProcessPoolExecutor with trickle integration.

This example demonstrates:
1. Creating a ProcessPoolPipeline with dedicated worker per stream
2. Starting multiple parallel streams
3. Updating prompts for running streams
4. Monitoring worker status per stream
5. Restarting workers per stream
6. Proper cleanup
"""

import asyncio
import aiohttp
import json
import logging
from aiohttp import web
from comfystream.processpool_pipeline import ProcessPoolPipeline
from comfystream.server.trickle_api import setup_trickle_routes
from comfystream.prompts import DEFAULT_PROMPT

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Example prompts for demonstration
INVERT_PROMPT = {
    "1": {
        "inputs": {
            "images": ["2", 0]
        },
        "class_type": "SaveTensor"
    },
    "2": {
        "inputs": {},
        "class_type": "LoadTensor"
    },
    "3": {
        "inputs": {
            "images": ["2", 0]
        },
        "class_type": "ImageInvert"
    }
}

BLUR_PROMPT = {
    "1": {
        "inputs": {
            "images": ["3", 0]
        },
        "class_type": "SaveTensor"
    },
    "2": {
        "inputs": {},
        "class_type": "LoadTensor"
    },
    "3": {
        "inputs": {
            "images": ["2", 0],
            "blur_radius": 5
        },
        "class_type": "ImageBlur"
    }
}

async def create_app():
    """Create the trickle app with ProcessPoolPipeline."""
    app = web.Application()
    
    # Set up CORS
    from aiohttp_cors import setup as cors_setup
    cors = cors_setup(app, defaults={
        "*": {
            "allow_credentials": True,
            "expose_headers": "*",
            "allow_headers": "*",
            "allow_methods": "*"
        }
    })
    
    # Set app context for trickle integration
    app['workspace'] = '/path/to/comfyui/workspace'  # Update this path
    app['warm_pipeline'] = False
    
    # Setup trickle routes
    setup_trickle_routes(app, cors)
    
    return app

async def start_stream(session, stream_id, subscribe_url, publish_url):
    """Start a stream with dedicated worker."""
    payload = {
        "subscribe_url": subscribe_url,
        "publish_url": publish_url,
        "gateway_request_id": stream_id,
        "params": {
            "width": 512,
            "height": 512,
            "dedicated_worker": True,
            "prompt": json.dumps(INVERT_PROMPT)
        }
    }
    
    async with session.post('http://localhost:8000/stream/start', json=payload) as resp:
        result = await resp.json()
        logger.info(f"Stream {stream_id} start response: {result}")
        return resp.status == 200

async def update_stream_prompts(session, stream_id, new_prompt):
    """Update prompts for a running stream."""
    payload = {
        "prompt": json.dumps(new_prompt)
    }
    
    async with session.post(f'http://localhost:8000/stream/{stream_id}/update-prompts', json=payload) as resp:
        result = await resp.json()
        logger.info(f"Stream {stream_id} update prompts response: {result}")
        return resp.status == 200

async def get_stream_status(session, stream_id):
    """Get status of a stream."""
    async with session.get(f'http://localhost:8000/stream/{stream_id}/status') as resp:
        result = await resp.json()
        logger.info(f"Stream {stream_id} status: {result}")
        return result

async def get_worker_status(session, stream_id):
    """Get dedicated worker status for a stream."""
    async with session.get(f'http://localhost:8000/stream/{stream_id}/worker') as resp:
        result = await resp.json()
        logger.info(f"Stream {stream_id} worker status: {result}")
        return result

async def restart_worker(session, stream_id):
    """Restart dedicated worker for a stream."""
    async with session.post(f'http://localhost:8000/stream/{stream_id}/restart-worker') as resp:
        result = await resp.json()
        logger.info(f"Stream {stream_id} restart worker response: {result}")
        return resp.status == 200

async def stop_stream(session, stream_id):
    """Stop a stream."""
    async with session.post(f'http://localhost:8000/stream/{stream_id}/stop') as resp:
        result = await resp.json()
        logger.info(f"Stream {stream_id} stop response: {result}")
        return resp.status == 200

async def list_streams(session):
    """List all active streams."""
    async with session.get('http://localhost:8000/streams') as resp:
        result = await resp.json()
        logger.info(f"Active streams: {result}")
        return result

async def demo_client():
    """Demonstrate ProcessPoolPipeline trickle integration with prompt updates."""
    async with aiohttp.ClientSession() as session:
        logger.info("=== ProcessPoolPipeline Trickle Integration Demo ===")
        
        # Wait for server to be ready
        await asyncio.sleep(2)
        
        # 1. Start multiple streams with dedicated workers
        logger.info("\n1. Starting multiple streams with dedicated workers...")
        
        streams = [
            ("stream-1", "ws://localhost:8080/input1", "ws://localhost:8080/output1"),
            ("stream-2", "ws://localhost:8080/input2", "ws://localhost:8080/output2"),
            ("stream-3", "ws://localhost:8080/input3", "ws://localhost:8080/output3")
        ]
        
        for stream_id, sub_url, pub_url in streams:
            success = await start_stream(session, stream_id, sub_url, pub_url)
            if success:
                logger.info(f"✓ Stream {stream_id} started successfully")
            else:
                logger.error(f"✗ Failed to start stream {stream_id}")
        
        # 2. List all streams
        logger.info("\n2. Listing all active streams...")
        await list_streams(session)
        
        # 3. Get individual stream status
        logger.info("\n3. Getting individual stream status...")
        for stream_id, _, _ in streams:
            await get_stream_status(session, stream_id)
        
        # 4. Get dedicated worker status
        logger.info("\n4. Getting dedicated worker status...")
        for stream_id, _, _ in streams:
            await get_worker_status(session, stream_id)
        
        # 5. Update prompts for streams
        logger.info("\n5. Updating prompts for streams...")
        
        # Update stream-1 to use blur effect
        success = await update_stream_prompts(session, "stream-1", BLUR_PROMPT)
        if success:
            logger.info("✓ Stream stream-1 prompts updated to blur effect")
        else:
            logger.error("✗ Failed to update stream-1 prompts")
        
        # Update stream-2 to use a different invert configuration
        modified_invert = INVERT_PROMPT.copy()
        modified_invert["4"] = {
            "inputs": {
                "images": ["3", 0]
            },
            "class_type": "ImageInvert"
        }
        
        success = await update_stream_prompts(session, "stream-2", modified_invert)
        if success:
            logger.info("✓ Stream stream-2 prompts updated to modified invert")
        else:
            logger.error("✗ Failed to update stream-2 prompts")
        
        # 6. Verify prompt updates by checking stream status
        logger.info("\n6. Verifying prompt updates...")
        await asyncio.sleep(1)  # Give time for updates to apply
        
        for stream_id, _, _ in streams:
            await get_stream_status(session, stream_id)
        
        # 7. Restart a worker
        logger.info("\n7. Restarting worker for stream-1...")
        success = await restart_worker(session, "stream-1")
        if success:
            logger.info("✓ Worker restarted successfully for stream-1")
        else:
            logger.error("✗ Failed to restart worker for stream-1")
        
        # 8. Monitor for a bit
        logger.info("\n8. Monitoring streams for 10 seconds...")
        for i in range(5):
            await asyncio.sleep(2)
            logger.info(f"Monitor check {i+1}/5...")
            await list_streams(session)
        
        # 9. Stop all streams
        logger.info("\n9. Stopping all streams...")
        for stream_id, _, _ in streams:
            success = await stop_stream(session, stream_id)
            if success:
                logger.info(f"✓ Stream {stream_id} stopped successfully")
            else:
                logger.error(f"✗ Failed to stop stream {stream_id}")
        
        # 10. Verify cleanup
        logger.info("\n10. Verifying cleanup...")
        await asyncio.sleep(1)
        final_streams = await list_streams(session)
        if final_streams['count'] == 0:
            logger.info("✓ All streams cleaned up successfully")
        else:
            logger.warning(f"⚠ {final_streams['count']} streams still active")
        
        logger.info("\n=== Demo completed ===")

async def main():
    """Main function to run the demo."""
    # Create and start the trickle app
    app = await create_app()
    
    # Start the web server
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 8000)
    await site.start()
    
    logger.info("Trickle server started on http://0.0.0.0:8000")
    logger.info("Available endpoints:")
    logger.info("  POST /stream/start - Start stream")
    logger.info("  POST /stream/{id}/stop - Stop stream")
    logger.info("  GET /stream/{id}/status - Get stream status")
    logger.info("  POST /stream/{id}/update-prompts - Update stream prompts")
    logger.info("  GET /stream/{id}/worker - Get worker status")
    logger.info("  POST /stream/{id}/restart-worker - Restart worker")
    logger.info("  GET /streams - List all streams")
    logger.info("  GET /health - Health check")
    
    try:
        # Run the demo client
        await demo_client()
    finally:
        # Cleanup
        await runner.cleanup()

if __name__ == "__main__":
    asyncio.run(main()) 