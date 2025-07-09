import asyncio
from typing import List, Dict, Any
import logging

from comfystream import tensor_cache
from comfystream.utils import convert_prompt

from comfy.api.components.schema.prompt import PromptDictInput
from comfy.cli_args_types import Configuration
from comfy.client.embedded_comfy_client import EmbeddedComfyClient

logger = logging.getLogger(__name__)


class ComfyStreamClient:
    def __init__(self, max_workers: int = 1, **kwargs):
        config = Configuration(**kwargs)
        self.comfy_client = EmbeddedComfyClient(config, max_workers=max_workers)
        self.running_prompts = {} # To be used for cancelling tasks
        self.current_prompts = []
        self._cleanup_lock = asyncio.Lock()
        self._prompt_update_lock = asyncio.Lock()
        
        # Store configuration for potential restart
        self._config_kwargs = kwargs
        self._max_workers = max_workers

    async def set_prompts(self, prompts: List[PromptDictInput]):
        await self.cancel_running_prompts()
        self.current_prompts = [convert_prompt(prompt) for prompt in prompts]
        for idx in range(len(self.current_prompts)):
            task = asyncio.create_task(self.run_prompt(idx))
            self.running_prompts[idx] = task

    async def update_prompts(self, prompts: List[PromptDictInput]):
        async with self._prompt_update_lock:
            # Always cancel running prompts before updating to ensure clean transition
            logger.info("Cancelling running prompts before update")
            await self.cancel_running_prompts()
            
            # Aggressively clear all input/output queues multiple times to stop pending work
            logger.info("Aggressively clearing all input/output queues")
            for i in range(3):  # Clear multiple times to ensure everything is gone
                await self.cleanup_queues()
                await asyncio.sleep(0.05)  # Small delay between clears
            
            # Additional aggressive queue clearing
            await self._aggressive_queue_cleanup()
            
            # Wait a moment for cleanup to complete
            await asyncio.sleep(0.2)
            
            # Convert and set new prompts
            self.current_prompts = [convert_prompt(prompt) for prompt in prompts]
            
            # Start new prompt tasks
            for idx in range(len(self.current_prompts)):
                task = asyncio.create_task(self.run_prompt(idx))
                self.running_prompts[idx] = task
            
            logger.info(f"Updated prompts: {len(self.current_prompts)} prompts now running")

    async def restart_client(self):
        """Restart the ComfyUI client to ensure clean state."""
        logger.info("Restarting ComfyUI client")
        
        # Cancel all running prompts first
        await self.cancel_running_prompts()
        
        # Cleanup existing client
        if self.comfy_client.is_running:
            try:
                await self.comfy_client.__aexit__(None, None, None)
            except Exception as e:
                logger.warning(f"Error during ComfyUI client exit: {e}")
        
        # Clear all queues
        await self.cleanup_queues()
        await self._aggressive_queue_cleanup()
        
        # Reinitialize client with same configuration
        config = Configuration(**self._config_kwargs)
        self.comfy_client = EmbeddedComfyClient(config, max_workers=self._max_workers)
        
        logger.info("ComfyUI client restarted successfully")

    def requires_warmup(self, new_params: Dict[str, Any]) -> bool:
        """Check if parameter changes require pipeline warmup."""
        warmup_params = {'width', 'height', 'cwd', 'disable_cuda_malloc', 'gpu_only'}
        
        for param in warmup_params:
            if param in new_params:
                current_value = getattr(self.comfy_client, param, None)
                if current_value != new_params[param]:
                    logger.info(f"Parameter {param} changed from {current_value} to {new_params[param]} - warmup required")
                    return True
        
        return False

    async def run_prompt(self, prompt_index: int):
        while True:
            async with self._prompt_update_lock:
                try:
                    # Use the original prompt directly - it should work
                    await self.comfy_client.queue_prompt(self.current_prompts[prompt_index])
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    await self.cleanup()
                    logger.error(f"Error running prompt: {str(e)}")
                    raise

    async def cleanup(self):
        await self.cancel_running_prompts()
        async with self._cleanup_lock:
            if self.comfy_client.is_running:
                try:
                    await self.comfy_client.__aexit__(None, None, None)
                except Exception as e:
                    logger.error(f"Error during ComfyClient cleanup: {e}")

            await self.cleanup_queues()
            logger.info("Client cleanup complete")

    async def cancel_running_prompts(self):
        async with self._cleanup_lock:
            tasks_to_cancel = list(self.running_prompts.values())
            for task in tasks_to_cancel:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            self.running_prompts.clear()

        
    async def cleanup_queues(self):
        while not tensor_cache.image_inputs.empty():
            tensor_cache.image_inputs.get()

        while not tensor_cache.audio_inputs.empty():
            tensor_cache.audio_inputs.get()

        while not tensor_cache.image_outputs.empty():
            await tensor_cache.image_outputs.get()

        while not tensor_cache.audio_outputs.empty():
            await tensor_cache.audio_outputs.get()

    async def _aggressive_queue_cleanup(self):
        """Aggressively clear all tensor cache queues."""
        try:
            # Clear image input queue completely
            while not tensor_cache.image_inputs.empty():
                try:
                    tensor_cache.image_inputs.get_nowait()
                except:
                    break
            
            # Clear audio input queue completely  
            while not tensor_cache.audio_inputs.empty():
                try:
                    tensor_cache.audio_inputs.get_nowait()
                except:
                    break
            
            # Clear image output queue completely
            while not tensor_cache.image_outputs.empty():
                try:
                    await asyncio.wait_for(tensor_cache.image_outputs.get(), timeout=0.01)
                except (asyncio.TimeoutError, Exception):
                    break
            
            # Clear audio output queue completely
            while not tensor_cache.audio_outputs.empty():
                try:
                    await asyncio.wait_for(tensor_cache.audio_outputs.get(), timeout=0.01)
                except (asyncio.TimeoutError, Exception):
                    break
                    
            logger.info("Aggressive queue cleanup completed")
        except Exception as e:
            logger.warning(f"Error during aggressive queue cleanup: {e}")

    def put_video_input(self, frame):
        if tensor_cache.image_inputs.full():
            tensor_cache.image_inputs.get(block=True)
        tensor_cache.image_inputs.put(frame)
    
    def put_audio_input(self, frame):
        tensor_cache.audio_inputs.put(frame)

    async def get_video_output(self):
        return await tensor_cache.image_outputs.get()
    
    async def get_audio_output(self):
        return await tensor_cache.audio_outputs.get()

    async def get_available_nodes(self):
        """Get metadata and available nodes info in a single pass"""
        # TODO: make it for for multiple prompts
        if not self.running_prompts:
            return {}

        try:
            from comfy.nodes.package import import_all_nodes_in_workspace
            nodes = import_all_nodes_in_workspace()

            all_prompts_nodes_info = {}
            
            for prompt_index, prompt in enumerate(self.current_prompts):
                # Get set of class types we need metadata for, excluding LoadTensor and SaveTensor
                needed_class_types = {
                    node.get('class_type') 
                    for node in prompt.values()  # type: ignore
                }
                remaining_nodes = {
                    node_id 
                    for node_id, node in prompt.items()  # type: ignore
                }
                nodes_info = {}
                
                # Get metadata for each node in the prompt
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
                                    
                all_prompts_nodes_info[prompt_index] = nodes_info
            
            return all_prompts_nodes_info
            
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
