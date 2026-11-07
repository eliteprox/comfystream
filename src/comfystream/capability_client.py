"""Local capabilities through the embedded ComfyUI core process executor.

This path is independent of the hosted fal job pool. Each admitted slot owns one
core executor, and a shared file lock prevents separate containers overbooking it.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from stream_pack.catalog import load_catalog


def _initialize_worker(gpu: str, input_dir: str, output_dir: str) -> None:
    os.environ["CUDA_VISIBLE_DEVICES"] = gpu
    os.environ["STREAM_PACK_INPUT_DIR"] = input_dir
    os.environ["STREAM_PACK_OUTPUT_DIR"] = output_dir
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
    # Catalog checkpoints are provisioned locally and workers run offline.
    # Bypass download interception, whose nested import contexts can recurse.
    from comfy import model_downloader
    from comfy.cmd import folder_paths
    from comfy.component_model.platform_path import construct_path

    def local_lookup(folder_name, filename, *args, **kwargs):
        paths = folder_paths._folder_names_and_paths()
        path = paths.first_existing_or_none(folder_name, construct_path(filename))
        if path is not None:
            return str(path)
        root = (Path(os.environ["COMFYUI_CWD"]) / "models" / folder_name).resolve()
        candidate = Path(os.path.abspath(root / filename))
        if candidate.is_relative_to(root) and candidate.is_file():
            return str(candidate)
        return None

    model_downloader.get_or_download = local_lookup
    # Core node exception logs can include complete node inputs. The service
    # boundary returns sanitized errors; worker logs must not retain customer data.
    logging.disable(logging.CRITICAL)


def _set_request_output(directory: str):
    os.environ["STREAM_PACK_OUTPUT_DIR"] = directory


def _discard_request_output(directory: Path, root: str):
    resolved = directory.resolve()
    if resolved.parent != Path(root).resolve() or not resolved.name.startswith("request_"):
        raise ValueError("Invalid request output directory")
    try:
        shutil.rmtree(resolved)
    except FileNotFoundError:
        pass


def _try_lock(handle):
    """Nonblocking lock, also used to distinguish live demand from crash debris."""
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)


class CapacityUnavailable(RuntimeError):
    pass


def _validate_workflows(workspace: str, capabilities: list[str]):
    from comfy.cli_args_types import Configuration
    from comfy.execution_context import context_configuration

    with context_configuration(
        Configuration(
            cwd=workspace, cpu=True, offline=True, disable_api_nodes=True, preview_method="none"
        )
    ):
        from comfy.nodes.package import import_all_nodes_in_workspace

        nodes = import_all_nodes_in_workspace()
        catalog = load_catalog()
        for name in capabilities:
            catalog[name].validate_workflow(nodes.NODE_CLASS_MAPPINGS)


class CapabilityClient:
    def __init__(
        self,
        *,
        workspace: str,
        input_dir: str,
        output_dir: str,
        capacity_file: str,
        gpu: str = "0",
        queue_limit: int = 16,
        executor_factory=None,
        client_factory=None,
        configuration_factory=None,
    ):
        self.catalog = load_catalog()
        self.workspace = workspace
        self.input_dir = str(Path(input_dir).resolve())
        self.output_dir = str(Path(output_dir).resolve())
        self.capacity_file = Path(capacity_file)
        self.gpu = gpu
        self.queue_limit = queue_limit
        self._waiting = 0
        self._lock = asyncio.Lock()
        self._executor = None
        self._client = None
        self._model = None
        self._file = None
        self._executor_factory = executor_factory
        self._client_factory = client_factory
        self._configuration_factory = configuration_factory
        self._idle_task = None

    def _capacity_requested(self):
        for path in self.capacity_file.parent.glob(self.capacity_file.name + ".waiter_*"):
            try:
                with path.open("r+b") as handle:
                    try:
                        _try_lock(handle)
                    except OSError:
                        return True
            except FileNotFoundError:
                continue
        return False

    async def _idle_unload(self):
        # Retain weights indefinitely while idle. A competing container signals
        # demand with a locked file on the shared capacity volume. Unlocked files
        # left by crashed waiters do not cause eviction. Never evict active work.
        while True:
            await asyncio.sleep(0.1)
            async with self._lock:
                if self._capacity_requested():
                    await self._stop_worker()
                    return

    def capabilities(self) -> list[dict[str, Any]]:
        return [
            {
                "id": c.id,
                "model": c.document["model"],
                "admission": "candidate",
                "input_schema": c.document["input_schema"],
                "operation": c.document["operation"],
            }
            for c in self.catalog.values()
        ]

    async def validate_capabilities(self, capabilities: list[str]):
        # Registers core's context-variable serializers before executor submission.
        import comfy.execution_context
        from comfy.distributed.process_pool_executor import ProcessPoolExecutor

        executor = ProcessPoolExecutor(max_workers=1)
        try:
            await asyncio.get_running_loop().run_in_executor(
                executor, _validate_workflows, self.workspace, capabilities
            )
        finally:
            executor.stop()
            await asyncio.to_thread(executor.join)

    async def _acquire_gpu(self):
        self.capacity_file.parent.mkdir(parents=True, exist_ok=True)
        handle = self.capacity_file.open("a+b")
        handle.seek(0)
        if not handle.read(1):
            handle.write(b"0")
            handle.flush()
        demand = None
        demand_path = None
        admitted = False
        try:
            while True:
                try:
                    _try_lock(handle)
                    self._file = handle
                    admitted = True
                    return
                except OSError:
                    if demand is None:
                        descriptor, name = tempfile.mkstemp(
                            prefix=self.capacity_file.name + ".waiter_",
                            dir=self.capacity_file.parent,
                        )
                        demand_path = Path(name)
                        demand = os.fdopen(descriptor, "r+b")
                        demand.write(b"0")
                        demand.flush()
                        _try_lock(demand)
                    await asyncio.sleep(0.05)
        finally:
            if demand is not None:
                demand.close()
                demand_path.unlink(missing_ok=True)
            if not admitted:
                handle.close()

    async def _load(self, capability: str):
        if self._model == capability:
            return
        await self._stop_worker()
        if self._file is None:
            await self._acquire_gpu()
        executor_factory, client_factory = self._executor_factory, self._client_factory
        configuration_factory = self._configuration_factory
        if executor_factory is None:
            from comfy.distributed.process_pool_executor import ProcessPoolExecutor

            executor_factory = ProcessPoolExecutor
        if client_factory is None:
            from comfy.client.embedded_comfy_client import EmbeddedComfyClient

            client_factory = EmbeddedComfyClient
        if configuration_factory is None:
            from comfy.cli_args_types import Configuration

            configuration_factory = Configuration

        self._executor = executor_factory(
            max_workers=1,
            initializer=_initialize_worker,
            initargs=(self.gpu, self.input_dir, self.output_dir),
        )
        self._client = client_factory(
            configuration_factory(
                cwd=self.workspace,
                preview_method="none",
                offline=True,
                disable_api_nodes=True,
                disable_manager_model_fallback=True,
                disable_civitai_model_fallback=True,
                logging_level="CRITICAL",
            ),
            executor=self._executor,
        )
        # This caller owns the external executor. queue_prompt establishes its
        # per-call configuration context; retaining an entered context across
        # unrelated asyncio request/idle tasks would leak context-variable tokens.
        self._model = capability

    async def _stop_worker(self):
        if self._executor is not None:
            executor = self._executor
            executor.stop()
            # A future cancellation is insufficient: wait for the CUDA owner to exit.
            stopped = asyncio.create_task(asyncio.to_thread(executor.join))
            interrupted = False
            while not stopped.done():
                try:
                    await asyncio.shield(stopped)
                except asyncio.CancelledError:
                    interrupted = True
            stopped.result()
            self._executor = None
        else:
            interrupted = False
        self._client = None
        self._model = None
        if self._file is not None:
            self._file.close()
            self._file = None
        if interrupted:
            raise asyncio.CancelledError

    async def execute(
        self, capability: str, inputs: dict[str, Any], *, timeout: float = 300
    ) -> dict[str, Any]:
        spec = self.catalog[capability]
        prompt = spec.render(inputs)
        if self._waiting >= self.queue_limit:
            raise CapacityUnavailable("Capability queue is full")
        self._waiting += 1
        try:
            async with asyncio.timeout(timeout):
                async with self._lock:
                    if self._idle_task:
                        self._idle_task.cancel()
                        self._idle_task = None
                    request_output = None
                    try:
                        await self._load(capability)
                        Path(self.output_dir).mkdir(parents=True, exist_ok=True)
                        request_output = Path(
                            tempfile.mkdtemp(prefix="request_", dir=self.output_dir)
                        )
                        await asyncio.get_running_loop().run_in_executor(
                            self._executor, _set_request_output, str(request_output)
                        )
                        outputs = await self._client.queue_prompt(prompt)
                        result = outputs[spec.document["output_binding"]["node"]]["service_result"][
                            0
                        ]
                        spec.validate_output(result)
                        await self._client.clear_cache(unload_models=False)
                        self._client.history.clear()
                        if not result.get("artifacts"):
                            await asyncio.to_thread(
                                _discard_request_output, request_output, self.output_dir
                            )
                        self._idle_task = asyncio.create_task(self._idle_unload())
                        return result
                    except BaseException:
                        try:
                            await self._stop_worker()
                        finally:
                            if request_output is not None and self._executor is None:
                                await asyncio.to_thread(
                                    _discard_request_output, request_output, self.output_dir
                                )
                        raise
        finally:
            self._waiting -= 1

    async def close(self):
        if self._idle_task:
            self._idle_task.cancel()
            await asyncio.gather(self._idle_task, return_exceptions=True)
            self._idle_task = None
        async with self._lock:
            await self._stop_worker()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()
