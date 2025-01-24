#!/bin/bash

WORKFLOW_FILE="workflows/build_tensorrt_api_workflows/build_onnx_api.json"
MODEL_NAME="dreamshaper_8_dmd_1kstep.safetensors"
OUTPUT_DIR="../ComfyUI/models/onnx"  # Relative to where script is run

# Read and modify the workflow JSON
MODIFIED_JSON=$(cat "$WORKFLOW_FILE" | sed "s|/workspace/ComfyUI/models/onnx|$OUTPUT_DIR|g" | sed "s/[^\"]*\.safetensors/$MODEL_NAME/g")

# Debug: Print the JSON structure
echo "Sending workflow structure:"
echo "$MODIFIED_JSON" | jq '.'

# Execute the curl command
echo "Sending request to API..."
curl -v -X POST "http://127.0.0.1:8888/run_prompt" \
    -H 'Content-Type: application/json' \
    -d "$MODIFIED_JSON" 