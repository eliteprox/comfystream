import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "deployment_renderer", ROOT / "scripts/configure_capability_deployment.py"
)
renderer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(renderer)


def inventory():
    apps = {
        "flux-image": "text-to-image",
        "birefnet": "image-edit",
        "whisper": "transcription",
        "ace-music": "music",
        "triposr": "image-to-3d",
    }
    return {
        "host": "test-node",
        "lan_ip": "192.168.10.30",
        "public_url": "https://runner.example:8936",
        "network": "arbitrum-one-mainnet",
        "account": "0x" + "1" * 40,
        "orchestrator_address": "0x" + "2" * 40,
        "node_data_dir": "/node/data",
        "keystore_file": "/node/key.json",
        "password_file": "/node/password",
        "ticket_face_value": "5999999999998",
        "ticket_win_prob": str(2**255),
        "runner_grants": ["127.0.0.1"],
        "session_proxy_grants": ["127.0.0.1"],
        "metrics_port": 7935,
        "catalog_image": "example/catalog@sha256:" + "a" * 64,
        "catalog_runtime_image": "sha256:" + "a" * 64,
        "catalog_state": "/catalog",
        "capacity_group": "test-gpu",
        "gpu_uuid": "GPU-00000000-0000-0000-0000-000000000000",
        "model_cache": "/cache",
        "workspace_models": "/models",
        "checkpoints": "/checkpoints",
        "models": {
            name: {"app": app, "runner_id": "test-" + name, "price": "0.02"}
            for name, app in apps.items()
        },
    }


def test_renderer_passes_secrets_explicitly_and_preserves_model_identity():
    value = inventory()
    value["catalog_extra_mounts"] = [
        {"type": "bind", "source": "/flux-cache", "target": "/model-cache/flux", "read_only": True}
    ]
    compose, deployment = renderer.render(value)
    node = compose["services"]["node"]
    assert "LIVEPEER_ORCHESTRATOR_KEYSTORE_PASSWORD" not in node["environment"]
    assert node["environment"]["LIVEPEER_ORCHESTRATOR_BOOTSTRAP_SECRET"].startswith("${")
    catalog = compose["services"]["catalog"]
    assert "env_file" not in catalog
    assert catalog["environment"]["LIVEPEER_ORCH_SECRET"].startswith("${")
    assert value["catalog_extra_mounts"][0] in catalog["volumes"]
    assert deployment["models"] == value["models"]


def test_renderer_rejects_model_name_app_ids():
    value = inventory()
    value["models"]["flux-image"]["app"] = "flux-klein"
    with pytest.raises(ValueError, match="capability App ID"):
        renderer.render(value)


def test_renderer_rejects_implicit_fal_advertising():
    value = inventory()
    service = {
        "image": "sha256:" + "b" * 64,
        "network_mode": "host",
        "restart": "unless-stopped",
        "command": [],
    }
    value["runner_services"] = {name: dict(service) for name in ["live", "ffmpeg", "swarm"]}
    value["runner_services"]["live"]["command"] = ["--enable-fal"]
    with pytest.raises(ValueError, match="FAL is disabled"):
        renderer.render(value)
