"""Compatibility alias for Stream Pack workflow behavior."""
import sys

from stream_pack.video_tools import container as _implementation

sys.modules[__name__] = _implementation
