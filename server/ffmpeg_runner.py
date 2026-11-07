"""Compatibility entrypoint for the installed FFmpeg runner."""
import sys
from comfystream.server import ffmpeg_runner as _implementation
if __name__ == "__main__":
    _implementation.main()
else:
    sys.modules[__name__] = _implementation
