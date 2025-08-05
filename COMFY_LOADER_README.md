# ComfyUI Hiddenswitch v0.3.40 Integration

This implementation provides a clean separation between vanilla custom nodes and comfystream server components using importlib namespace override.

## Features

- **Package Data Integration**: ComfyUI hiddenswitch v0.3.40 is included as package data
- **Importlib Namespace Override**: Uses importlib to override the comfy namespace
- **Vanilla Custom Node Support**: server_manager.py works as a vanilla custom node
- **Component Isolation**: Prevents accidental loading of comfystream server components

## Setup

1. **Download ComfyUI Package Data**:
   ```bash
   python setup_comfyui_data.py
   ```

2. **Install comfystream**:
   ```bash
   pip install -e .
   ```

## Usage

### As Vanilla Custom Node

The `nodes/server_manager.py` can be used as a vanilla custom node in ComfyUI:

```python
# This will work as a vanilla custom node
from nodes.server_manager import ComfyStreamServerManager
```

### As ComfyStream Server Component

For server applications, the full comfystream components are available:

```python
from comfystream import ComfyStreamClient, Pipeline
```

## Implementation Details

### comfy_loader.py

- **Namespace Setup**: Downloads and extracts ComfyUI hiddenswitch v0.3.40
- **Importlib Override**: Uses importlib to override comfy namespace
- **Context Detection**: Detects vanilla custom node context
- **Component Isolation**: Prevents server component loading in vanilla context

### Package Data

ComfyUI hiddenswitch v0.3.40 is included as package data in:
- `src/comfystream/comfyui_hiddenswitch_v0.3.40/`

### Environment Variables

- `COMFYSTREAM_DISABLE_SERVER=1`: Disable server component loading

## Testing

Run the test script to verify the implementation:

```bash
python test_comfy_loader.py
```

## Architecture

```
comfystream/
├── src/comfystream/
│   ├── comfy_loader.py          # Namespace override logic
│   ├── client.py                # Server component
│   ├── pipeline.py              # Server component
│   └── server/                  # Server components
├── nodes/
│   ├── server_manager.py        # Vanilla custom node
│   └── web/                     # UI components
└── comfyui_hiddenswitch_v0.3.40/ # Package data
```

## Key Benefits

1. **Clean Separation**: Vanilla custom nodes don't load server components
2. **Package Data**: ComfyUI is bundled with comfystream
3. **Importlib Override**: Proper namespace isolation
4. **Backward Compatibility**: Existing code continues to work
5. **Development Friendly**: Works in both installed and development environments 