#!/usr/bin/env python3
"""
Enhanced Stream Start Example

This example demonstrates how to start trickle streams with prompts directly
in the start request, supporting multiple architectures:
1. Regular Pipeline (shared single process)
2. ProcessPoolPipeline (dedicated worker per stream)
3. PromptWorkerPipeline (prompt-per-worker architecture)
"""

import asyncio
import json
import logging
import aiohttp
from typing import Dict, Any, List

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Example prompts
SIMPLE_INVERT_PROMPT = {
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
            "images": ["2", 0],
            "blur_radius": 5
        },
        "class_type": "ImageBlur"
    }
}

EDGE_DETECT_PROMPT = {
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
            "images": ["2", 0],
            "threshold1": 100,
            "threshold2": 200
        },
        "class_type": "EdgeDetect"
    }
}

async def start_stream_with_single_prompt(
    session: aiohttp.ClientSession,
    base_url: str,
    stream_id: str,
    prompt: Dict[str, Any],
    architecture: str = "processpool"
) -> Dict[str, Any]:
    """Start a stream with a single prompt using JSON string format."""
    
    # Architecture configuration
    arch_config = {
        "shared": {
            "dedicated_worker": False,
            "use_prompt_worker": False
        },
        "processpool": {
            "dedicated_worker": True,
            "use_prompt_worker": False
        },
        "prompt_worker": {
            "dedicated_worker": False,
            "use_prompt_worker": True
        }
    }
    
    config = arch_config.get(architecture, arch_config["processpool"])
    
    request_data = {
        "subscribe_url": f"http://localhost:3389/{stream_id}",
        "publish_url": f"http://localhost:3389/{stream_id}-output",
        "gateway_request_id": stream_id,
        "params": {
            "width": 512,
            "height": 512,
            "prompt": json.dumps(prompt),  # Single prompt as JSON string
            **config
        }
    }
    
    logger.info(f"Starting stream {stream_id} with {architecture} architecture and single prompt")
    
    async with session.post(f"{base_url}/stream/start", json=request_data) as response:
        result = await response.json()
        
        if response.status == 200:
            logger.info(f"✅ Stream {stream_id} started successfully")
            logger.info(f"   Architecture: {result['config']['architecture']}")
            logger.info(f"   Pipeline: {result['config']['pipeline_type']}")
            logger.info(f"   Prompts set: {result['config']['prompts_set']}")
        else:
            logger.error(f"❌ Failed to start stream {stream_id}: {result}")
            
        return result

async def start_stream_with_multiple_prompts(
    session: aiohttp.ClientSession,
    base_url: str,
    stream_id: str,
    prompts: List[Dict[str, Any]],
    architecture: str = "prompt_worker"
) -> Dict[str, Any]:
    """Start a stream with multiple prompts using array format."""
    
    # Architecture configuration
    arch_config = {
        "shared": {
            "dedicated_worker": False,
            "use_prompt_worker": False
        },
        "processpool": {
            "dedicated_worker": True,
            "use_prompt_worker": False
        },
        "prompt_worker": {
            "dedicated_worker": False,
            "use_prompt_worker": True
        }
    }
    
    config = arch_config.get(architecture, arch_config["prompt_worker"])
    
    request_data = {
        "subscribe_url": f"http://localhost:3389/{stream_id}",
        "publish_url": f"http://localhost:3389/{stream_id}-output",
        "gateway_request_id": stream_id,
        "params": {
            "width": 512,
            "height": 512,
            "prompts": prompts,  # Multiple prompts as array
            **config
        }
    }
    
    logger.info(f"Starting stream {stream_id} with {architecture} architecture and {len(prompts)} prompts")
    
    async with session.post(f"{base_url}/stream/start", json=request_data) as response:
        result = await response.json()
        
        if response.status == 200:
            logger.info(f"✅ Stream {stream_id} started successfully")
            logger.info(f"   Architecture: {result['config']['architecture']}")
            logger.info(f"   Pipeline: {result['config']['pipeline_type']}")
            logger.info(f"   Prompt count: {result['config']['prompt_count']}")
        else:
            logger.error(f"❌ Failed to start stream {stream_id}: {result}")
            
        return result

async def start_stream_with_default_prompt(
    session: aiohttp.ClientSession,
    base_url: str,
    stream_id: str,
    architecture: str = "processpool"
) -> Dict[str, Any]:
    """Start a stream without specifying prompts (uses default)."""
    
    # Architecture configuration
    arch_config = {
        "shared": {
            "dedicated_worker": False,
            "use_prompt_worker": False
        },
        "processpool": {
            "dedicated_worker": True,
            "use_prompt_worker": False
        },
        "prompt_worker": {
            "dedicated_worker": False,
            "use_prompt_worker": True
        }
    }
    
    config = arch_config.get(architecture, arch_config["processpool"])
    
    request_data = {
        "subscribe_url": f"http://localhost:3389/{stream_id}",
        "publish_url": f"http://localhost:3389/{stream_id}-output",
        "gateway_request_id": stream_id,
        "params": {
            "width": 512,
            "height": 512,
            # No prompt specified - will use default
            **config
        }
    }
    
    logger.info(f"Starting stream {stream_id} with {architecture} architecture and default prompt")
    
    async with session.post(f"{base_url}/stream/start", json=request_data) as response:
        result = await response.json()
        
        if response.status == 200:
            logger.info(f"✅ Stream {stream_id} started successfully")
            logger.info(f"   Architecture: {result['config']['architecture']}")
            logger.info(f"   Pipeline: {result['config']['pipeline_type']}")
            logger.info(f"   Using default prompt: {result['config']['prompts_set'] == False}")
        else:
            logger.error(f"❌ Failed to start stream {stream_id}: {result}")
            
        return result

async def check_workspace_info(
    session: aiohttp.ClientSession,
    base_url: str,
    stream_id: str
) -> Dict[str, Any]:
    """Check workspace configuration for a stream."""
    
    logger.info(f"Checking workspace info for stream {stream_id}")
    
    async with session.get(f"{base_url}/stream/{stream_id}/workspace-info") as response:
        result = await response.json()
        
        if response.status == 200:
            workspace_info = result['workspace_info']
            logger.info(f"✅ Workspace info for {stream_id}:")
            logger.info(f"   Workspace path: {workspace_info.get('workspace_path')}")
            logger.info(f"   Total workers: {workspace_info.get('total_workers')}")
            logger.info(f"   Active prompt: {workspace_info.get('active_prompt_id')}")
        else:
            logger.error(f"❌ Failed to get workspace info for {stream_id}: {result}")
            
        return result

async def stop_stream(
    session: aiohttp.ClientSession,
    base_url: str,
    stream_id: str
) -> Dict[str, Any]:
    """Stop a stream."""
    
    logger.info(f"Stopping stream {stream_id}")
    
    async with session.post(f"{base_url}/stream/{stream_id}/stop") as response:
        result = await response.json()
        
        if response.status == 200:
            logger.info(f"✅ Stream {stream_id} stopped successfully")
        else:
            logger.error(f"❌ Failed to stop stream {stream_id}: {result}")
            
        return result

async def main():
    """Main demonstration function."""
    base_url = "http://localhost:8000"
    
    async with aiohttp.ClientSession() as session:
        logger.info("🚀 Starting Enhanced Stream Start Demo")
        
        # Example 1: Start stream with single prompt (ProcessPool architecture)
        logger.info("\n" + "="*60)
        logger.info("Example 1: Single Prompt with ProcessPool Architecture")
        logger.info("="*60)
        
        result1 = await start_stream_with_single_prompt(
            session, base_url, "single_prompt_stream", 
            SIMPLE_INVERT_PROMPT, "processpool"
        )
        
        if result1.get('status') == 'success':
            await check_workspace_info(session, base_url, "single_prompt_stream")
            await asyncio.sleep(2)
            await stop_stream(session, base_url, "single_prompt_stream")
        
        # Example 2: Start stream with multiple prompts (Prompt-Worker architecture)
        logger.info("\n" + "="*60)
        logger.info("Example 2: Multiple Prompts with Prompt-Worker Architecture")
        logger.info("="*60)
        
        result2 = await start_stream_with_multiple_prompts(
            session, base_url, "multi_prompt_stream", 
            [SIMPLE_INVERT_PROMPT, BLUR_PROMPT, EDGE_DETECT_PROMPT], 
            "prompt_worker"
        )
        
        if result2.get('status') == 'success':
            await check_workspace_info(session, base_url, "multi_prompt_stream")
            await asyncio.sleep(2)
            await stop_stream(session, base_url, "multi_prompt_stream")
        
        # Example 3: Start stream with default prompt (Shared architecture)
        logger.info("\n" + "="*60)
        logger.info("Example 3: Default Prompt with Shared Architecture")
        logger.info("="*60)
        
        result3 = await start_stream_with_default_prompt(
            session, base_url, "default_prompt_stream", "shared"
        )
        
        if result3.get('status') == 'success':
            await check_workspace_info(session, base_url, "default_prompt_stream")
            await asyncio.sleep(2)
            await stop_stream(session, base_url, "default_prompt_stream")
        
        logger.info("\n🎉 Enhanced Stream Start Demo completed!")

if __name__ == "__main__":
    asyncio.run(main()) 