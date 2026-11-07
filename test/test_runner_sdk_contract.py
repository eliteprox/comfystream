"""Catch drift between runner registration calls and the pinned SDK."""

import ast
import importlib.util
import inspect
from pathlib import Path

import pytest
from livepeer_gateway.live_runner import register_runner


@pytest.mark.parametrize(
    "module",
    [
        "comfystream.realtime.worker",
        "comfystream.server.live_runner",
        "comfystream.server.ffmpeg_runner",
    ],
)
def test_registration_keywords_match_installed_sdk(module):
    source = Path(importlib.util.find_spec(module).origin).read_text()
    calls = [
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "register_runner"
    ]
    assert calls
    for call in calls:
        assert all(keyword.arg is not None for keyword in call.keywords)
        inspect.signature(register_runner).bind(
            None, **{keyword.arg: None for keyword in call.keywords}
        )


@pytest.mark.asyncio
async def test_pinned_sdk_mutations_reach_heartbeat_payload(monkeypatch):
    from livepeer_gateway.live_runner import LiveRunnerRegistration

    from comfystream.sdk_compat import update_registration

    registration = object.__new__(LiveRunnerRegistration)
    seen = []

    async def send():
        seen.append((registration._metadata, registration._status))

    monkeypatch.setattr(registration, "_send_heartbeat", send)
    await update_registration(registration, metadata='{"runner_contract":1}', status="busy")
    await update_registration(registration, status="ready")
    assert seen == [('{"runner_contract":1}', "busy"), ('{"runner_contract":1}', "ready")]
