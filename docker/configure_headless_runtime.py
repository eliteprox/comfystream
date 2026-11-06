"""Configure the existing CUDA runtime without replacing its model assets."""

import os
import site
import sys
from pathlib import Path


def archive(path, destination):
    if path.exists() or path.is_symlink():
        if destination.exists() or destination.is_symlink():
            raise RuntimeError(f"Refusing to overwrite runtime backup: {destination}")
        path.rename(destination)


def main():
    role = sys.argv[1]
    if role not in {"catalog", "live", "ffmpeg"}:
        raise ValueError("Unknown runner role")
    packages = Path(site.getsitepackages()[0])
    # A legacy loader takes precedence over the lockfile's NumPy-compatible wheel.
    loader = packages / "cv2" / f"config-{sys.version_info.major}.{sys.version_info.minor}.py"
    if loader.exists():
        archive(loader, loader.with_suffix(".py.legacy"))
    manager = Path("/workspace/ComfyUI/custom_nodes/ComfyUI-Manager/prestartup_script.py")
    if manager.exists():
        archive(manager, manager.with_suffix(".py.disabled-for-headless-runner"))
    custom = Path("/workspace/ComfyUI/custom_nodes")
    custom.mkdir(parents=True, exist_ok=True)
    archive(custom / "comfystream", Path("/opt/legacy-comfystream-custom-node"))
    canonical = custom / "ComfyStreamNodes"
    if canonical.is_symlink() and canonical.resolve() == Path("/opt/migration-source"):
        pass
    else:
        archive(canonical, Path("/opt/legacy-ComfyStreamNodes"))
        canonical.symlink_to("/opt/migration-source")
    # File tools need this in grandchildren too. Other roles keep their own
    # request-specific input directories instead of forcing the FFmpeg paths.
    (packages / "comfystream-ffmpeg-paths.pth").write_text(
        "import os; os.environ.get('COMFYSTREAM_RUNNER_ROLE') == 'ffmpeg' and __import__('comfystream_ffmpeg_paths')\n"
    )
    selected = Path("/usr/local/bin/comfystream-selected-runner")
    if selected.is_symlink():
        selected.unlink()
    elif selected.exists():
        raise RuntimeError("Selected runner path is occupied")
    selected.symlink_to(Path(sys.prefix) / "bin" / f"comfystream-{role}-runner")
    for name in ("catalog", "live", "ffmpeg", "capability"):
        if not os.access(Path(sys.prefix) / "bin" / f"comfystream-{name}-runner", os.X_OK):
            raise RuntimeError(f"Missing installed {name} runner entrypoint")


if __name__ == "__main__":
    main()
