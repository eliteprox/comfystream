import os
import sys
from pathlib import Path

import torch

# Initialize CUDA before other imports
if torch.cuda.is_available():
    torch.cuda.init()

from runner.app import start_app
from runner.live.pipelines import PipelineSpec

# Define the pipeline spec for ComfyRunner
pipeline_spec = PipelineSpec(
    name="comfy-runner",
    pipeline_cls="comfy_runner.pipeline.pipeline:ComfyRunner",
    params_cls="comfy_runner.params.params:ComfyParams",
)

def main():
    """Start the ComfyRunner ai-runner worker."""
    start_app(pipeline=pipeline_spec)

if __name__ == "__main__":
    main()

