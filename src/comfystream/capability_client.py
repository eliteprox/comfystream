"""Local capabilities through the embedded ComfyUI core process executor.

This path is independent of the hosted fal job pool. Each admitted slot owns one
core executor, and a shared file lock prevents separate containers overbooking it.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from stream_pack.catalog import load_catalog


def _initialize_worker(gpu: str, input_dir: str, output_dir: str) -> None:
    os.environ["CUDA_VISIBLE_DEVICES"] = gpu
    os.environ["STREAM_PACK_INPUT_DIR"] = input_dir
    os.environ["STREAM_PACK_OUTPUT_DIR"] = output_dir
    os.environ["HF_HUB_OFFLINE"] = "1"


class CapacityUnavailable(RuntimeError):
    pass


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

    async def _idle_unload(self):
        await asyncio.sleep(5)
        async with self._lock:
            await self._stop_worker()

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

    async def _acquire_gpu(self):
        self.capacity_file.parent.mkdir(parents=True, exist_ok=True)
        handle = self.capacity_file.open("a+b")
        handle.seek(0)
        if not handle.read(1):
            handle.write(b"0")
            handle.flush()
        while True:
            try:
                if os.name == "nt":
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._file = handle
                return
            except OSError:
                try:
                    await asyncio.sleep(0.05)
                except BaseException:
                    handle.close()
                    raise

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
            configuration_factory(cwd=self.workspace, preview_method="none"),
            executor=self._executor,
        )
        await self._client.__aenter__()
        self._model = capability

    async def _stop_worker(self):
        if self._executor is not None:
            executor, self._executor = self._executor, None
            executor.stop()
            # A future cancellation is insufficient: wait for the CUDA owner to exit.
            stopped = asyncio.create_task(asyncio.to_thread(executor.join))
            try:
                await asyncio.shield(stopped)
            except asyncio.CancelledError:
                await stopped
                raise
        self._client = None
        self._model = None
        if self._file is not None:
            self._file.close()
            self._file = None

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
                    await self._load(capability)
                    try:
                        outputs = await self._client.queue_prompt(prompt)
                        result = outputs[spec.document["output_binding"]["node"]]["service_result"][
                            0
                        ]
                        spec.validate_output(result)
                        await self._client.clear_cache()
                        self._idle_task = asyncio.create_task(self._idle_unload())
                        return result
                    except BaseException:
                        await self._stop_worker()
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
