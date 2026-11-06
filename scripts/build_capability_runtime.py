"""Build a runner from committed source and a verified existing base image."""

import argparse
import hashlib
import io
import json
import subprocess
import tarfile
import tempfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-image", required=True)
    parser.add_argument("--role", choices=("catalog", "live", "ffmpeg", "swarm"), required=True)
    parser.add_argument("--venv")
    parser.add_argument("--uv-bin", default="uv")
    parser.add_argument("--tag", required=True)
    parser.add_argument("--record", type=Path, required=True)
    args = parser.parse_args()
    venv = args.venv or ("/usr/local" if args.role == "swarm" else "/opt/venv")
    repo = Path(__file__).resolve().parents[1]
    subprocess.run(["git", "diff", "--quiet", "HEAD"], cwd=repo, check=True)
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    source = subprocess.check_output(["git", "archive", "--format=tar", revision], cwd=repo)
    source_hash = hashlib.sha256(source).hexdigest()
    lock = json.loads((repo / "contracts/runner-registry.lock.json").read_text())
    image = json.loads(subprocess.check_output(["docker", "image", "inspect", args.base_image]))[0][
        "Id"
    ]
    base_tag = "comfystream-build-base:" + image.removeprefix("sha256:")
    subprocess.run(["docker", "tag", image, base_tag], check=True)
    with tempfile.TemporaryDirectory(prefix="comfystream-build-") as directory:
        context = Path(directory)
        with tarfile.open(fileobj=io.BytesIO(source)) as archive:
            archive.extractall(context, filter="data")
        dockerfile = (
            "Dockerfile.swarm-contract" if args.role == "swarm" else "Dockerfile.capability-runtime"
        )
        subprocess.run(
            [
                "docker",
                "build",
                "-f",
                str(context / "docker" / dockerfile),
                "--build-arg",
                "BASE_IMAGE=" + base_tag,
                "--build-arg",
                "VENV=" + venv,
                "--build-arg",
                "UV_BIN=" + args.uv_bin,
                "--build-arg",
                "RUNNER_ROLE=" + args.role,
                "--build-arg",
                "SOURCE_REVISION=" + revision,
                "--build-arg",
                "REGISTRY_REVISION=" + lock["revision"],
                "--build-arg",
                "SOURCE_ARCHIVE_SHA256=" + source_hash,
                "-t",
                args.tag,
                str(context),
            ],
            check=True,
        )
    built = json.loads(subprocess.check_output(["docker", "image", "inspect", args.tag]))[0]
    record = {
        "source_revision": revision,
        "source_archive_sha256": source_hash,
        "registry_revision": lock["revision"],
        "base_image_id": image,
        "image_id": built["Id"],
        "role": args.role,
        "python_venv": venv,
        "tag": args.tag,
        "registry_published_image": bool(built.get("RepoDigests")),
    }
    args.record.write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps(record))


if __name__ == "__main__":
    main()
