"""Compatibility alias for Stream Pack workflow nodes."""
import sys
from stream_pack.legacy import flux_klein_stream as _implementation
sys.modules[__name__] = _implementation
