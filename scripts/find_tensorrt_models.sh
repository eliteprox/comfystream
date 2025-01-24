#!/bin/bash

MODEL_NAME="$stat-b-1-h-512-w-512"
CURRENT_DIR=$(pwd)

echo "Starting search from current directory: $CURRENT_DIR"
echo "Searching upwards for TensorRT model files..."

# Search up the directory tree
dir="$CURRENT_DIR"
while [ "$dir" != "/" ]; do
    echo -e "\nSearching in: $dir"
    
    # Search for engine files matching the pattern
    echo "Looking for engine files:"
    find "$dir" -type f -name "static-${MODEL_NAME}*engine" 2>/dev/null
    
    # Search for any engine files (in case naming pattern is different)
    echo -e "\nLooking for all .engine files:"
    find "$dir" -type f -name "*.engine" 2>/dev/null
    
    # Move up one directory
    dir=$(dirname "$dir")
done 