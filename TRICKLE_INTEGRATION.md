# ComfyStream Trickle Integration

This document describes the basic integration between ComfyStream and the trickle-app package for video streaming.

## Overview

The trickle integration adds REST API endpoints to ComfyStream for real-time video stream processing using the trickle protocol.

## Installation

Install the trickle-app package:

```bash
pip install git+https://github.com/eliteprox/py-trickle.git
```

## API Endpoints

### Start Stream
```
POST /stream/start
```

Start a new trickle stream with the following request body:

```json
{
  "subscribe_url": "http://source:port/input",
  "publish_url": "http://destination:port/output",
  "gateway_request_id": "unique-stream-id",
  "params": {
    "width": 512,
    "height": 512,
    "prompt": "{\"1\":{\"inputs\":{\"images\":[\"2\",0]},\"class_type\":\"SaveTensor\"},\"2\":{\"inputs\":{},\"class_type\":\"LoadTensor\"}}"
  }
}
```

### Stop Stream
```
POST /stream/{request_id}/stop
```

### Get Stream Status
```
GET /stream/{request_id}/status
```

### List All Streams
```
GET /streams
```

## Basic Workflow

The workflow must include:
1. **LoadTensor** node: Receives input frames
2. **SaveTensor** node: Outputs processed frames
3. Any processing nodes in between

## Example

See `examples/basic_trickle_integration.py` for a complete example.

## Notes

- If trickle-app is not available, the integration falls back to a mock implementation
- The integration handles different tensor formats automatically
- Streams are cleaned up automatically when stopped