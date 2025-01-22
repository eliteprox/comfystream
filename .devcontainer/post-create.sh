#!/bin/bash

# Create a symlink to the ComfyUI workspace
if [ ! -d "/workspace/ComfyUI" ]; then
    ln -s /ComfyUI /workspace/ComfyUI
fi

# Create a symlink to the extra_model_paths.yaml file only if /ComfyUI/extra/models exists and /ComfyUI/extra_model_paths.yaml does not exist, otherwise remove the link
if [ -d "/ComfyUI/extra/models" ] && [ ! -L "/ComfyUI/extra_model_paths.yaml" ]; then
    ln -s /workspace/.devcontainer/extra_model_paths.yaml /ComfyUI/extra_model_paths.yaml
else
    if [ -L "/ComfyUI/extra_model_paths.yaml" ]; then
        rm /ComfyUI/extra_model_paths.yaml
    fi
fi

# Create a symlink to the tensor_utils directory to make it easier to develop comfystream nodes
if [ ! -d "/ComfyUI/custom_nodes/tensor_utils" ]; then
    ln -s /workspace/nodes/tensor_utils /ComfyUI/custom_nodes/tensor_utils
fi

# Initialize conda if needed
if ! command -v conda &> /dev/null; then
    /miniconda3/bin/conda init bash
fi

source ~/.bashrc
