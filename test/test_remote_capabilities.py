import asyncio

import pytest
from aiohttp.test_utils import TestServer
from stream_pack.catalog import load_catalog

from comfystream.remote_client import RemoteCapabilityClient, RemoteExecutionError
from comfystream.server.capability_runner import create_app


class FakeClient:
    def __init__(self):
        self.catalog = load_catalog()
        self.validated = None
        self.closed = False
        self.block = False
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def validate_capabilities(self, capabilities):
        self.validated = capabilities

    async def execute(self, capability, inputs, *, timeout):
        self.started.set()
        if self.block:
            try:
                await asyncio.Event().wait()
            finally:
                self.cancelled.set()
        return {"artifacts": [], "text": "test result"}

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_two_profiles_expose_one_validated_catalog_and_preserve_cancellation():
    media, chat = FakeClient(), FakeClient()
    async with TestServer(
        create_app(media, profile="media", token="test-token"), handler_cancellation=True
    ) as media_server:
        async with TestServer(
            create_app(chat, profile="chat", token="test-token"), handler_cancellation=True
        ) as chat_server:
            runners = {
                "media": str(media_server.make_url("/")),
                "chat": str(chat_server.make_url("/")),
            }
            invalid = RemoteCapabilityClient(runners, token="wrong-token")
            with pytest.raises(RemoteExecutionError):
                await invalid.start()
            client = RemoteCapabilityClient(runners, token="test-token")
            await client.start()
            try:
                assert len(client._routes) == 9
                assert len(media.validated) == 8 and chat.validated == ["qwen-chat"]
                result = await client.execute("whisper", {"audio": "fixture.wav"})
                assert result["text"] == "test result"
                media.started.clear()
                media.block = True
                task = asyncio.create_task(client.execute("whisper", {"audio": "fixture.wav"}))
                await media.started.wait()
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                await asyncio.wait_for(media.cancelled.wait(), timeout=3)
            finally:
                await client.close()
    assert media.closed and chat.closed
