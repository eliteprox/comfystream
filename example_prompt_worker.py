"""
Example demonstrating the Prompt-per-Worker architecture

This example shows how to use the new PromptWorkerPipeline to:
1. Set multiple prompts with dedicated workers
2. Switch between prompts instantly
3. Update prompts without cancellation issues
4. Monitor worker status
"""

import asyncio
import json
import logging
from typing import Dict, Any

from comfystream.prompt_worker_pipeline import PromptWorkerPipeline
from comfystream.prompts import DEFAULT_PROMPT, INVERTED_PROMPT

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Define some example prompts
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
            "image": ["2", 0],
            "blur_radius": 5.0
        },
        "class_type": "ImageBlur"
    }
}

EDGE_PROMPT = {
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
            "image": ["2", 0],
            "edge_enhance": 2.0
        },
        "class_type": "ImageEdgeEnhance"
    }
}


async def demonstrate_prompt_worker_pipeline():
    """Demonstrate the prompt-per-worker architecture."""
    logger.info("=== Prompt-per-Worker Pipeline Demo ===")
    
    # Initialize pipeline
    pipeline = PromptWorkerPipeline(
        width=512,
        height=512,
        cwd="/path/to/comfyui",  # Replace with actual path
        disable_cuda_malloc=True,
        gpu_only=True
    )
    
    try:
        # 1. Set multiple prompts - each gets its own dedicated worker
        logger.info("\n1. Setting multiple prompts with dedicated workers...")
        
        prompts = [
            json.loads(DEFAULT_PROMPT),
            json.loads(INVERTED_PROMPT),
            BLUR_PROMPT,
            EDGE_PROMPT
        ]
        
        await pipeline.set_prompts(prompts)
        
        # Check worker status
        status = pipeline.get_worker_status()
        logger.info(f"Worker Status: {json.dumps(status, indent=2)}")
        
        # List available prompts
        available_prompts = pipeline.get_available_prompts()
        active_prompt = pipeline.get_active_prompt_id()
        logger.info(f"Available prompts: {available_prompts}")
        logger.info(f"Active prompt: {active_prompt}")
        
        # 2. Switch between prompts instantly
        logger.info("\n2. Switching between prompts...")
        
        for prompt_id in available_prompts[:3]:  # Switch to first 3 prompts
            logger.info(f"Switching to prompt: {prompt_id}")
            await pipeline.switch_prompt(prompt_id)
            
            # Verify the switch
            current_active = pipeline.get_active_prompt_id()
            logger.info(f"Now active: {current_active}")
            
            # Simulate some processing time
            await asyncio.sleep(1)
        
        # 3. Update prompts - this replaces all workers cleanly
        logger.info("\n3. Updating prompts (clean worker replacement)...")
        
        new_prompts = [
            json.loads(DEFAULT_PROMPT),
            BLUR_PROMPT
        ]
        
        await pipeline.update_prompts(new_prompts)
        
        # Check new status
        new_available = pipeline.get_available_prompts()
        new_active = pipeline.get_active_prompt_id()
        logger.info(f"After update - Available: {new_available}")
        logger.info(f"After update - Active: {new_active}")
        
        # 4. Demonstrate the ease of prompt management
        logger.info("\n4. Final worker status...")
        final_status = pipeline.get_worker_status()
        logger.info(f"Final Status: {json.dumps(final_status, indent=2)}")
        
        logger.info("\n✅ Demo completed successfully!")
        
    except Exception as e:
        logger.error(f"Error during demo: {e}")
        raise
    finally:
        # Clean up
        logger.info("\n5. Cleaning up...")
        await pipeline.cleanup()
        logger.info("Cleanup complete")


async def demonstrate_api_usage():
    """Demonstrate API usage with prompt-per-worker."""
    logger.info("\n=== API Usage Demo ===")
    
    # This would typically be done via HTTP requests
    api_examples = {
        "set_multiple_prompts": {
            "method": "POST",
            "url": "/stream/start",
            "body": {
                "subscribe_url": "ws://localhost:8080/subscribe",
                "publish_url": "ws://localhost:8080/publish",
                "gateway_request_id": "prompt_worker_stream",
                "params": {
                    "use_prompt_worker": True,
                    "prompts": [
                        json.loads(DEFAULT_PROMPT),
                        json.loads(INVERTED_PROMPT),
                        BLUR_PROMPT
                    ]
                }
            }
        },
        "list_prompts": {
            "method": "GET",
            "url": "/stream/prompt_worker_stream/prompts",
            "expected_response": {
                "status": "success",
                "prompts": ["abc123", "def456", "ghi789"],
                "active_prompt_id": "abc123",
                "total_prompts": 3
            }
        },
        "switch_prompt": {
            "method": "POST",
            "url": "/stream/prompt_worker_stream/switch-prompt",
            "body": {
                "prompt_id": "def456"
            },
            "expected_response": {
                "status": "success",
                "message": "Switched to prompt def456 for stream prompt_worker_stream",
                "active_prompt_id": "def456"
            }
        },
        "update_prompts": {
            "method": "POST",
            "url": "/stream/prompt_worker_stream/update-prompts",
            "body": {
                "prompts": [
                    json.loads(DEFAULT_PROMPT),
                    EDGE_PROMPT
                ]
            }
        }
    }
    
    logger.info("API Examples:")
    for name, example in api_examples.items():
        logger.info(f"\n{name.upper()}:")
        logger.info(f"  {example['method']} {example['url']}")
        if 'body' in example:
            logger.info(f"  Body: {json.dumps(example['body'], indent=4)}")
        if 'expected_response' in example:
            logger.info(f"  Expected Response: {json.dumps(example['expected_response'], indent=4)}")


def compare_architectures():
    """Compare the different pipeline architectures."""
    logger.info("\n=== Architecture Comparison ===")
    
    comparison = {
        "Regular Pipeline": {
            "prompt_isolation": "❌ Shared workers, cancellation issues",
            "prompt_switching": "❌ Must restart entire pipeline",
            "prompt_updates": "⚠️ Complex cancellation and cleanup",
            "worker_management": "🔄 Shared worker pool",
            "use_case": "Single prompt workflows"
        },
        "ProcessPool Pipeline": {
            "prompt_isolation": "⚠️ Stream isolation, but shared prompts per stream",
            "prompt_switching": "❌ Must restart worker",
            "prompt_updates": "⚠️ Worker restart required",
            "worker_management": "🔄 One worker per stream",
            "use_case": "Multi-stream isolation"
        },
        "PromptWorker Pipeline": {
            "prompt_isolation": "✅ Complete isolation per prompt",
            "prompt_switching": "✅ Instant switching between prompts",
            "prompt_updates": "✅ Clean worker replacement",
            "worker_management": "🎯 One worker per prompt",
            "use_case": "Multi-prompt workflows with instant switching"
        }
    }
    
    for arch_name, features in comparison.items():
        logger.info(f"\n{arch_name}:")
        for feature, description in features.items():
            logger.info(f"  {feature}: {description}")


async def main():
    """Main demo function."""
    try:
        # Run the pipeline demo
        await demonstrate_prompt_worker_pipeline()
        
        # Show API usage examples
        await demonstrate_api_usage()
        
        # Compare architectures
        compare_architectures()
        
    except Exception as e:
        logger.error(f"Demo failed: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main()) 