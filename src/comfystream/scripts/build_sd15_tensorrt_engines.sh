#!/bin/bash

# Define arrays of workflows and models (using space-separated strings)
WORKFLOWS="workflows/build_tensorrt_api_workflows/build_onnx_api.json \
    workflows/build_tensorrt_api_workflows/build_tensorrt_api.json"

MODELS="dreamshaper_8.safetensors"

# Function to process a workflow with a specific model
process_workflow() {
    local WORKFLOW_FILE=$1
    local MODEL_NAME=$2
    local ONNX_DIR="/workspace/ComfyUI/models/onnx"
    local MODEL_BASE_NAME="${MODEL_NAME%.*}"
    
    echo "Processing workflow: $WORKFLOW_FILE with model: $MODEL_NAME"
    
    # Read and modify the workflow JSON based on type
    case "$WORKFLOW_FILE" in
        *build_onnx_api.json)
            MODIFIED_JSON=$(cat "$WORKFLOW_FILE" | \
                sed "s|/workspace/ComfyUI/models/onnx|$ONNX_DIR|g" | \
                sed "s/{model_name}/$MODEL_BASE_NAME/g")
            ;;
        *build_tensorrt_api.json)
            MODIFIED_JSON=$(cat "$WORKFLOW_FILE" | \
                sed "s/{model_name}/$MODEL_BASE_NAME/g")
            ;;
        *)
            echo "Unknown workflow type: $WORKFLOW_FILE"
            return 1
            ;;
    esac
    
    # Show the modification (optional, for debugging)
    echo "Modified workflow:"
    echo "$MODIFIED_JSON" | jq '.'
    
    # Execute the workflow
    echo "Executing workflow..."
    RESPONSE=$(curl -v -X POST "http://127.0.0.1:8889/run_prompt" \
        -H 'Content-Type: application/json' \
        -d "$MODIFIED_JSON")
    
    # Check response
    if [ $? -eq 0 ]; then
        echo "Successfully processed with $MODEL_NAME"
        echo "Response: $RESPONSE"
    else
        echo "Error executing workflow"
        return 1
    fi
    
    sleep 2
}

# Main loop
for workflow in $WORKFLOWS; do
    for model in $MODELS; do
        process_workflow "$workflow" "$model"
    done
done
