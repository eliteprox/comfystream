#!/bin/bash

# ComfyStream MCP Server Startup Script
# This script starts the MCP server for ComfyStream

# Set default values
COMFYSTREAM_HOST="${COMFYSTREAM_HOST:-localhost}"
COMFYSTREAM_PORT="${COMFYSTREAM_PORT:-8889}"
TRANSPORT="${TRANSPORT:-stdio}"

# Activate virtual environment if it exists
if [ -f "../venv/bin/activate" ]; then
    source ../venv/bin/activate
elif [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
fi

# Start the MCP server
echo "Starting ComfyStream MCP Server..."
echo "ComfyStream Host: $COMFYSTREAM_HOST"
echo "ComfyStream Port: $COMFYSTREAM_PORT"
echo "Transport: $TRANSPORT"
echo ""

python mcp_server.py --host "$COMFYSTREAM_HOST" --port "$COMFYSTREAM_PORT" --transport "$TRANSPORT"