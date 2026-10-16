"""Keep LoadImage from crashing when Comfy's input directory is relative.

Validation calls folder_paths.get_input_directory(), which resolves a relative
``input`` folder against base_paths[0]. The process that validates a prompt
can be a fresh interpreter whose base-path list is empty. This module installs
an import hook so every process in the runner, including that one, returns the
workspace directories instead.
"""

from __future__ import annotations

import builtins
import os
import sys
from pathlib import Path
from types import ModuleType

_INSTALLED = False
_REAL_IMPORT = builtins.__import__


def install() -> None:
    global _INSTALLED
    existing = sys.modules.get("comfy.cmd.folder_paths")
    if existing is not None:
        _patch(existing)
    if _INSTALLED:
        return
    _INSTALLED = True
    builtins.__import__ = _import_and_pin


def _import_and_pin(name, globals=None, locals=None, fromlist=(), level=0):
    module = _REAL_IMPORT(name, globals, locals, fromlist, level)
    target = sys.modules.get("comfy.cmd.folder_paths")
    if target is not None and not getattr(target, "_comfystream_pinned", False):
        _patch(target)
    return module


def _patch(module: ModuleType) -> None:
    if getattr(module, "_comfystream_pinned", False):
        return
    # folder_paths is imported by its own dependencies before its functions exist.
    # Patching then would be overwritten when the module body finishes, and the
    # flag would block the real patch.
    if not callable(getattr(module, "get_input_directory", None)):
        return
    if not callable(getattr(module, "_folder_names_and_paths", None)):
        return
    module._comfystream_pinned = True
    root = Path(os.environ.get("COMFYUI_CWD") or "/workspace/ComfyUI")
    input_dir = root / "input"
    output_dir = root / "output"
    temp_dir = root / "temp"
    for path in (input_dir, output_dir, temp_dir):
        path.mkdir(parents=True, exist_ok=True)

    def get_input_directory(mkdirs: bool = True) -> str:
        if mkdirs:
            input_dir.mkdir(parents=True, exist_ok=True)
        return str(input_dir)

    def get_output_directory() -> str:
        return str(output_dir)

    def get_temp_directory() -> str:
        return str(temp_dir)

    def safe_base_path() -> Path:
        names = module._folder_names_and_paths()
        paths = names.base_paths
        if not paths:
            paths.append(root)
        application = getattr(names, "application_paths", None)
        if application is not None:
            current = getattr(application, "input_directory", None)
            if current is None or not Path(current).is_absolute():
                application.input_directory = input_dir
            current_output = getattr(application, "output_directory", None)
            if current_output is None or not Path(current_output).is_absolute():
                application.output_directory = output_dir
        return paths[0]

    wrapped = getattr(module, "_base_path", None)
    closure = getattr(wrapped, "__closure__", None) or ()
    for cell in closure:
        try:
            inner = cell.cell_contents
        except ValueError:
            continue
        if getattr(inner, "__name__", None) == "_base_path":
            cell.cell_contents = safe_base_path

    module.get_input_directory = get_input_directory
    module.get_output_directory = get_output_directory
    module.get_temp_directory = get_temp_directory
    module._base_path = safe_base_path


install()
