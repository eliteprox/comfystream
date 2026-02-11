import asyncio
import contextlib
import logging
import time
from typing import List

from comfy.api.components.schema.prompt import PromptDictInput
from comfy.cli_args_types import Configuration
from comfy.client.embedded_comfy_client import EmbeddedComfyClient
from comfy.distributed.executors import ContextVarExecutor

from comfystream import tensor_cache
from comfystream.exceptions import ComfyStreamInputTimeoutError
from comfystream.modalities import detect_io_points
from comfystream.utils import convert_prompt, get_default_workflow

logger = logging.getLogger(__name__)


_fsspec_patched = False


def _patch_fsspec_registry():
    """Permanently patch fsspec's register_implementation to allow "pkg" clobber.

    The comfystream ImportContext can evict comfy.component_model.package_filesystem
    from sys.modules after its module-level ``register_implementation("pkg", ...)``
    already ran.  Any later re-import -- whether during __aenter__ or inside a
    ContextVarExecutor thread running queue_prompt -- creates a *new* class that
    conflicts with the already-registered one in fsspec.

    Rather than temporarily patching and restoring (which leaves the executor thread
    unprotected), we apply the patch once and leave it in place.  Only the "pkg"
    protocol is affected; all other registrations behave normally.
    """
    global _fsspec_patched
    if _fsspec_patched:
        return

    import importlib

    _fsreg = importlib.import_module("fsspec.registry")
    _original_register = _fsreg.register_implementation

    def _lenient_register(name, cls, clobber=False, errtxt=None):
        if name == "pkg":
            clobber = True
        return _original_register(name, cls, clobber=clobber, errtxt=errtxt)

    _fsreg.register_implementation = _lenient_register
    _fsspec_patched = True


def _create_embedded_client(
    config: Configuration,
    max_workers: int,
    executor: ContextVarExecutor,
) -> EmbeddedComfyClient:
    """Create an EmbeddedComfyClient that always uses a ContextVarExecutor.

    EmbeddedComfyClient's constructor rejects ContextVarExecutor when the config
    contains model-management flags (e.g. gpu_only) that normally require a
    ProcessPoolExecutor for process isolation.  Since comfystream runs ComfyUI
    in-process and doesn't need that isolation, we construct the client without
    a config first, then patch in the real config so the executor validation is
    bypassed while the configuration is still applied at runtime.
    """
    client = EmbeddedComfyClient(configuration=None, max_workers=max_workers)
    # Replace the auto-created executor with the one we want
    if client._owns_executor and client._executor is not None:
        client._executor.shutdown(wait=False)
    client._executor = executor
    client._owns_executor = True
    # Apply the real configuration so it's used in __aenter__ / queue_prompt
    client._configuration = config
    return client


class ComfyStreamClient:
    # Maximum seconds to wait for input before cancelling prompt execution.
    INPUT_WAIT_TIMEOUT = 5.0

    def __init__(self, max_workers: int = 1, **kwargs):
        config = Configuration(**kwargs)
        # Force ContextVarExecutor (thread-based) to avoid ProcessPoolExecutor pickling
        # issues. ComfyUI auto-selects ProcessPoolExecutor when model-management flags
        # like gpu_only are set, but ProcessPoolExecutor pickles the contextvars.Context
        # on every submission which fails when cvpickle._context_factory can't be
        # resolved (e.g. under debugpy or after module reloads). Since comfystream
        # embeds ComfyUI in-process, process isolation is unnecessary and
        # ContextVarExecutor (a ThreadPoolExecutor) works correctly while still
        # propagating the configuration via context vars.
        executor = ContextVarExecutor(max_workers=max_workers)
        self.comfy_client = _create_embedded_client(config, max_workers, executor)
        self.current_prompts = []
        self._cleanup_lock = asyncio.Lock()
        self._prompt_update_lock = asyncio.Lock()
        self._started = False

        # PromptRunner state
        self._shutdown_event = asyncio.Event()
        self._run_enabled_event = asyncio.Event()
        self._runner_task = None

    async def start(self):
        """Enter the EmbeddedComfyClient async context manager.

        This must be called before the first queue_prompt.  It applies
        context_configuration (populating folder_names_and_paths in the
        execution context) and formally starts the executor.  Without this,
        every queue_prompt call would trigger init_default_paths which can
        fail under debugpy due to duplicate fsspec registrations.
        """
        if self._started:
            return
        # The comfystream ImportContext can evict package_filesystem from sys.modules
        # after its module-level fsspec registration already ran.  Any later re-import
        # (during __aenter__ or inside a ContextVarExecutor thread running queue_prompt)
        # creates a new class that fails fsspec's identity check.  Patch once so all
        # future "pkg" registrations use clobber=True.
        _patch_fsspec_registry()

        await self.comfy_client.__aenter__()
        self._started = True
        logger.debug("EmbeddedComfyClient async context entered")

    async def set_prompts(self, prompts: List[PromptDictInput]):
        """Set new prompts, replacing any existing ones.

        Args:
            prompts: List of prompt dictionaries to set

        Raises:
            ValueError: If prompts list is empty
            Exception: If prompt conversion or validation fails
        """
        if not prompts:
            raise ValueError("Cannot set empty prompts list")

        # Pause runner while swapping prompts to avoid interleaving
        was_running = self._run_enabled_event.is_set()
        self._run_enabled_event.clear()
        self.current_prompts = [convert_prompt(prompt) for prompt in prompts]
        logger.info(f"Configured {len(self.current_prompts)} prompt(s)")
        # Ensure runner exists (IDLE until resumed)
        await self.ensure_prompt_tasks_running()
        if was_running:
            self._run_enabled_event.set()

    async def update_prompts(self, prompts: List[PromptDictInput]):
        async with self._prompt_update_lock:
            # TODO: currently under the assumption that only already running prompts are updated
            if len(prompts) != len(self.current_prompts):
                raise ValueError(
                    "Number of updated prompts must match the number of currently running prompts."
                )
            # Validation step before updating the prompt, only meant for a single prompt for now
            for idx, prompt in enumerate(prompts):
                converted_prompt = convert_prompt(prompt)
                try:
                    # Lightweight validation by queueing is retained for compatibility
                    await self.comfy_client.queue_prompt(converted_prompt)
                    self.current_prompts[idx] = converted_prompt
                except Exception as e:
                    raise Exception(f"Prompt update failed: {str(e)}") from e

    async def ensure_prompt_tasks_running(self):
        # Ensure the single runner task exists (does not force running)
        if self._runner_task and not self._runner_task.done():
            return
        if not self.current_prompts:
            return
        # Enter the EmbeddedComfyClient context before the first prompt run.
        # This applies context_configuration so queue_prompt doesn't need to
        # call init_default_paths on every execution.
        await self.start()
        self._shutdown_event.clear()
        self._runner_task = asyncio.create_task(self._runner_loop())

    async def _runner_loop(self):
        try:
            while not self._shutdown_event.is_set():
                # IDLE until running is enabled
                await self._run_enabled_event.wait()

                # Determine which input modalities the workflow actually requires
                # so we only wait for queues that the workflow will consume.
                io_caps = detect_io_points(self.current_prompts)
                needs_video = io_caps["video"]["input"]
                needs_audio = io_caps["audio"]["input"]

                # Wait until we actually have input to feed the workflow. This prevents
                # LoadTensor from timing out when the runner loops faster than frames
                # arrive from upstream. If no input arrives within the timeout, pause
                # the runner until new input is provided via put_video/audio_input.
                # Skip waiting entirely if the workflow has no external input nodes.
                if needs_video or needs_audio:
                    wait_start = time.monotonic()
                    last_log_time = 0.0
                    timed_out = False
                    while (
                        not self._shutdown_event.is_set()
                        and self._run_enabled_event.is_set()
                    ):
                        has_needed_input = (
                            (needs_video and tensor_cache.image_inputs.qsize() > 0)
                            or (needs_audio and tensor_cache.audio_inputs.qsize() > 0)
                        )
                        if has_needed_input:
                            break
                        elapsed = time.monotonic() - wait_start
                        if elapsed > self.INPUT_WAIT_TIMEOUT:
                            logger.warning(
                                "Runner waited %.1fs for input with no data "
                                "(needs_video=%s, needs_audio=%s), "
                                "pausing prompt execution until new input arrives",
                                elapsed,
                                needs_video,
                                needs_audio,
                            )
                            timed_out = True
                            break
                        # Throttle logging to once per second
                        if elapsed - last_log_time >= 1.0:
                            logger.info(
                                "Runner waiting for input "
                                "(image_in=%s, audio_in=%s, image_out=%s, waited=%.1fs)",
                                tensor_cache.image_inputs.qsize(),
                                tensor_cache.audio_inputs.qsize(),
                                tensor_cache.image_outputs.qsize(),
                                elapsed,
                            )
                            last_log_time = elapsed
                        await asyncio.sleep(0.01)
                    if self._shutdown_event.is_set() or not self._run_enabled_event.is_set():
                        break
                    if timed_out:
                        # Pause the runner; it will block at the top of the loop on
                        # _run_enabled_event.wait() until put_video/audio_input
                        # re-enables it.
                        self._run_enabled_event.clear()
                        continue

                # Snapshot prompts without holding the lock during network I/O
                async with self._prompt_update_lock:
                    prompts_snapshot = list(self.current_prompts)
                for prompt_index, prompt in enumerate(prompts_snapshot):
                    if self._shutdown_event.is_set() or not self._run_enabled_event.is_set():
                        break
                    try:
                        logger.debug(
                            "Queueing prompt %s (image_in=%s, audio_in=%s, image_out=%s)",
                            prompt_index,
                            tensor_cache.image_inputs.qsize(),
                            tensor_cache.audio_inputs.qsize(),
                            tensor_cache.image_outputs.qsize(),
                        )
                        await self.comfy_client.queue_prompt(prompt)
                    except asyncio.CancelledError:
                        raise
                    except ComfyStreamInputTimeoutError:
                        logger.warning(f"Input for prompt {prompt_index} timed out, continuing")
                        continue
                    except Exception as e:
                        logger.error(f"Error running prompt: {str(e)}")
                        # Re-raise the error to stop immediately instead of falling back to passthrough
                        raise
        except asyncio.CancelledError:
            pass

    async def cleanup(self):
        # Signal runner to shutdown
        self._shutdown_event.set()
        if self._runner_task:
            self._runner_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._runner_task
            self._runner_task = None

        # Pause running
        self._run_enabled_event.clear()

        async with self._cleanup_lock:
            if self._started:
                try:
                    await self.comfy_client.__aexit__(None, None, None)
                except Exception as e:
                    logger.error(f"Error during ComfyClient cleanup: {e}")
                finally:
                    self._started = False

            await self.cleanup_queues()
            logger.info("Client cleanup complete")

    def pause_prompts(self):
        """Pause prompt execution loops without canceling underlying tasks."""
        self._run_enabled_event.clear()
        logger.debug("Prompt execution paused")

    async def resume_prompts(self):
        """Resume prompt execution loops."""
        await self.ensure_prompt_tasks_running()
        self._run_enabled_event.set()
        logger.debug("Prompt execution resumed")

    async def stop_prompts(self, cleanup: bool = False):
        """Stop running prompts by canceling their tasks.

        Args:
            cleanup: If True, perform full cleanup including queue clearing and
                client shutdown. If False, only cancel prompt tasks.
        """
        await self.stop_prompts_immediately()

        if cleanup:
            await self.cleanup()
            logger.info("Prompts stopped with full cleanup")
        else:
            logger.debug("Prompts stopped (tasks cancelled)")

    async def cleanup_queues(self):
        while not tensor_cache.image_inputs.empty():
            tensor_cache.image_inputs.get()

        while not tensor_cache.audio_inputs.empty():
            tensor_cache.audio_inputs.get()

        while not tensor_cache.image_outputs.empty():
            await tensor_cache.image_outputs.get()

        while not tensor_cache.audio_outputs.empty():
            await tensor_cache.audio_outputs.get()

        while not tensor_cache.text_outputs.empty():
            await tensor_cache.text_outputs.get()

    async def stop_prompts_immediately(self):
        """Cancel the runner task to immediately stop any in-flight prompt execution."""
        self._run_enabled_event.clear()
        if self._runner_task:
            self._runner_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._runner_task
            self._runner_task = None

    async def _fallback_to_passthrough(self):
        """Switch to default passthrough workflow when an error occurs."""
        try:
            # Pause the runner
            self._run_enabled_event.clear()

            # Set to default passthrough workflow
            default_workflow = get_default_workflow()
            async with self._prompt_update_lock:
                self.current_prompts = [convert_prompt(default_workflow)]

            logger.info("Switched to default passthrough workflow")

            # Resume the runner with passthrough workflow
            self._run_enabled_event.set()

        except Exception as e:
            logger.error(f"Failed to fallback to passthrough: {str(e)}")
            # If fallback fails, just pause execution
            self._run_enabled_event.clear()

    def put_video_input(self, frame):
        logger.debug(f"Putting video input, queue size: {tensor_cache.image_inputs.qsize()}")
        if tensor_cache.image_inputs.full():
            tensor_cache.image_inputs.get(block=True)
        tensor_cache.image_inputs.put(frame)
        # Wake the runner if it was paused due to input timeout
        if not self._run_enabled_event.is_set():
            logger.info("New video input received, resuming runner")
            self._run_enabled_event.set()

    def put_audio_input(self, frame):
        tensor_cache.audio_inputs.put(frame)
        # Wake the runner if it was paused due to input timeout
        if not self._run_enabled_event.is_set():
            logger.info("New audio input received, resuming runner")
            self._run_enabled_event.set()

    async def get_video_output(self):
        logger.debug(f"Getting video output, queue size: {tensor_cache.image_outputs.qsize()}")
        return await tensor_cache.image_outputs.get()

    async def get_audio_output(self):
        return await tensor_cache.audio_outputs.get()

    async def get_text_output(self):
        try:
            return tensor_cache.text_outputs.get_nowait()
        except asyncio.QueueEmpty:
            # Expected case - queue is empty, no text available
            return None
        except Exception as e:
            # Unexpected errors logged for debugging
            logger.warning(f"Unexpected error in get_text_output: {e}")
            return None

    async def get_available_nodes(self):
        """Get metadata and available nodes info in a single pass"""
        # TODO: make it for for multiple prompts
        if not self.current_prompts:
            return {}

        try:
            from comfy.nodes.package import import_all_nodes_in_workspace

            nodes = import_all_nodes_in_workspace()

            all_prompts_nodes_info = {}

            for prompt_index, prompt in enumerate(self.current_prompts):
                # Get set of class types we need metadata for, excluding LoadTensor and SaveTensor
                needed_class_types = {node.get("class_type") for node in prompt.values()}
                remaining_nodes = {node_id for node_id, node in prompt.items()}
                nodes_info = {}

                # Only process nodes until we've found all the ones we need
                for class_type, node_class in nodes.NODE_CLASS_MAPPINGS.items():
                    if not remaining_nodes:  # Exit early if we've found all needed nodes
                        break

                    if class_type not in needed_class_types:
                        continue

                    # Get metadata for this node type (same as original get_node_metadata)
                    input_data = (
                        node_class.INPUT_TYPES() if hasattr(node_class, "INPUT_TYPES") else {}
                    )
                    input_info = {}

                    # Process required inputs
                    if "required" in input_data:
                        for name, value in input_data["required"].items():
                            if isinstance(value, tuple):
                                if len(value) == 1 and isinstance(value[0], list):
                                    # Handle combo box case where value is ([option1, option2, ...],)
                                    input_info[name] = {
                                        "type": "combo",
                                        "value": value[0],  # The list of options becomes the value
                                    }
                                elif len(value) == 2:
                                    input_type, config = value
                                    input_info[name] = {
                                        "type": input_type,
                                        "required": True,
                                        "min": config.get("min", None),
                                        "max": config.get("max", None),
                                        "widget": config.get("widget", None),
                                    }
                                elif len(value) == 1:
                                    # Handle simple type case like ('IMAGE',)
                                    input_info[name] = {"type": value[0]}
                            else:
                                logger.error(
                                    f"Unexpected structure for required input {name}: {value}"
                                )

                    # Process optional inputs with same logic
                    if "optional" in input_data:
                        for name, value in input_data["optional"].items():
                            if isinstance(value, tuple):
                                if len(value) == 1 and isinstance(value[0], list):
                                    # Handle combo box case where value is ([option1, option2, ...],)
                                    input_info[name] = {
                                        "type": "combo",
                                        "value": value[0],  # The list of options becomes the value
                                    }
                                elif len(value) == 2:
                                    input_type, config = value
                                    input_info[name] = {
                                        "type": input_type,
                                        "required": False,
                                        "min": config.get("min", None),
                                        "max": config.get("max", None),
                                        "widget": config.get("widget", None),
                                    }
                                elif len(value) == 1:
                                    # Handle simple type case like ('IMAGE',)
                                    input_info[name] = {"type": value[0]}
                            else:
                                logger.error(
                                    f"Unexpected structure for optional input {name}: {value}"
                                )

                    # Now process any nodes in our prompt that use this class_type
                    for node_id in list(remaining_nodes):
                        node = prompt[node_id]
                        if node.get("class_type") != class_type:
                            continue

                        node_info = {"class_type": class_type, "inputs": {}}

                        if "inputs" in node:
                            for input_name, input_value in node["inputs"].items():
                                input_metadata = input_info.get(input_name, {})
                                node_info["inputs"][input_name] = {
                                    "value": input_value,
                                    "type": input_metadata.get("type", "unknown"),
                                    "min": input_metadata.get("min", None),
                                    "max": input_metadata.get("max", None),
                                    "widget": input_metadata.get("widget", None),
                                }
                                # For combo type inputs, include the list of options
                                if input_metadata.get("type") == "combo":
                                    node_info["inputs"][input_name]["value"] = input_metadata.get(
                                        "value", []
                                    )

                        nodes_info[node_id] = node_info
                        remaining_nodes.remove(node_id)

                    all_prompts_nodes_info[prompt_index] = nodes_info

            return all_prompts_nodes_info

        except Exception as e:
            logger.error(f"Error getting node info: {str(e)}")
            return {}
