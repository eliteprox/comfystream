"""ComfyStream public API; discovery does not initialize model libraries."""

from importlib import import_module

_EXPORTS = {
    "ComfyStreamClient": "client",
    "CapabilityClient": "capability_client",
    "Pipeline": "pipeline",
    "PipelineState": "pipeline_state",
    "PipelineStateManager": "pipeline_state",
    "temporary_log_level": "server.utils",
    "FPSMeter": "server.utils",
    "MetricsManager": "server.metrics",
    "StreamStatsManager": "server.metrics",
    "ComfyStreamInputTimeoutError": "exceptions",
    "ComfyStreamAudioBufferError": "exceptions",
}
__all__ = list(_EXPORTS)


def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(name)
    from comfy_compatibility.imports import MAIN_PY, SITE_PACKAGES, ImportContext

    with ImportContext("comfy", "comfy_extras", "comfy.vendor", order=[SITE_PACKAGES, MAIN_PY]):
        value = getattr(import_module("." + _EXPORTS[name], __name__), name)
    globals()[name] = value
    return value
