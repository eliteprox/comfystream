"""Mutable heartbeat support for the exact gateway SDK pinned by this project."""

import asyncio


async def update_registration(registration, **changes):
    unsupported = set(changes) - {"metadata", "status"}
    if unsupported:
        raise ValueError("Unsupported heartbeat mutation")
    update = getattr(registration, "update", None)
    if callable(update):
        return await update(**changes)
    # The pinned upstream SDK stores these values in its heartbeat payload but
    # exposes no public mutator; keep this adapter isolated and regression tested.
    if not hasattr(registration, "_send_heartbeat"):
        raise TypeError("Installed gateway SDK has no mutable heartbeat transport")
    lock = getattr(registration, "_comfystream_update_lock", None)
    if lock is None:
        lock = registration._comfystream_update_lock = asyncio.Lock()
    async with lock:
        if "metadata" in changes:
            raw = changes["metadata"]
            if not isinstance(raw, str) or len(raw.encode("utf-8")) > 1024:
                raise ValueError("Invalid heartbeat metadata")
            registration._metadata = raw
        if "status" in changes:
            if changes["status"] not in {"ready", "busy"}:
                raise ValueError("Invalid runner status")
            registration._status = changes["status"]
        await registration._send_heartbeat()
