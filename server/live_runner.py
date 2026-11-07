"""Compatibility entrypoint for the installed ComfyStream runner."""
import sys
from comfystream.server import live_runner as _implementation
if __name__ == "__main__":
    _implementation.main()
else:
    sys.modules[__name__] = _implementation
