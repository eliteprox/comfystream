"""GPU-free Swarm receipts for FFmpeg outputs."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from eth_account import Account
from livepeer_swarm.bmt import chunk_address, pyramid
from livepeer_swarm.frame import unpack
from livepeer_swarm.publisher import SwarmPublishError
from livepeer_swarm.stamp import STAMP_SIZE, StampIssuer

from comfystream.ffmpeg.swarm import (
    AudioSwarmSession,
    SwarmHold,
    SwarmProtocolError,
    file_descriptor,
)

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "ffmpeg_runner",
    REPO / "server" / "ffmpeg_runner.py",
)
ffmpeg_runner = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(ffmpeg_runner)

PAYLOAD = b"swarm-payload-marker-" + (b"a" * 5000)


class FakePool:
    def __init__(self, result: dict):
        self.result = result

    async def submit(self, _job: dict, timeout: float) -> dict:
        del timeout
        return self.result


class FakePublisher:
    def __init__(self, fail: bool = False):
        self.frames: list[bytes] = []
        self.fail = fail
        self.enabled = True

    async def put(self, frame: bytes) -> None:
        if self.fail:
            raise SwarmPublishError(500, "bee down")
        self.frames.append(frame)

    async def aclose(self) -> None:
        return None


def _addresses(data: bytes) -> list[str]:
    return [chunk_address(span, body).hex() for span, body in pyramid(data)]


def _stamp(addresses: list[str]) -> list[str]:
    account = Account.create()
    issuer = StampIssuer(b"\x11" * 32)
    return [issuer.stamp(bytes.fromhex(address), account).hex() for address in addresses]


def _runner(tmp_path, result: dict, *, swarm: bool) -> ffmpeg_runner.FfmpegRunner:
    os.environ.pop("BEE_URL", None)
    ffmpeg_runner.OUTPUT_ROOT = tmp_path
    args = SimpleNamespace(
        runner_url="http://ffmpeg-runner:8994",
        orchestrator="",
        orchSecret="",
        realtime_config="",
    )
    runner = ffmpeg_runner.FfmpegRunner(args)
    runner.pool = FakePool(result)
    runner._http = None
    if swarm:
        runner.bee_url = "http://bee.test"
        runner.hold = SwarmHold()
        runner.publisher = FakePublisher()
    else:
        runner.hold = None
        runner.publisher = FakePublisher()
    return runner


async def _collect(_session, _tool, _body, _work):
    return {"media": PAYLOAD}


def _app(runner) -> web.Application:
    app = web.Application()
    app.router.add_post("/batch/{tool}", runner.handle_batch)
    app.router.add_post("/publish", runner.handle_publish)
    app.router.add_get("/files/{name}", runner.handle_file)
    return app


@pytest.mark.parametrize(
    ("tool", "key"),
    [("scale-video", "video"), ("loudnorm", "audio"), ("extract-frame", "images")],
)
def test_batch_receipt_is_addresses_only(tmp_path, monkeypatch, tool, key):
    monkeypatch.setattr(ffmpeg_runner, "_collect", _collect)
    runner = _runner(
        tmp_path,
        {"content_type": "video/mp4", "data": PAYLOAD, "suffix": ".mp4"},
        swarm=True,
    )

    async def scenario():
        async with TestClient(TestServer(_app(runner))) as client:
            response = await client.post(f"/batch/{tool}", json={})
            assert response.status == 200
            body = await response.json()
        text = json.dumps(body)
        assert "swarm-payload-marker-" not in text
        media = body["images"][0] if key == "images" else body[key]
        assert media["addresses"] == _addresses(PAYLOAD)
        assert media["root"] == media["addresses"][-1]
        assert media["bytes"] == len(PAYLOAD)
        assert "url" not in media
        assert media["request_id"]

    asyncio.run(scenario())


def test_publish_posts_one_frame_per_stamp(tmp_path, monkeypatch):
    monkeypatch.setattr(ffmpeg_runner, "_collect", _collect)
    runner = _runner(
        tmp_path,
        {"content_type": "video/mp4", "data": PAYLOAD, "suffix": ".mp4"},
        swarm=True,
    )

    async def scenario():
        async with TestClient(TestServer(_app(runner))) as client:
            created = await client.post("/batch/scale-video", json={})
            media = (await created.json())["video"]
            short = await client.post(
                "/publish",
                json={"request_id": media["request_id"], "stamps": media["addresses"][:1]},
            )
            assert short.status == 400
            assert media["request_id"] in runner.hold._items
            stamps = _stamp(media["addresses"])
            runner.publisher.fail = True
            failed = await client.post(
                "/publish",
                json={"request_id": media["request_id"], "stamps": stamps},
            )
            assert failed.status == 502
            assert media["request_id"] in runner.hold._items
            runner.publisher.fail = False
            published = await client.post(
                "/publish",
                json={"request_id": media["request_id"], "stamps": stamps},
            )
            assert published.status == 200
            body = await published.json()
            assert body["reference"] == media["root"]
            assert body["url"] == f"https://bzz.limo/bytes/{media['root']}?x=.mp4"
            assert len(runner.publisher.frames) == len(media["addresses"])
            for frame, address in zip(runner.publisher.frames, media["addresses"], strict=True):
                stamped = unpack(frame)
                assert len(stamped.stamp) == STAMP_SIZE
                assert stamped.address.hex() == address
            missing = await client.post(
                "/publish",
                json={"request_id": media["request_id"], "stamps": stamps},
            )
            assert missing.status == 404

    asyncio.run(scenario())


def test_files_url_when_bee_unset(tmp_path, monkeypatch):
    monkeypatch.setattr(ffmpeg_runner, "_collect", _collect)
    runner = _runner(
        tmp_path,
        {"content_type": "video/mp4", "data": PAYLOAD, "suffix": ".mp4"},
        swarm=False,
    )

    async def scenario():
        async with TestClient(TestServer(_app(runner))) as client:
            response = await client.post("/batch/trim-video", json={})
            body = await response.json()
        assert body["video"]["url"].startswith("http://ffmpeg-runner:8994/files/")
        assert "addresses" not in body["video"]
        name = body["video"]["url"].rsplit("/", 1)[-1]
        assert (tmp_path / name).read_bytes() == PAYLOAD

    asyncio.run(scenario())


@pytest.mark.parametrize("swarm", [False, True])
def test_metadata_is_the_probe(tmp_path, monkeypatch, swarm):
    monkeypatch.setattr(ffmpeg_runner, "_collect", _collect)
    probe = {"media": {"workflow": "GetVideoComponents", "file": "input.bin"}}
    runner = _runner(tmp_path, probe, swarm=swarm)

    async def scenario():
        async with TestClient(TestServer(_app(runner))) as client:
            response = await client.post("/batch/metadata", json={})
            assert await response.json() == probe

    asyncio.run(scenario())
    if runner.hold is not None:
        assert runner.hold._items == {}


def test_file_descriptor_without_hold():
    described = file_descriptor(None, b"abc", "video/mp4", "http://runner/files/a.mp4")
    assert described["url"] == "http://runner/files/a.mp4"
    assert "addresses" not in described


def test_hold_drops_the_oldest_past_two_files():
    hold = SwarmHold()
    offered = [hold.offer(bytes([index]) * 8, "video/mp4") for index in range(3)]
    assert offered[0]["request_id"] not in hold._items
    assert offered[2]["request_id"] in hold._items


def test_expired_request_is_unknown():
    hold = SwarmHold()
    offered = hold.offer(b"abc", "video/mp4")
    hold._items[offered["request_id"]].created = 0

    async def scenario():
        with pytest.raises(SwarmProtocolError) as caught:
            await hold.publish(offered["request_id"], [], FakePublisher())
        assert caught.value.status == 404

    asyncio.run(scenario())


def test_audio_segment_stamp_round_trip():
    events: list[dict] = []

    async def on_event(event: dict) -> None:
        events.append(event)

    async def scenario():
        publisher = FakePublisher()
        session = AudioSwarmSession(publisher, on_event)
        segment = b"\x01\x02" * 3000
        await session.push(segment)
        assert events[0]["addresses"] == _addresses(segment)
        assert "ok" not in events[0]
        await session.receive_stamps(
            {
                "req_id": "req-1",
                "seq": events[0]["seq"],
                "stamps": _stamp(events[0]["addresses"]),
            }
        )
        assert events[1]["ok"] is True
        assert events[1]["ref"] == events[0]["addresses"][-1]
        assert events[1]["url"] == f"https://bzz.limo/bytes/{events[1]['ref']}?x=.aac"
        assert len(publisher.frames) == len(events[0]["addresses"])

    asyncio.run(scenario())
