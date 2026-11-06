"""Materialize the exact shared registry revision; preserve local changes."""

import argparse
import json
import os
import subprocess
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--lock",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "contracts/runner-registry.lock.json",
    )
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    lock = json.loads(args.lock.read_text())
    revision = lock["revision"]
    if len(revision) != 40 or any(c not in "0123456789abcdef" for c in revision):
        raise ValueError("Registry lock requires an immutable Git commit")
    destination = args.destination.resolve()
    environment = dict(os.environ, GIT_TERMINAL_PROMPT="0")

    def git(*arguments, capture=False):
        result = subprocess.run(
            ["git", "-C", str(destination), *arguments],
            env=environment,
            check=True,
            text=True,
            stdout=subprocess.PIPE if capture else None,
        )
        return result.stdout.strip() if capture else None

    if destination.exists():
        if not (destination / ".git").exists():
            raise ValueError("Destination exists and is not a registry checkout")
        if git("status", "--porcelain", capture=True):
            raise ValueError("Registry checkout has local changes; refusing to overwrite")
    else:
        subprocess.run(
            ["git", "clone", "--no-checkout", lock["repository"], str(destination)],
            env=environment,
            check=True,
        )
    git("fetch", "--depth", "1", "origin", revision)
    git("checkout", "--detach", revision)
    if git("rev-parse", "HEAD", capture=True) != revision:
        raise RuntimeError("Registry revision differs from lock")
    print("Registry pinned to " + revision + "; release=" + str(lock.get("release", False)))


if __name__ == "__main__":
    main()
