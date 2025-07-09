# ProcessPoolExecutor Trickle Integration

This document describes the integration of ProcessPoolExecutor functionality with the trickle pipeline, enabling multiple ComfyUI workflows to run simultaneously with dedicated worker processes per stream.

## Overview

The ProcessPoolExecutor integration provides:
- **Dedicated worker per stream**: Each trickle stream gets its own isolated ComfyUI worker process
- **True parallelization**: Multiple streams can run simultaneously without CUDA conflicts
- **Process isolation**: Stream failures don't affect other streams
- **Synchronous frame correspondence**: Fixed frame timing to eliminate jitter

## Key Changes from Regular Pipeline

### Architecture
- **One worker per stream** instead of multiple workers per stream
- **Dedicated worker processes** for true isolation
- **Synchronous frame correspondence** to maintain proper timing
- **Background frame processing** for ProcessPoolPipeline streams

### Frame Processing
The ProcessPoolPipeline now maintains synchronous frame correspondence like the regular Pipeline:
- Frames are processed in order with proper input-output matching
- Eliminates frame jitter caused by temporal misalignment
- Background task handles async processing while maintaining sync interface

## API Usage

### Basic Usage

```python
from comfystream.processpool_pipeline import ProcessPoolPipeline

# Create pipeline with dedicated worker
pipeline = ProcessPoolPipeline(
    width=512,
    height=512,
    stream_id="my_stream",
    dedicated_worker=True  # Always True for ProcessPoolPipeline
)

# Use with trickle integration
from comfystream.server.trickle_integration import TrickleStreamManager

manager = TrickleStreamManager()
await manager.create_stream(
    request_id="stream_1",
    subscribe_url="ws://localhost:8080/subscribe",
    publish_url="ws://localhost:8080/publish", 
    pipeline=pipeline
)
```

### API Endpoints

#### Stream Management
- `POST /stream` - Create new stream with dedicated worker
- `GET /stream/{id}` - Get stream status
- `DELETE /stream/{id}` - Stop stream and terminate worker
- `POST /stream/{id}/update-prompts` - Update prompts for running stream

#### Worker Management
- `GET /stream/{id}/worker` - Get dedicated worker status
- `POST /stream/{id}/restart-worker` - Restart dedicated worker
- `GET /stream/{id}/workers` - Backward compatibility alias
- `POST /stream/{id}/restart-workers` - Backward compatibility alias

### Request Parameters

When creating a stream, use:
```json
{
    "subscribe_url": "ws://localhost:8080/subscribe",
    "publish_url": "ws://localhost:8080/publish",
    "dedicated_worker": true,
    "width": 512,
    "height": 512,
    "prompts": [...]
}
```

**Note**: The `dedicated_worker` parameter is always `true` for ProcessPoolPipeline (default behavior).

## Starting Streams with Prompts

You can now specify prompts directly when starting a stream, eliminating the need for a separate update call. This supports multiple formats and architectures:

### Single Prompt (JSON String Format)

```bash
# Start stream with single prompt using ProcessPool architecture
curl -X POST http://localhost:8000/stream/start \
  -H "Content-Type: application/json" \
  -d '{
    "subscribe_url": "http://localhost:3389/my_stream",
    "publish_url": "http://localhost:3389/my_stream-output",
    "gateway_request_id": "my_stream",
    "params": {
      "width": 512,
      "height": 512,
      "prompt": "{\"1\":{\"inputs\":{\"images\":[\"2\",0]},\"class_type\":\"SaveTensor\"},\"2\":{\"inputs\":{},\"class_type\":\"LoadTensor\"},\"3\":{\"inputs\":{\"images\":[\"2\",0]},\"class_type\":\"ImageInvert\"}}",
      "dedicated_worker": true,
      "use_prompt_worker": false
    }
  }'
```

### Multiple Prompts (Array Format)

```bash
# Start stream with multiple prompts using Prompt-Worker architecture
curl -X POST http://localhost:8000/stream/start \
  -H "Content-Type: application/json" \
  -d '{
    "subscribe_url": "http://localhost:3389/my_stream",
    "publish_url": "http://localhost:3389/my_stream-output",
    "gateway_request_id": "my_stream",
    "params": {
      "width": 512,
      "height": 512,
      "prompts": [
        {
          "1": {"inputs": {"images": ["2", 0]}, "class_type": "SaveTensor"},
          "2": {"inputs": {}, "class_type": "LoadTensor"},
          "3": {"inputs": {"images": ["2", 0]}, "class_type": "ImageInvert"}
        },
        {
          "1": {"inputs": {"images": ["2", 0]}, "class_type": "SaveTensor"},
          "2": {"inputs": {}, "class_type": "LoadTensor"},
          "3": {"inputs": {"images": ["2", 0], "blur_radius": 5}, "class_type": "ImageBlur"}
        }
      ],
      "dedicated_worker": false,
      "use_prompt_worker": true
    }
  }'
```

### Default Prompt (No Prompt Specified)

```bash
# Start stream with default prompt (simple inversion workflow)
curl -X POST http://localhost:8000/stream/start \
  -H "Content-Type: application/json" \
  -d '{
    "subscribe_url": "http://localhost:3389/my_stream",
    "publish_url": "http://localhost:3389/my_stream-output",
    "gateway_request_id": "my_stream",
    "params": {
      "width": 512,
      "height": 512,
      "dedicated_worker": true,
      "use_prompt_worker": false
    }
  }'
```

### Architecture Options

| Parameter | Description | Default |
|-----------|-------------|---------|
| `dedicated_worker` | Use ProcessPoolPipeline (dedicated worker per stream) | `true` |
| `use_prompt_worker` | Use PromptWorkerPipeline (prompt-per-worker architecture) | `false` |

#### Architecture Combinations

1. **Shared Pipeline** (`dedicated_worker: false, use_prompt_worker: false`)
   - Single shared pipeline for all streams
   - Lowest resource usage
   - Potential interference between streams

2. **ProcessPool Pipeline** (`dedicated_worker: true, use_prompt_worker: false`)
   - Dedicated worker process per stream
   - Good isolation between streams
   - Better for concurrent streams

3. **Prompt-Worker Pipeline** (`dedicated_worker: false, use_prompt_worker: true`)
   - Dedicated worker per prompt within a stream
   - Best for frequent prompt switching
   - Complete prompt isolation

### Response Format

```json
{
  "status": "success",
  "message": "Stream my_stream started successfully",
  "request_id": "my_stream",
  "config": {
    "subscribe_url": "http://localhost:3389/my_stream",
    "publish_url": "http://localhost:3389/my_stream-output",
    "width": 512,
    "height": 512,
    "dedicated_worker": true,
    "use_prompt_worker": false,
    "pipeline_type": "ProcessPoolPipeline",
    "architecture": "dedicated_worker_per_stream",
    "prompts_set": true,
    "prompt_count": 1
  }
}
```

### Error Handling

If prompt validation fails, you'll receive detailed error information:

```json
{
  "error": "Prompt validation failed",
  "message": "The provided prompt references models or values that are not available in this ComfyUI workspace",
  "validation_errors": [
    {
      "node_id": "3",
      "node_type": "ImageBlur",
      "error_type": "invalid_input",
      "message": "Required input is missing",
      "input_name": "blur_radius",
      "received_value": null,
      "valid_options": []
    }
  ]
}
```

## Workspace Configuration

The prompt-per-worker architecture ensures that each worker starts in the correct ComfyUI workspace directory. The workspace path is passed via the `cwd` parameter:

### Verifying Workspace Configuration

You can verify that workers are using the correct workspace path:

```bash
# Check workspace configuration for a stream
curl -X GET http://localhost:8000/stream/my_stream/workspace-info
```

Response:
```json
{
  "success": true,
  "stream_id": "my_stream",
  "workspace_info": {
    "workspace_path": "/workspace/ComfyUI",
    "config_keys": ["cwd", "disable_cuda_malloc", "gpu_only", "preview_method"],
    "total_workers": 2,
    "active_prompt_id": "abc123def456"
  }
}
```

### Common Workspace Issues

1. **Incorrect workspace path**: Ensure the `cwd` parameter points to the ComfyUI root directory
2. **Missing ComfyUI models**: Verify that models are accessible from the workspace path
3. **Permission issues**: Check that the workspace directory is readable/writable

### Testing Workspace Configuration

Use the included test script to verify workspace configuration:

```bash
cd /workspace/comfystream
python test_workspace_config.py
```

This will verify that:
- The workspace path is correctly passed to workers
- Workers can access the ComfyUI installation
- Configuration parameters are properly propagated

### Workspace Path Sources

The workspace path comes from different sources depending on how ComfyStream is started:

1. **Direct server start**: `--workspace` parameter
2. **Trickle API**: App context workspace setting
3. **ProcessPool**: Passed through pipeline configuration
4. **PromptWorker**: Inherited from client configuration

## Updating Prompts

Update prompts for a running stream without restarting:

```bash
# Update with single prompt (JSON string)
curl -X POST http://localhost:8000/stream/my_stream/update-prompts \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "{\"1\":{\"inputs\":{\"images\":[\"2\",0]},\"class_type\":\"SaveTensor\"},\"2\":{\"inputs\":{},\"class_type\":\"LoadTensor\"}}"
  }'

# Update with multiple prompts (array)
curl -X POST http://localhost:8000/stream/my_stream/update-prompts \
  -H "Content-Type: application/json" \
  -d '{
    "prompts": [
      {"1": {"inputs": {"images": ["2", 0]}, "class_type": "SaveTensor"}},
      {"2": {"inputs": {}, "class_type": "LoadTensor"}}
    ]
  }'

# Update prompts with parameter changes and forced warmup
curl -X POST http://localhost:8000/stream/my_stream/update-prompts \
  -H "Content-Type: application/json" \
  -d '{
    "prompts": [
      {"1": {"inputs": {"images": ["2", 0]}, "class_type": "SaveTensor"}},
      {"2": {"inputs": {}, "class_type": "LoadTensor"}}
    ],
    "parameters": {
      "width": 1024,
      "height": 1024,
      "warmup": true
    }
  }'
```

```python
# Python example
import aiohttp
import json

async def update_prompts(session, stream_id, new_prompt):
    payload = {"prompt": json.dumps(new_prompt)}
    async with session.post(f'http://localhost:8000/stream/{stream_id}/update-prompts', json=payload) as resp:
        result = await resp.json()
        return resp.status == 200

# Update with parameters
async def update_prompts_with_params(session, stream_id, new_prompts, parameters=None):
    payload = {"prompts": new_prompts}
    if parameters:
        payload["parameters"] = parameters
    async with session.post(f'http://localhost:8000/stream/{stream_id}/update-prompts', json=payload) as resp:
        result = await resp.json()
        return resp.status == 200
```

### Enhanced Prompt Update Mechanism

The improved prompt update system addresses the core issue where old prompts would continue running in background processes:

#### Regular Pipeline (`Pipeline` class)
- **Enhanced cleanup**: Aggressively cancels running prompts and clears all queues
- **ComfyUI client restart**: Completely restarts the embedded ComfyUI client to stop old prompt execution
- **Fresh state**: Ensures no old prompts continue running after update

#### ProcessPoolPipeline (`ProcessPoolPipeline` class)
- **Worker restart**: Completely restarts the dedicated worker process
- **Parameter tracking**: Monitors parameter changes that require warmup
- **Automatic warmup**: Performs warmup after prompt updates to ensure readiness
- **Enhanced error handling**: Provides detailed logging and timeout handling

#### Parameter Changes Requiring Warmup
The following parameter changes require pipeline warmup:
- `width` - Video frame width
- `height` - Video frame height  
- `cwd` - ComfyUI workspace directory
- `disable_cuda_malloc` - CUDA memory allocation settings
- `gpu_only` - GPU-only processing mode

#### API Response Format
```json
{
  "status": "success",
  "message": "Prompts updated for stream my_stream",
  "request_id": "my_stream",
  "prompts_count": 2,
  "parameters_updated": true,
  "warmup_performed": true
}
```

## Implementation Details

### ProcessPoolPipeline Class

Key features:
- **Single worker per stream**: `max_workers=1` always
- **Synchronous frame correspondence**: Maintains proper input-output timing
- **Process isolation**: Each stream gets its own ComfyUI instance
- **Background processing**: Async frame processing with sync interface

### Frame Processing Flow

1. **Input Frame**: Received via trickle sync interface
2. **Preprocessing**: Convert to ComfyUI format
3. **Queue Management**: Add to both client and incoming frames queue
4. **Background Processing**: Async task processes frames maintaining correspondence
5. **Output**: Return processed frame with preserved timing

### Jitter Fix

The frame jitter issue was caused by broken frame correspondence in the original implementation:
- **Problem**: Async buffer collected outputs independently, breaking input-output relationship
- **Solution**: Synchronous frame correspondence like regular Pipeline
- **Result**: Eliminated temporal misalignment and frame jitter

## Performance Characteristics

### Advantages
- **No CUDA conflicts**: Process isolation prevents CUDA graph errors
- **True parallelization**: Multiple streams run simultaneously
- **Fault tolerance**: Individual stream failures don't affect others
- **Smooth playback**: Fixed frame correspondence eliminates jitter

### Considerations
- **Memory usage**: Each worker process loads its own model instance
- **Startup time**: Process creation overhead per stream
- **Resource management**: Proper cleanup required for process termination

## Example Usage

See `example_processpool_trickle.py` for a complete working example:

```python
import asyncio
from comfystream.processpool_pipeline import ProcessPoolPipeline
from comfystream.server.trickle_api import create_trickle_app
from comfystream.prompts import DEFAULT_PROMPT

async def main():
    # Create pipeline with dedicated worker
    pipeline = ProcessPoolPipeline(
        width=512,
        height=512,
        stream_id="example_stream"
    )
    
    # Initialize with prompts
    await pipeline.initialize([DEFAULT_PROMPT])
    
    # Create trickle app
    app = create_trickle_app(pipeline)
    
    # Run server
    from aiohttp import web
    web.run_app(app, host="0.0.0.0", port=8000)

if __name__ == "__main__":
    asyncio.run(main())
```

## Troubleshooting

### Common Issues

1. **Worker not starting**: Check CUDA availability and memory
2. **Frame jitter**: Ensure using latest version with synchronous frame correspondence
3. **Memory leaks**: Verify proper stream cleanup on stop
4. **Process hanging**: Check worker termination timeouts

### Debugging

Enable debug logging:
```python
import logging
logging.getLogger("comfystream").setLevel(logging.DEBUG)
```

Check worker status:
```bash
curl http://localhost:8000/stream/{id}/worker
```

## Migration from Regular Pipeline

To migrate from regular Pipeline to ProcessPoolPipeline:

1. Replace `Pipeline` with `ProcessPoolPipeline`
2. Add `stream_id` parameter
3. Update API calls to use `/worker` endpoints
4. Ensure proper cleanup with `await pipeline.cleanup()`

The API remains largely compatible, with dedicated worker behavior as the main difference. 