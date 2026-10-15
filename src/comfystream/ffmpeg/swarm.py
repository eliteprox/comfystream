"""Caller-stamped Swarm upload for FFmpeg outputs.

The runner chunks with pyramid and keeps the bodies. The caller signs each
address. Publish posts stamp || chunk to Bee and does not return the file.
"""

from __future__ import annotations

import io
import logging
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import av
from livepeer_swarm.bmt import chunk_address, chunk_data, pyramid
from livepeer_swarm.frame import pack
from livepeer_swarm.playback import playback_url
from livepeer_swarm.publisher import SwarmPublisher, SwarmPublishError
from livepeer_swarm.stamp import STAMP_SIZE, StampedChunk

log = logging.getLogger("comfystream.ffmpeg.swarm")

HOLD_TTL_S = 600.0
HOLD_MAX = 2
AAC_BIT_RATE = 48_000


class SwarmProtocolError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass
class _Held:
    chunks: list[tuple[int, bytes]]
    root: str
    created: float
    content_type: str


@dataclass
class SwarmHold:
    """At most two unpublished files, dropped after ten minutes."""

    ttl_s: float = HOLD_TTL_S
    max_held: int = HOLD_MAX
    _items: dict[str, _Held] = field(default_factory=dict)
    _order: deque[str] = field(default_factory=deque)

    def offer(self, data: bytes, content_type: str, request_id: str | None = None) -> dict[str, Any]:
        self._expire()
        request_id = request_id or uuid.uuid4().hex
        chunks = pyramid(data)
        addresses = [chunk_address(span, body).hex() for span, body in chunks]
        root = addresses[-1]
        while len(self._items) >= self.max_held:
            self._drop(self._order[0])
        self._items[request_id] = _Held(
            chunks=chunks,
            root=root,
            created=time.monotonic(),
            content_type=content_type,
        )
        self._order.append(request_id)
        return {
            "bytes": len(data),
            "root": root,
            "addresses": addresses,
            "content_type": content_type,
            "request_id": request_id,
        }

    async def publish(
        self,
        request_id: str,
        stamps: list[str],
        publisher: SwarmPublisher,
    ) -> dict[str, str]:
        self._expire()
        held = self._items.get(request_id)
        if held is None:
            raise SwarmProtocolError(404, "unknown or expired request_id")
        if len(stamps) != len(held.chunks):
            raise SwarmProtocolError(400, "stamp count does not match addresses")
        prepared: list[StampedChunk] = []
        for (span, body), stamp_hex in zip(held.chunks, stamps, strict=True):
            stamp = _stamp_bytes(stamp_hex)
            prepared.append(
                StampedChunk(
                    address=chunk_address(span, body),
                    stamp=stamp,
                    chunk=chunk_data(span, body),
                    payload=body,
                    span=span,
                )
            )
        try:
            for stamped in prepared:
                await publisher.put(pack(stamped))
        except SwarmPublishError:
            raise
        self._drop(request_id)
        return {
            "reference": held.root,
            "url": playback_url(held.root, _playback_ext(held.content_type)),
        }

    def _expire(self) -> None:
        now = time.monotonic()
        stale = [key for key, item in self._items.items() if now - item.created >= self.ttl_s]
        for key in stale:
            self._drop(key)

    def _drop(self, request_id: str) -> None:
        self._items.pop(request_id, None)
        self._order = deque(key for key in self._order if key != request_id)


def file_descriptor(hold: SwarmHold | None, data: bytes, content_type: str, url: str) -> dict[str, Any]:
    """Swarm address object when a hold is configured, otherwise a file URL."""
    if hold is None:
        return {"url": url, "content_type": content_type, "file_size": len(data)}
    offered = hold.offer(data, content_type)
    return {
        "bytes": offered["bytes"],
        "root": offered["root"],
        "addresses": offered["addresses"],
        "content_type": offered["content_type"],
        "request_id": offered["request_id"],
    }


class AudioSwarmSession:
    """Publish one compressed audio segment. Video frames are not accepted here."""

    def __init__(
        self,
        publisher: SwarmPublisher,
        on_event: Callable[[dict[str, Any]], Awaitable[None]],
    ):
        self.publisher = publisher
        self.on_event = on_event
        self._seq = 0
        self._pending: dict[int, list[tuple[int, bytes]]] = {}
        self._start_s = 0.0

    async def push(self, data: bytes) -> None:
        if data:
            await self._open(data)

    async def receive_stamps(self, body: dict[str, Any]) -> None:
        seq = int(body["seq"]) if "seq" in body else self._only_seq(body.get("req_id"))
        stamps = body.get("stamps")
        if not isinstance(stamps, list):
            await self.on_event({"seq": seq, "ok": False, "error": "stamps must be a list"})
            return
        chunks = self._pending.get(seq)
        if chunks is None:
            await self.on_event({"seq": seq, "ok": False, "error": "unknown seq"})
            return
        if len(stamps) != len(chunks):
            await self.on_event({"seq": seq, "ok": False, "error": "stamp count"})
            return
        try:
            for (span, payload), stamp_hex in zip(chunks, stamps, strict=True):
                stamp = _stamp_bytes(str(stamp_hex))
                stamped = StampedChunk(
                    address=chunk_address(span, payload),
                    stamp=stamp,
                    chunk=chunk_data(span, payload),
                    payload=payload,
                    span=span,
                )
                await self.publisher.put(pack(stamped))
        except (SwarmPublishError, SwarmProtocolError) as exc:
            await self.on_event({"seq": seq, "ok": False, "error": str(exc)})
            return
        root = chunk_address(*chunks[-1]).hex()
        del self._pending[seq]
        await self.on_event(
            {
                "seq": seq,
                "ok": True,
                "ref": root,
                "url": playback_url(root, ".aac"),
                "req_id": body.get("req_id"),
            }
        )

    async def _open(self, piece: bytes) -> None:
        chunks = pyramid(piece)
        seq = self._seq
        self._seq += 1
        self._pending[seq] = chunks
        duration = 1.0
        event = {
            "seq": seq,
            "addresses": [chunk_address(span, body).hex() for span, body in chunks],
            "start": self._start_s,
            "duration": duration,
        }
        self._start_s += duration
        await self.on_event(event)

    def _only_seq(self, _req_id: Any) -> int:
        if len(self._pending) == 1:
            return next(iter(self._pending))
        return -1


class LiveAudioFeed:
    """Turn decoded input audio into about one second of AAC, then publish it.

    Video frames stay on MediaPublish. This feed never accepts them.
    """

    def __init__(self, session: AudioSwarmSession):
        self.session = session
        self._frames: list[av.AudioFrame] = []
        self._samples = 0

    async def push_frame(self, frame: av.AudioFrame) -> None:
        self._frames.append(frame)
        self._samples += int(frame.samples or 0)
        rate = int(frame.sample_rate or 48000)
        if self._samples < rate:
            return
        frames = self._frames
        self._frames = []
        self._samples = 0
        try:
            encoded = encode_aac(frames)
        except Exception:
            log.exception("aac encode failed; audio segment dropped")
            return
        await self.session.push(encoded)


def encode_aac(frames: list[av.AudioFrame]) -> bytes:
    """AAC-ADTS at 48 kbps. One second is one or two Swarm leaves."""
    if not frames:
        return b""
    rate = int(frames[0].sample_rate or 48000)
    buffer = io.BytesIO()
    container = av.open(buffer, mode="w", format="adts")
    try:
        stream = container.add_stream("aac", rate=rate)
        stream.bit_rate = AAC_BIT_RATE
        resampler = av.AudioResampler(format="fltp", layout="mono", rate=rate)
        for frame in frames:
            converted = resampler.resample(frame)
            if converted is None:
                continue
            if not isinstance(converted, list):
                converted = [converted]
            for item in converted:
                for packet in stream.encode(item):
                    container.mux(packet)
        flushed = resampler.resample(None)
        if flushed is None:
            flushed = []
        elif not isinstance(flushed, list):
            flushed = [flushed]
        for item in flushed:
            for packet in stream.encode(item):
                container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
    finally:
        container.close()
    return buffer.getvalue()


def _playback_ext(content_type: str) -> str:
    kind, _, subtype = content_type.partition("/")
    subtype = subtype.split(";", 1)[0].strip().lower()
    if kind == "image" and subtype == "png":
        return ".png"
    if kind == "audio":
        return {"flac": ".flac", "wav": ".wav", "mpeg": ".mp3", "mp4": ".m4a"}.get(subtype, ".aac")
    if kind == "video":
        return ".mp4"
    return ".bin"


def _stamp_bytes(value: str) -> bytes:
    text = value[2:] if value.startswith("0x") else value
    try:
        stamp = bytes.fromhex(text)
    except ValueError as exc:
        raise SwarmProtocolError(400, "stamp is not hex") from exc
    if len(stamp) != STAMP_SIZE:
        raise SwarmProtocolError(400, "stamp must be 113 bytes")
    return stamp
