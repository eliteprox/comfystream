import asyncio
from concurrent.futures import Future

import pytest

from comfystream.capability_client import CapabilityClient


class FakeExecutor:
    def __init__(self, **kwargs):
        self.options = kwargs
        self.stopped = False
        self.joined = False

    def stop(self):
        self.stopped = True

    def join(self):
        assert self.stopped
        self.joined = True

    def submit(self, function, *args):
        future = Future()
        future.set_result(None)
        return future


class FakeClient:
    def __init__(self, config, executor):
        self.executor = executor
        self.wait = False
        self.started = asyncio.Event()
        self.history = {}

    async def __aenter__(self):
        return self

    async def clear_cache(self, *, unload_models=True):
        assert unload_models is False

    async def queue_prompt(self, prompt):
        self.history["prompt"] = prompt
        self.started.set()
        if self.wait:
            await asyncio.Event().wait()
        return {"2": {"service_result": [{"text": "ok", "artifacts": []}]}}


def client(tmp_path):
    return CapabilityClient(
        workspace=str(tmp_path),
        input_dir=str(tmp_path / "input"),
        output_dir=str(tmp_path / "output"),
        capacity_file=str(tmp_path / "gpu.lock"),
        executor_factory=FakeExecutor,
        client_factory=FakeClient,
        configuration_factory=dict,
    )


@pytest.mark.asyncio
async def test_explicit_executor_reuses_model_and_evicts_on_switch(tmp_path):
    service = client(tmp_path)
    try:
        assert (await service.execute("whisper", {"audio": "test.wav"}))["text"] == "ok"
        worker = service._executor
        assert service._client.history == {}
        assert worker.options["max_workers"] == 1
        assert service._client.executor is worker
        # Regression: the old idle timer killed this worker after five seconds.
        await asyncio.sleep(5.2)
        assert service._executor is worker
        assert not worker.stopped
        await service.execute("whisper", {"audio": "test.wav"})
        assert service._executor is worker
        await service.execute("birefnet", {"image": "test.png"})
        assert worker.joined
        assert service._executor is not worker
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_cancel_joins_worker_before_releasing_capacity(tmp_path):
    service = client(tmp_path)
    await service.execute("whisper", {"audio": "test.wav"})
    worker = service._executor
    service._client.wait = True
    task = asyncio.create_task(service.execute("whisper", {"audio": "test.wav"}))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert worker.joined
    assert service._file is None
    assert list((tmp_path / "output").iterdir()) == []
    await service.close()


@pytest.mark.asyncio
async def test_competing_client_evicts_idle_resident_and_reclaims_demand(tmp_path):
    resident, contender = client(tmp_path), client(tmp_path)
    try:
        await resident.execute("whisper", {"audio": "test.wav"})
        old_worker = resident._executor
        await asyncio.wait_for(contender.execute("birefnet", {"image": "test.png"}), 3)
        assert old_worker.joined
        assert resident._file is None
        assert contender._executor is not None
        assert not list(tmp_path.glob("gpu.lock.waiter_*"))
    finally:
        await resident.close()
        await contender.close()


@pytest.mark.asyncio
async def test_pressure_never_interrupts_active_request_and_cancel_cleans_waiter(tmp_path):
    resident, contender = client(tmp_path), client(tmp_path)
    await resident.execute("whisper", {"audio": "test.wav"})
    worker = resident._executor
    resident._client.wait = True
    active = asyncio.create_task(resident.execute("whisper", {"audio": "test.wav"}))
    waiting = asyncio.create_task(contender.execute("birefnet", {"image": "test.png"}))
    try:
        async with asyncio.timeout(3):
            while not list(tmp_path.glob("gpu.lock.waiter_*")):
                await asyncio.sleep(0.01)
        await asyncio.sleep(0.2)
        assert not worker.stopped
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        assert not list(tmp_path.glob("gpu.lock.waiter_*"))
    finally:
        active.cancel()
        await asyncio.gather(active, waiting, return_exceptions=True)
        await resident.close()
        await contender.close()


@pytest.mark.asyncio
async def test_abandoned_demand_does_not_evict_resident(tmp_path):
    service = client(tmp_path)
    try:
        await service.execute("whisper", {"audio": "test.wav"})
        worker = service._executor
        (tmp_path / "gpu.lock.waiter_crashed").write_bytes(b"0")
        await asyncio.sleep(0.3)
        assert not worker.stopped
    finally:
        await service.close()
