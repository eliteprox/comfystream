#!/bin/bash

MODEL_NAME="dreamshaper_8.safetensors"
WORKFLOW_FILE="workflows/build_tensorrt_api_workflows/build_tensorrt_api.json"
MODEL_BASE_NAME="${MODEL_NAME%.*}"

echo "Testing TensorRT build for model: $MODEL_NAME"
echo "Using workflow: $WORKFLOW_FILE"

# Read and modify the workflow JSON
MODIFIED_JSON=$(cat "$WORKFLOW_FILE" | sed "s/{model_name}/$MODEL_BASE_NAME/g")

# Debug: Print the final JSON structure
echo "Modified workflow:"
echo "$MODIFIED_JSON" | jq '.'

# Execute the workflow
echo "Executing workflow..."
curl -v -X POST "http://127.0.0.1:8888/run_prompt" \
    -H 'Content-Type: application/json' \
    -d "$MODIFIED_JSON"

# Print expected output locations
echo -e "\nExpected output locations:"
echo "ONNX input should be at: ../ComfyUI/models/onnx/${MODEL_BASE_NAME}_fp8.onnx"
echo "TensorRT output should be at: ../ComfyUI/models/tensorrt/static-${MODEL_BASE_NAME}" 