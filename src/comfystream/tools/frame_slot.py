"""Compatibility alias for Stream Pack workflow behavior."""
import sys

from stream_pack.video_tools import frame_slot as _implementation

sys.modules[__name__] = _implementation
