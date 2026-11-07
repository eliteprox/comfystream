"""Compatibility alias for Stream Pack model behavior."""
import sys
from stream_pack.legacy import flux_klein as _implementation
sys.modules[__name__] = _implementation
