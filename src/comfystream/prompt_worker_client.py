"""
Prompt-per-Worker Client for ComfyStream

This module implements a new architecture where each prompt gets its own dedicated worker,
providing complete isolation and eliminating the issues with cancelling running prompts.
"""

import asyncio
import logging
import hashlib
from typing import Dict, Any, List, Optional, Union
from dataclasses import dataclass

from comfystream import tensor_cache
from comfystream.utils import convert_prompt

from comfy.api.components.schema.prompt import PromptDictInput
from comfy.cli_args_types import Configuration
from comfy.client.embedded_comfy_client import EmbeddedComfyClient

logger = logging.getLogger(__name__)


@dataclass
class PromptWorker:
    """Represents a dedicated worker for a specific prompt."""
    prompt_id: str
    prompt: Any  # Converted prompt
    client: EmbeddedComfyClient
    task: Optional[asyncio.Task] = None
    running: bool = False
    config_kwargs: Optional[Dict[str, Any]] = None


class PromptWorkerClient:
    """Client that manages dedicated workers per prompt for complete isolation."""
    
    def __init__(self, **kwargs):
        """Initialize the prompt-worker client.
        
        Args:
            **kwargs: Configuration arguments passed to worker clients
        """
        self.config_kwargs = kwargs
        self.workers: Dict[str, PromptWorker] = {}
        self.active_prompt_id: Optional[str] = None
        self._cleanup_lock = asyncio.Lock()
        
        logger.info("[PromptWorkerClient] Initialized with prompt-per-worker architecture")

    def _generate_prompt_id(self, prompt: Any) -> str:
        """Generate a unique ID for a prompt based on its content."""
        prompt_str = str(prompt)
        return hashlib.md5(prompt_str.encode()).hexdigest()[:12]

    async def set_prompts(self, prompts: List[PromptDictInput]):
        """Set prompts, creating dedicated workers for each.
        
        Args:
            prompts: List of prompt dictionaries
        """
        logger.info(f"[PromptWorkerClient] Setting {len(prompts)} prompts with dedicated workers")
        
        # Stop all existing workers
        await self.stop_all_workers()
        
        # Convert and create workers for each prompt
        for prompt_dict in prompts:
            converted_prompt = convert_prompt(prompt_dict)
            prompt_id = self._generate_prompt_id(converted_prompt)
            
            await self._create_worker(prompt_id, converted_prompt)
            
        # Set the first prompt as active (for backward compatibility)
        if prompts:
            first_prompt_id = list(self.workers.keys())[0]
            await self.set_active_prompt(first_prompt_id)
            
        logger.info(f"[PromptWorkerClient] Created {len(self.workers)} dedicated workers")

    async def update_prompts(self, prompts: List[PromptDictInput]):
        """Update prompts by replacing workers.
        
        Args:
            prompts: List of new prompt dictionaries
        """
        logger.info(f"[PromptWorkerClient] Updating to {len(prompts)} prompts")
        
        # This is much simpler with prompt-per-worker: just replace all workers
        await self.set_prompts(prompts)
        
        logger.info("[PromptWorkerClient] Prompts updated successfully")

    async def _create_worker(self, prompt_id: str, prompt: Any):
        """Create a dedicated worker for a prompt.
        
        Args:
            prompt_id: Unique identifier for the prompt
            prompt: Converted prompt object
        """
        # Log the workspace being used
        workspace_path = self.config_kwargs.get('cwd', 'NOT SET')
        logger.info(f"[PromptWorkerClient] Creating dedicated worker for prompt {prompt_id}")
        logger.info(f"[PromptWorkerClient] Worker {prompt_id} workspace: {workspace_path}")
        
        # Create dedicated ComfyUI client for this prompt
        config = Configuration(**self.config_kwargs)
        client = EmbeddedComfyClient(config, max_workers=1)  # Always 1 worker per prompt
        
        # Create worker object
        worker = PromptWorker(
            prompt_id=prompt_id,
            prompt=prompt,
            client=client,
            config_kwargs=self.config_kwargs.copy()
        )
        
        self.workers[prompt_id] = worker
        logger.info(f"[PromptWorkerClient] Worker {prompt_id} created successfully")

    async def set_active_prompt(self, prompt_id: str):
        """Set the active prompt for processing.
        
        Args:
            prompt_id: ID of the prompt to make active
        """
        if prompt_id not in self.workers:
            raise ValueError(f"Prompt {prompt_id} not found")
            
        # Stop current active worker if any
        if self.active_prompt_id and self.active_prompt_id != prompt_id:
            await self._stop_worker(self.active_prompt_id)
            
        # Start the new active worker
        await self._start_worker(prompt_id)
        self.active_prompt_id = prompt_id
        
        logger.info(f"[PromptWorkerClient] Set active prompt to {prompt_id}")

    async def _start_worker(self, prompt_id: str):
        """Start a specific worker.
        
        Args:
            prompt_id: ID of the worker to start
        """
        worker = self.workers[prompt_id]
        if worker.running:
            logger.warning(f"[PromptWorkerClient] Worker {prompt_id} already running")
            return
            
        logger.info(f"[PromptWorkerClient] Starting worker {prompt_id}")
        
        # Start the worker task
        worker.task = asyncio.create_task(self._run_worker(prompt_id))
        worker.running = True
        
        logger.info(f"[PromptWorkerClient] Worker {prompt_id} started")

    async def _stop_worker(self, prompt_id: str):
        """Stop a specific worker.
        
        Args:
            prompt_id: ID of the worker to stop
        """
        if prompt_id not in self.workers:
            return
            
        worker = self.workers[prompt_id]
        if not worker.running:
            return
            
        logger.info(f"[PromptWorkerClient] Stopping worker {prompt_id}")
        
        worker.running = False
        
        # Cancel the task
        if worker.task and not worker.task.done():
            worker.task.cancel()
            try:
                await worker.task
            except asyncio.CancelledError:
                pass
                
        # Stop the ComfyUI client
        if worker.client.is_running:
            try:
                await worker.client.__aexit__(None, None, None)
            except Exception as e:
                logger.warning(f"Error stopping worker {prompt_id}: {e}")
                
        logger.info(f"[PromptWorkerClient] Worker {prompt_id} stopped")

    async def _run_worker(self, prompt_id: str):
        """Run the worker loop for a specific prompt.
        
        Args:
            prompt_id: ID of the worker to run
        """
        worker = self.workers[prompt_id]
        logger.info(f"[PromptWorkerClient] Worker {prompt_id} starting execution loop")
        
        try:
            while worker.running:
                try:
                    await worker.client.queue_prompt(worker.prompt)
                except asyncio.CancelledError:
                    logger.info(f"[PromptWorkerClient] Worker {prompt_id} cancelled")
                    break
                except Exception as e:
                    logger.error(f"[PromptWorkerClient] Error in worker {prompt_id}: {e}")
                    # Don't break on errors, keep trying
                    await asyncio.sleep(1.0)
                    
        except asyncio.CancelledError:
            logger.info(f"[PromptWorkerClient] Worker {prompt_id} execution cancelled")
        finally:
            logger.info(f"[PromptWorkerClient] Worker {prompt_id} execution loop ended")

    async def stop_all_workers(self):
        """Stop all workers."""
        logger.info(f"[PromptWorkerClient] Stopping all {len(self.workers)} workers")
        
        # Stop all workers in parallel
        stop_tasks = [
            self._stop_worker(prompt_id) 
            for prompt_id in list(self.workers.keys())
        ]
        
        if stop_tasks:
            await asyncio.gather(*stop_tasks, return_exceptions=True)
            
        # Clear workers
        self.workers.clear()
        self.active_prompt_id = None
        
        logger.info("[PromptWorkerClient] All workers stopped")

    async def cleanup(self):
        """Clean up all resources."""
        async with self._cleanup_lock:
            logger.info("[PromptWorkerClient] Starting cleanup")
            
            await self.stop_all_workers()
            
            # Clear tensor cache
            await self.cleanup_queues()
            
            logger.info("[PromptWorkerClient] Cleanup complete")

    async def cleanup_queues(self):
        """Clean up tensor cache queues."""
        while not tensor_cache.image_inputs.empty():
            tensor_cache.image_inputs.get()

        while not tensor_cache.audio_inputs.empty():
            tensor_cache.audio_inputs.get()

        while not tensor_cache.image_outputs.empty():
            await tensor_cache.image_outputs.get()

        while not tensor_cache.audio_outputs.empty():
            await tensor_cache.audio_outputs.get()

    def put_video_input(self, frame):
        """Put video input into the tensor cache."""
        if tensor_cache.image_inputs.full():
            tensor_cache.image_inputs.get(block=True)
        tensor_cache.image_inputs.put(frame)
    
    def put_audio_input(self, frame):
        """Put audio input into the tensor cache."""
        tensor_cache.audio_inputs.put(frame)

    async def get_video_output(self):
        """Get video output from the tensor cache."""
        return await tensor_cache.image_outputs.get()
    
    async def get_audio_output(self):
        """Get audio output from the tensor cache."""
        return await tensor_cache.audio_outputs.get()

    def get_worker_status(self) -> Dict[str, Any]:
        """Get status of all workers."""
        return {
            'total_workers': len(self.workers),
            'active_prompt_id': self.active_prompt_id,
            'workers': {
                prompt_id: {
                    'running': worker.running,
                    'has_task': worker.task is not None,
                    'task_done': worker.task.done() if worker.task else True
                }
                for prompt_id, worker in self.workers.items()
            }
        }

    def get_prompt_list(self) -> List[str]:
        """Get list of available prompt IDs."""
        return list(self.workers.keys())

    async def switch_prompt(self, prompt_id: str):
        """Switch to a different prompt.
        
        Args:
            prompt_id: ID of the prompt to switch to
        """
        logger.info(f"[PromptWorkerClient] Switching to prompt {prompt_id}")
        await self.set_active_prompt(prompt_id)
        logger.info(f"[PromptWorkerClient] Switched to prompt {prompt_id}")

    async def get_available_nodes(self):
        """Get available nodes info from the active worker."""
        if not self.active_prompt_id or self.active_prompt_id not in self.workers:
            return {}
            
        try:
            from comfy.nodes.package import import_all_nodes_in_workspace
            nodes = import_all_nodes_in_workspace()

            worker = self.workers[self.active_prompt_id]
            prompt = worker.prompt
            
            nodes_info = {}
            
            # Get metadata for each node in the active prompt
            for node_id, node in prompt.items():  # type: ignore
                class_type = node.get('class_type')
                if class_type in nodes.NODE_CLASS_MAPPINGS and class_type not in ['LoadTensor', 'SaveTensor']:
                    node_class = nodes.NODE_CLASS_MAPPINGS[class_type]
                    if hasattr(node_class, 'INPUT_TYPES'):
                        input_types = node_class.INPUT_TYPES()
                        nodes_info[node_id] = {
                            'class_type': class_type,
                            'inputs': {}
                        }
                        
                        # Process required inputs
                        if 'required' in input_types:
                            for input_name, input_spec in input_types['required'].items():
                                nodes_info[node_id]['inputs'][input_name] = self._parse_input_spec(input_spec)
                        
                        # Process optional inputs
                        if 'optional' in input_types:
                            for input_name, input_spec in input_types['optional'].items():
                                nodes_info[node_id]['inputs'][input_name] = self._parse_input_spec(input_spec)
            
            return {0: nodes_info}  # Return in expected format
            
        except Exception as e:
            logger.error(f"Error getting available nodes: {e}")
            return {}

    def _parse_input_spec(self, input_spec):
        """Parse input specification to extract type and options."""
        if isinstance(input_spec, tuple) and len(input_spec) > 0:
            input_type = input_spec[0]
            
            if isinstance(input_type, list):
                # Combo/dropdown input
                return {
                    'type': 'combo',
                    'widget': 'combo',
                    'options': input_type,
                    'value': input_type[0] if input_type else None
                }
            elif isinstance(input_type, str):
                # String type input
                return {
                    'type': input_type.lower(),
                    'widget': 'text' if input_type.lower() == 'string' else input_type.lower(),
                    'value': input_spec[1] if len(input_spec) > 1 else None
                }
            else:
                # Other types (int, float, etc.)
                return {
                    'type': str(input_type).lower(),
                    'widget': str(input_type).lower(),
                    'value': input_spec[1] if len(input_spec) > 1 else 0
                }
        else:
            return {
                'type': 'unknown',
                'widget': 'text',
                'value': None
            } 

    def get_workspace_info(self) -> Dict[str, Any]:
        """Get workspace configuration information."""
        return {
            'workspace_path': self.config_kwargs.get('cwd', None),
            'config_keys': list(self.config_kwargs.keys()),
            'total_workers': len(self.workers),
            'active_prompt_id': self.active_prompt_id
        } 