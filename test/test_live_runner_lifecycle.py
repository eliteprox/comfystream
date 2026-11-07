from types import SimpleNamespace

import pytest
from aiohttp import web

from comfystream.server.live_runner import _close_session


@pytest.mark.asyncio
async def test_closed_session_replaces_its_permanently_stopped_executor():
    class Pipeline:
        def __init__(self):
            self.closed = False
            self.initialized = False

        async def stop_prompts(self, cleanup=False):
            assert cleanup
            self.closed = True

        async def initialize(self):
            self.initialized = True

    app = web.Application()
    app["pipeline"] = Pipeline()
    app["pipeline_factory"] = Pipeline
    for _ in range(2):
        old = app["pipeline"]
        app["session"] = SimpleNamespace(
            text_task=None, media_in=None, video_out=None, text_out=None
        )
        await _close_session(app)
        assert old.closed
        assert app["pipeline"] is not old
        assert app["pipeline"].initialized
        assert app["session"] is None


def test_fal_advertising_is_explicit_opt_in(monkeypatch):
    import sys

    from comfystream.server.live_runner import _parse_args

    monkeypatch.setattr(sys, "argv", ["runner", "--workspace", "/tmp"])
    assert _parse_args().skip_batch is True
    monkeypatch.setattr(sys, "argv", ["runner", "--workspace", "/tmp", "--enable-fal"])
    assert _parse_args().skip_batch is False
    monkeypatch.setattr(
        sys, "argv", ["runner", "--workspace", "/tmp", "--enable-fal", "--skip-batch"]
    )
    assert _parse_args().skip_batch is True


@pytest.mark.asyncio
async def test_workflow_reservation_uses_the_same_live_gpu_slot(monkeypatch):
    import asyncio

    from comfystream.realtime.capacity import WarmCapacity
    from comfystream.realtime.spec import RealtimePipelineSpec
    from comfystream.realtime.supervisor import RealtimeSupervisor

    flux = RealtimePipelineSpec(
        name="flux", app="live/flux", backend="comfy_workflow", port=1, gpu="GPU-shared"
    )
    sd = RealtimePipelineSpec(
        name="sd", app="live/sd", backend="comfy_workflow", port=2, gpu="GPU-shared", policy="cold"
    )
    supervisor = RealtimeSupervisor(
        [flux, sd], orchestrator="", orch_secret="", capacity=WarmCapacity(max_loaded=1)
    )
    monkeypatch.setattr(supervisor, "_schedule_reconcile", lambda: None)
    supervisor.loaded = {"flux"}
    supervisor._reports["flux"] = (1, "ready", 0, False)
    supervisor._exited["flux"] = asyncio.Event()

    async def evict(_session, victim):
        assert victim.name == "flux"
        supervisor.loaded.discard(victim.name)
        supervisor._exited[victim.name].set()
        return True

    monkeypatch.setattr(supervisor, "_request_evict", evict)
    assert await supervisor.acquire_external("workflow", "GPU-shared") is None
    assert supervisor.loaded == {"workflow"}
    assert supervisor.status_for(sd) == "busy"
    assert await supervisor.acquire("sd") is not None
    assert supervisor.loaded == {"workflow"}
    await supervisor.release_external("workflow")
    assert await supervisor.acquire("sd") is None
    assert supervisor.loaded == {"sd"}


@pytest.mark.asyncio
async def test_cancelled_workflow_admission_clears_its_reservation(monkeypatch):
    import asyncio

    from comfystream.realtime.supervisor import RealtimeSupervisor

    supervisor = RealtimeSupervisor([], orchestrator="", orch_secret="")
    monkeypatch.setattr(supervisor, "_schedule_reconcile", lambda: None)
    entered = asyncio.Event()

    async def acquire(_name):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(supervisor, "acquire", acquire)
    task = asyncio.create_task(supervisor.acquire_external("workflow", "GPU-shared"))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert "workflow" not in supervisor._external
    assert "workflow" not in supervisor.loaded
