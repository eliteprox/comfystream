"""Render a host-specific node stack from an explicit, secret-free inventory.

Does not deploy, rename registrations, copy wallets, or choose payment policy.
The resulting stack must pass preflight and acceptance before cutover.
"""

import argparse
import ipaddress
import json
import os
import re
from pathlib import Path

NODE_IMAGE = "livepeer/node@sha256:677f8b1aa8ee1c31ede2b258665ffaad20410564cb81480b84c52da291bf4d84"
REQUIRED = {
    "host",
    "lan_ip",
    "public_url",
    "network",
    "account",
    "orchestrator_address",
    "node_data_dir",
    "keystore_file",
    "password_file",
    "ticket_face_value",
    "ticket_win_prob",
    "runner_grants",
    "session_proxy_grants",
    "metrics_port",
    "catalog_image",
    "catalog_state",
    "capacity_group",
    "gpu_uuid",
    "model_cache",
    "workspace_models",
    "checkpoints",
    "models",
}
OPTIONAL = {"runner_services", "catalog_runtime_image", "catalog_extra_mounts"}


def render(config):
    if not REQUIRED <= set(config) or set(config) - REQUIRED - OPTIONAL:
        raise ValueError(
            f"Inventory keys differ: missing={sorted(REQUIRED - set(config))}, unknown={sorted(set(config) - REQUIRED - OPTIONAL)}"
        )
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", config["host"]):
        raise ValueError("Invalid host identity")
    ip = ipaddress.ip_address(config["lan_ip"])
    if not ip.is_private or ip.is_loopback or ip.version != 4:
        raise ValueError("lan_ip must be a private IPv4 backend address")
    if not re.fullmatch(r"https://[a-zA-Z0-9.-]+:8936", config["public_url"]):
        raise ValueError("Public URL must be HTTPS on port 8936")
    for key in ("account", "orchestrator_address"):
        if not re.fullmatch(r"0x[0-9a-fA-F]{40}", config[key]):
            raise ValueError(f"Invalid {key}")
    for key in (
        "node_data_dir",
        "keystore_file",
        "password_file",
        "catalog_state",
        "model_cache",
        "workspace_models",
        "checkpoints",
    ):
        if not config[key].startswith("/") or "\n" in config[key]:
            raise ValueError(f"{key} must be an absolute Linux path")
    if not re.fullmatch(r".+@sha256:[0-9a-f]{64}", config["catalog_image"]):
        raise ValueError("Catalog image must be pinned by digest")
    if not re.fullmatch(r"GPU-[a-f0-9-]+", config["gpu_uuid"]):
        raise ValueError("Use the host's GPU UUID, not an ordinal")
    if (
        not isinstance(config["metrics_port"], int)
        or not 1024 <= config["metrics_port"] <= 65535
        or config["metrics_port"] in {8936, 8993}
    ):
        raise ValueError("Invalid or conflicting metrics port")
    for key in ("ticket_face_value", "ticket_win_prob"):
        if not str(config[key]).isdigit() or int(config[key]) <= 0:
            raise ValueError(f"An explicit positive {key} is required")
    if int(config["ticket_win_prob"]) >= 2**256 - 1:
        raise ValueError("Ticket win probability exceeds uint256")
    for key in ("runner_grants", "session_proxy_grants"):
        if (
            not isinstance(config[key], list)
            or not config[key]
            or any(not isinstance(v, str) or not v or "\n" in v for v in config[key])
        ):
            raise ValueError(f"Explicit nonempty {key} are required")
    from decimal import Decimal

    if set(config["models"]) != {"flux-image", "birefnet", "whisper", "ace-music", "triposr"}:
        raise ValueError("All five native batch models must be mapped explicitly")
    ids, apps = set(), set()
    canonical_apps = {
        "flux-image": "text-to-image",
        "birefnet": "image-edit",
        "whisper": "transcription",
        "ace-music": "music",
        "triposr": "image-to-3d",
    }
    for capability, model in config["models"].items():
        if set(model) != {"app", "runner_id", "price"}:
            raise ValueError("Model fields must be app, runner_id, price")
        for key in ("app", "runner_id"):
            if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", model[key]):
                raise ValueError(f"Invalid model {key}")
        if model["app"] != canonical_apps[capability]:
            raise ValueError(
                f"{capability} must register capability App ID {canonical_apps[capability]}"
            )
        price = Decimal(str(model["price"]))
        if not price.is_finite() or price <= 0:
            raise ValueError("Explicit positive model prices are required")
        ids.add(model["runner_id"])
        apps.add(model["app"])
    if len(ids) != 5 or len(apps) != 5:
        raise ValueError("Duplicate batch registration identity")
    env = {
        "DATA_DIR": "/var/lib/livepeer",
        "NETWORK": config["network"],
        "LISTEN": f"unsafe@{ip}:8936",
        "METRICS_LISTEN": f"127.0.0.1:{config['metrics_port']}",
        "SERVICE_URL": config["public_url"],
        "RUNNER_SERVICE_URL": config["public_url"],
        "ACCOUNT": config["account"],
        "ORCHESTRATOR_ADDRESS": config["orchestrator_address"],
        "KEYSTORE_FILE": "/run/secrets/keystore.json",
        "KEYSTORE_PASSWORD_FILE": "/run/secrets/password",
        "TICKET_FACE_VALUE": str(config["ticket_face_value"]),
        "TICKET_WIN_PROB": str(config["ticket_win_prob"]),
        "RUNNER_GRANTS": ",".join(config["runner_grants"]),
        "SESSION_PROXY_GRANTS": ",".join(config["session_proxy_grants"]),
        "HEARTBEAT_INTERVAL": "10s",
        "HEARTBEAT_TTL": "1m",
        "BOOTSTRAP_SECRET": "${LIVEPEER_ORCHESTRATOR_BOOTSTRAP_SECRET:?required}",
        "RPC_URL": "${LIVEPEER_ORCHESTRATOR_RPC_URL:?required}",
    }

    def bind(source, target, readonly=False):
        return {"type": "bind", "source": source, "target": target, "read_only": readonly}

    compose = {
        "name": config["host"] + "-capabilities",
        "services": {
            "node": {
                "image": NODE_IMAGE,
                "network_mode": "host",
                "user": "1000:1000",
                "restart": "unless-stopped",
                "command": ["orchestrator", "--config="],
                "environment": {"LIVEPEER_ORCHESTRATOR_" + k: v for k, v in env.items()},
                "volumes": [
                    bind(config["node_data_dir"], "/var/lib/livepeer"),
                    bind(config["keystore_file"], "/run/secrets/keystore.json", True),
                    bind(config["password_file"], "/run/secrets/password", True),
                ],
            },
            "catalog": {
                "image": config.get("catalog_runtime_image", config["catalog_image"]),
                "network_mode": "host",
                "restart": "unless-stopped",
                "entrypoint": ["comfystream-catalog-runner"],
                "command": ["--config", "/state/deployment.json"],
                "environment": {
                    "LIVEPEER_ORCH_URL": f"http://{ip}:8936",
                    "LIVEPEER_ORCH_SECRET": "${LIVEPEER_ORCHESTRATOR_BOOTSTRAP_SECRET:?required}",
                    "COMFYUI_CWD": "/workspace/ComfyUI",
                    "COMFYSTREAM_CAPACITY_FILE": "/state/gpu.lock",
                    "COMFYSTREAM_CAPABILITY_STATE": "/state",
                    "CUDA_VISIBLE_DEVICES": config["gpu_uuid"],
                    "HF_HUB_CACHE": "/model-cache",
                    "HF_HUB_OFFLINE": "1",
                },
                "deploy": {
                    "resources": {
                        "reservations": {
                            "devices": [
                                {
                                    "driver": "nvidia",
                                    "device_ids": [config["gpu_uuid"]],
                                    "capabilities": ["gpu"],
                                }
                            ]
                        }
                    }
                },
                "volumes": [
                    bind(config["catalog_state"], "/state"),
                    bind(config["model_cache"], "/model-cache", True),
                    bind(config["workspace_models"], "/workspace/ComfyUI/models", True),
                    bind(config["checkpoints"], "/workspace/ComfyUI/models/checkpoints", True),
                ],
            },
        },
    }
    runtime = compose["services"]["catalog"]["image"]
    if not re.fullmatch(r"(?:sha256:[0-9a-f]{64}|.+@sha256:[0-9a-f]{64})", runtime):
        raise ValueError("Catalog runtime must be an immutable local image ID or repository digest")
    for mount in config.get("catalog_extra_mounts", []):
        if (
            mount.get("type") != "bind"
            or not mount.get("source", "").startswith("/")
            or not mount.get("target", "").startswith(
                ("/model-cache/", "/workspace/ComfyUI/models/")
            )
            or mount.get("read_only") is not True
        ):
            raise ValueError(
                "Additional catalog model mounts must be absolute read-only model binds"
            )
        compose["services"]["catalog"]["volumes"].append(mount)
    if "runner_services" in config:
        services = config["runner_services"]
        if set(services) != {"live", "ffmpeg", "swarm"}:
            raise ValueError("Explicit live, ffmpeg, and swarm service definitions are required")
        for role, service in services.items():
            if not re.fullmatch(
                r"(?:sha256:[0-9a-f]{64}|.+@sha256:[0-9a-f]{64})", service.get("image", "")
            ):
                raise ValueError(f"{role}: image must be immutable")
            if service.get("network_mode") != "host" or service.get("restart") != "unless-stopped":
                raise ValueError(f"{role}: host networking and managed restart are required")
            if not isinstance(service.get("command"), list):
                raise ValueError(f"{role}: command must be an argument array")
            if role == "live" and "--skip-batch" not in service["command"]:
                raise ValueError(
                    "FAL is disabled for this deployment; live command requires --skip-batch"
                )
            for key, value in service.get("environment", {}).items():
                if (
                    key
                    in {"LIVEPEER_ORCH_SECRET", "LIVEPEER_ORCHESTRATOR_BOOTSTRAP_SECRET", "FAL_KEY"}
                    and value
                    and not value.startswith("${")
                ):
                    raise ValueError(f"{role}: secret {key} must use stack interpolation")
            compose["services"][role] = service
    deployment = {
        "image": config["catalog_image"],
        "capacity_group": config["capacity_group"],
        "models": config["models"],
    }
    return compose, deployment


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    compose, deployment = render(json.loads(args.inventory.read_text()))
    args.output.mkdir(mode=0o700, parents=True, exist_ok=True)
    for name, document in (("compose.json", compose), ("deployment.json", deployment)):
        destination = args.output / name
        fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as handle:
            json.dump(document, handle, indent=2)
            handle.write("\n")
    print(
        f"Staged {len(compose['services'])} services in {args.output}; validate profiles, host paths, image availability and health before cutover"
    )


if __name__ == "__main__":
    main()
