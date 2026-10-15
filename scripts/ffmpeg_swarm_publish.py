#!/usr/bin/env python3
"""Stamp an FFmpeg batch receipt and submit the publish jobs call.

The runner already chunked the file. This process signs addresses with
SWARM_SIGNER_KEY and SWARM_BATCH_ID, then posts the stamps. It does not
write the media file.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from eth_account import Account
from livepeer_gateway.live_runner import call_runner
from livepeer_gateway.selection import runner_selector
from livepeer_swarm.stamp import StampIssuer

DEFAULT_DISCOVERY = "https://localhost:8935/discovery"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one FFmpeg batch tool and publish its Swarm reference."
    )
    parser.add_argument("tool", help="Batch tool name, for example scale-video.")
    parser.add_argument("--input-json", type=Path, required=True)
    parser.add_argument("--discovery", default=os.environ.get("DISCOVERY_URL", DEFAULT_DISCOVERY))
    parser.add_argument("--signer", default="")
    parser.add_argument("--timeout", type=float, default=180.0)
    return parser.parse_args()


def _output(receipt: dict[str, Any]) -> dict[str, Any]:
    for key in ("video", "audio"):
        value = receipt.get(key)
        if isinstance(value, dict) and isinstance(value.get("addresses"), list):
            return value
    images = receipt.get("images")
    if isinstance(images, list) and images and isinstance(images[0], dict):
        if isinstance(images[0].get("addresses"), list):
            return images[0]
    raise SystemExit("batch receipt has no addresses")


def _stamps(addresses: list[str]) -> list[str]:
    key = os.environ.get("SWARM_SIGNER_KEY", "").strip()
    batch_hex = os.environ.get("SWARM_BATCH_ID", "").strip()
    if not key or not batch_hex:
        raise SystemExit("SWARM_SIGNER_KEY and SWARM_BATCH_ID are required")
    if batch_hex.startswith("0x"):
        batch_hex = batch_hex[2:]
    batch_id = bytes.fromhex(batch_hex)
    account = Account.from_key(key)
    issuer = StampIssuer(batch_id)
    return [issuer.stamp(bytes.fromhex(address), account).hex() for address in addresses]


async def _jobs(
    discovery: str,
    app: str,
    payload: dict[str, Any],
    signer_url: str | None,
    timeout: float,
) -> dict[str, Any]:
    cursor = await runner_selector(
        discovery_url=discovery,
        app=app,
        signer_url=signer_url,
        timeout=timeout,
    )
    try:
        discovered = cursor.candidates[0]
    except IndexError as exc:
        raise SystemExit(f"discovery returned no runner for {app}") from exc
    result = await call_runner(
        runner=discovered,
        payload=payload,
        signer_url=signer_url,
        timeout=timeout,
    )
    data = result.data
    if not isinstance(data, dict):
        raise SystemExit(f"{app} did not return a JSON object")
    return data


async def _amain() -> None:
    args = _parse_args()
    body = json.loads(args.input_json.expanduser().read_text(encoding="utf-8"))
    if not isinstance(body, dict):
        raise SystemExit("input JSON must be an object")
    signer = args.signer.strip() or None
    receipt = await _jobs(
        args.discovery,
        f"comfystream/ffmpeg-{args.tool}",
        body,
        signer,
        args.timeout,
    )
    media = _output(receipt)
    published = await _jobs(
        args.discovery,
        "comfystream/ffmpeg-publish",
        {"request_id": media["request_id"], "stamps": _stamps(list(media["addresses"]))},
        signer,
        args.timeout,
    )
    reference = published.get("reference")
    if not isinstance(reference, str) or not reference:
        raise SystemExit("publish response has no reference")
    url = published.get("url")
    print(url if isinstance(url, str) and url else reference)


def main() -> None:
    try:
        asyncio.run(_amain())
    except KeyboardInterrupt:
        raise SystemExit(130) from None


if __name__ == "__main__":
    sys.exit(main() or 0)
