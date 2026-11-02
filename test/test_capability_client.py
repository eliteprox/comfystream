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
