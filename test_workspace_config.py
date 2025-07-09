#!/usr/bin/env python3
"""
Test script to verify that PromptWorkerClient is using the correct workspace path.
"""

import asyncio
import logging
import sys
from pathlib import Path

# Add the src directory to the path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from comfystream.prompt_worker_client import PromptWorkerClient
from comfystream.prompt_worker_pipeline import PromptWorkerPipeline

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Test prompt
TEST_PROMPT = {
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

async def test_workspace_config():
    """Test that the workspace configuration is correctly passed to workers."""
    
    # Test with explicit workspace path
    workspace_path = "/workspace/ComfyUI"
    
    logger.info(f"Testing PromptWorkerClient with workspace: {workspace_path}")
    
    # Create client with explicit workspace
    client = PromptWorkerClient(
        cwd=workspace_path,
        disable_cuda_malloc=True,
        gpu_only=True,
        preview_method='none'
    )
    
    # Check workspace info before creating workers
    workspace_info = client.get_workspace_info()
    logger.info(f"Initial workspace info: {workspace_info}")
    
    # Set prompts (this creates workers)
    await client.set_prompts([TEST_PROMPT])
    
    # Check workspace info after creating workers
    workspace_info = client.get_workspace_info()
    logger.info(f"Workspace info after creating workers: {workspace_info}")
    
    # Test with PromptWorkerPipeline
    logger.info(f"Testing PromptWorkerPipeline with workspace: {workspace_path}")
    
    pipeline = PromptWorkerPipeline(
        width=512,
        height=512,
        cwd=workspace_path,
        disable_cuda_malloc=True,
        gpu_only=True,
        preview_method='none'
    )
    
    # Set prompts
    await pipeline.set_prompts([TEST_PROMPT])
    
    # Check workspace info
    pipeline_workspace_info = pipeline.get_workspace_info()
    logger.info(f"Pipeline workspace info: {pipeline_workspace_info}")
    
    # Cleanup
    await client.cleanup()
    await pipeline.cleanup()
    
    # Verify the workspace path is correct
    expected_workspace = "/workspace/ComfyUI"
    actual_workspace = pipeline_workspace_info.get('workspace_path')
    
    if actual_workspace == expected_workspace:
        logger.info("✅ SUCCESS: Workspace path is correctly configured!")
        return True
    else:
        logger.error(f"❌ FAILURE: Expected workspace '{expected_workspace}', got '{actual_workspace}'")
        return False

async def main():
    """Main test function."""
    logger.info("Starting workspace configuration test...")
    
    try:
        success = await test_workspace_config()
        if success:
            logger.info("All tests passed!")
            return 0
        else:
            logger.error("Tests failed!")
            return 1
    except Exception as e:
        logger.error(f"Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code) 