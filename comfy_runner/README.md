# ComfyRunner

AI Runner integration for ComfyStream, enabling ComfyStream to run as an ai-runner worker.

## Overview

ComfyRunner is a pipeline adapter that integrates [ComfyStream](https://github.com/livepeer/comfystream) into the [Livepeer AI Runner](https://github.com/livepeer/ai-runner) framework. It allows ComfyUI workflows to be deployed as scalable, distributed video processing workers.

## Architecture

```mermaid
graph LR
    AIRunner[AI Runner Framework] --> ComfyRunner[ComfyRunner Adapter]
    ComfyRunner --> ComfyStream[ComfyStream Pipeline]
    ComfyStream --> ComfyUI[ComfyUI Engine]
```

## Project Structure

```
comfy_runner/
├── main.py              # Entrypoint for ai-runner worker
├── pipeline/
│   └── pipeline.py      # ComfyRunner class implementing ai-runner Pipeline interface
├── params/
│   └── params.py         # ComfyParams class defining pipeline parameters
├── docker/
│   └── Dockerfile        # Docker build configuration
├── pyproject.toml        # Package configuration
└── README.md             # This file
```

## Dependencies

- **comfystream**: The core ComfyUI streaming pipeline
- **ai-runner**: Livepeer's AI pipeline orchestration framework
- **torch**: PyTorch for GPU acceleration
- **numpy**: Numerical operations

## Installation

### From Source

```bash
# Install comfystream first (from parent directory)
cd ..
pip install -e .

# Install comfy-runner
cd comfy_runner
pip install -e .
```

### With Docker

Build the Docker image from the repository root:

```bash
# Build from comfyui-base
docker build -f comfy_runner/docker/Dockerfile -t comfy-runner:latest .
```

Or use the base image build process:

```bash
# Build comfyui-base first
docker build -f docker/Dockerfile.base -t livepeer/comfyui-base:latest .

# Build comfy-runner
docker build -f comfy_runner/docker/Dockerfile -t comfy-runner:latest .
```

## Usage

### Running the Worker

Start the ComfyRunner worker:

```bash
# From the comfy_runner directory
python main.py

# Or if installed as a package
python -m comfy_runner.main
```

The worker will:

1. Initialize CUDA if available
2. Register with the ai-runner framework
3. Wait for inference requests

### Running with Docker

```bash
docker run --gpus all -p 8000:8000 \
  -e ORCH_URL=<orchestrator-url> \
  -e ORCH_SECRET=<secret> \
  -e CAPABILITY_NAME=comfy-runner \
  comfy-runner:latest
```

### Environment Variables

- `COMFY_UI_WORKSPACE`: Path to ComfyUI workspace (default: `~/comfyui`)
- `MODEL_DIR`: Base model directory for ai-runner (will create `ComfyUI` subdirectory)
- `ORCH_URL`: Orchestrator URL for worker registration
- `ORCH_SECRET`: Authentication secret for orchestrator
- `CAPABILITY_NAME`: Name to register capability as (default: `comfy-runner`)
- `CAPABILITY_URL`: Public URL for this worker instance

## Pipeline Parameters

The `ComfyParams` class extends ai-runner's `BaseParams`:

```python
{
    "width": 512,          # Output width (384-1024, divisible by 64)
    "height": 512,         # Output height (384-1024, divisible by 64)
    "prompts": {...}       # ComfyUI workflow JSON
}
```

### Example Workflow

```json
{
  "prompts": {
    "1": {
      "class_type": "LoadImage",
      "inputs": { "image": "input" }
    },
    "2": {
      "class_type": "PreviewImage",
      "inputs": { "images": ["1", 0] }
    }
  }
}
```

## Model Preparation

Models are automatically downloaded on first use, but you can pre-download them:

```python
from comfy_runner.pipeline.pipeline import ComfyRunner

# Download all required models
ComfyRunner.prepare_models()
```

Or via command line:

```bash
python -c "from comfy_runner.pipeline.pipeline import ComfyRunner; ComfyRunner.prepare_models()"
```

## Development

### Local Development Setup

```bash
# Install in development mode
pip install -e ".[dev]"

# Run tests (if available)
pytest
```

### Building Docker Image for Development

```bash
# Build with local changes
docker build -f comfy_runner/docker/Dockerfile -t  livepeer/comfy-runner:dev .
```

## Integration with AI Runner

ComfyRunner implements the ai-runner `Pipeline` interface:

- `initialize(**params)`: Set up pipeline with initial parameters
- `put_video_frame(frame, request_id)`: Queue video frame for processing
- `get_processed_video_frame()`: Retrieve processed frame
- `update_params(**params)`: Update pipeline parameters dynamically
- `stop()`: Clean shutdown of pipeline
- `prepare_models()`: Download required models

## Differences from ComfyStream BYOC

ComfyRunner differs from the original ComfyStream BYOC server:

| Feature       | ComfyStream BYOC  | ComfyRunner              |
| ------------- | ----------------- | ------------------------ |
| Framework     | pytrickle         | ai-runner                |
| Architecture  | Standalone server | Distributed worker       |
| Orchestration | Self-contained    | Centralized orchestrator |
| Registration  | Optional          | Required                 |
| Scale         | Single instance   | Multi-worker             |

## Troubleshooting

### Models Not Found

Ensure `COMFY_UI_WORKSPACE` or `MODEL_DIR` is set correctly:

```bash
export COMFY_UI_WORKSPACE=/workspace/ComfyUI
```

### CUDA Initialization Failed

Check GPU availability:

```bash
python -c "import torch; print(torch.cuda.is_available())"
```

### Import Errors

Ensure both packages are installed:

```bash
pip install -e /path/to/comfystream
pip install -e /path/to/comfy_runner
```

## License

Same as ComfyStream parent project.

## Contributing

Contributions welcome! Please ensure:

- Code follows existing style
- Tests pass (if applicable)
- Documentation is updated

## Related Projects

- [ComfyStream](https://github.com/livepeer/comfystream): Parent project
- [AI Runner](https://github.com/livepeer/ai-runner): Pipeline framework
- [Scope Runner](https://github.com/daydreamlive/scope-runner): Similar integration for Scope
- [ComfyUI](https://github.com/comfyanonymous/ComfyUI): Underlying workflow engine
