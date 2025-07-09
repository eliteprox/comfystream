"""
Trickle API routes for ComfyStream.

This module implements REST API endpoints for streaming with trickle protocol integration.
Handles ingress/egress to ComfyStream pipeline using the trickle-app package.
"""

import asyncio
import json
import logging
from typing import Dict, Optional
from aiohttp import web

# Import the trickle integration - trickle-app should always be installed
from trickle_integration import TrickleStreamManager
from comfystream.processpool_pipeline import ProcessPoolPipeline

logger = logging.getLogger(__name__)
logger.info("Using trickle integration")

# Global stream manager instance - will be initialized in setup_trickle_routes
stream_manager: Optional[TrickleStreamManager] = None

async def start_stream(request):
    """Start a new trickle stream.
    
    Expected request format:
    {
        "subscribe_url": "http://192.168.10.61:3389/sample",
        "publish_url": "http://192.168.10.61:3389/sample-output", 
        "gateway_request_id": "sample2",
        "params": {
            "width": 512,
            "height": 512,
            "prompt": "{\"1\":{\"inputs\":{\"images\":[\"2\",0]},\"class_type\":\"SaveTensor\"},...}",
            "prompts": [
                {"1": {"inputs": {"images": ["2", 0]}, "class_type": "SaveTensor"}},
                {"2": {"inputs": {}, "class_type": "LoadTensor"}}
            ],
            "use_prompt_worker": true
        }
    }
    """
    try:
        if not stream_manager:
            return web.json_response({'error': 'Stream manager not initialized'}, status=500)
            
        data = await request.json()
        
        # Validate required fields
        required_fields = ['subscribe_url', 'publish_url', 'gateway_request_id']
        for field in required_fields:
            if field not in data:
                return web.json_response(
                    {'error': f'Missing required field: {field}'}, 
                    status=400
                )
        
        # Extract configuration
        request_id = data['gateway_request_id']
        params = data.get('params', {})
        
        width = params.get('width', 512)
        height = params.get('height', 512)
        use_dedicated_worker = params.get('dedicated_worker', True)  # Default to dedicated worker per stream
        use_prompt_worker = params.get('use_prompt_worker', False)  # New prompt-per-worker architecture
        
        # Create pipeline based on architecture choice
        workspace = request.app.get('workspace')
        
        if use_prompt_worker:
            # Use the new prompt-per-worker architecture
            logger.info(f"Creating PromptWorkerPipeline for stream {request_id}")
            from comfystream.prompt_worker_pipeline import PromptWorkerPipeline
            pipeline = PromptWorkerPipeline(
                width=width,
                height=height,
                cwd=workspace,
                disable_cuda_malloc=True,
                gpu_only=True,
                preview_method='none'
            )
        elif use_dedicated_worker:
            # Use ProcessPoolPipeline for dedicated worker per stream
            logger.info(f"Creating dedicated ProcessPoolPipeline worker for stream {request_id}")
            pipeline = ProcessPoolPipeline(
                width=width,
                height=height,
                stream_id=request_id,
                cwd=workspace,
                disable_cuda_malloc=True,
                gpu_only=True
            )
        else:
            # Use the shared pipeline from app.py only if explicitly requested
            pipeline = request.app.get('pipeline')
            if not pipeline:
                return web.json_response({
                    'error': 'Pipeline not initialized in app'
                }, status=500)
        
        # Update pipeline resolution if different from current
        if pipeline.width != width or pipeline.height != height:
            pipeline.width = width
            pipeline.height = height
            logger.info(f"Updated pipeline resolution to {width}x{height} for stream {request_id}")
        
        # Parse prompts from request - support both formats
        prompts = None
        if 'prompt' in params:
            try:
                prompt_data = json.loads(params['prompt'])
                prompts = [prompt_data]
                logger.info(f"Using single prompt from 'prompt' field for stream {request_id}")
            except json.JSONDecodeError as e:
                logger.error(f"Invalid prompt JSON for stream {request_id}: {e}")
                return web.json_response(
                    {'error': f'Invalid prompt JSON: {str(e)}'}, 
                    status=400
                )
        elif 'prompts' in params:
            prompts = params['prompts']
            if not isinstance(prompts, list):
                return web.json_response(
                    {'error': 'prompts must be a list of prompt objects'}, 
                    status=400
                )
            logger.info(f"Using {len(prompts)} prompts from 'prompts' field for stream {request_id}")
        
        # Set prompts if provided, otherwise use default
        if prompts:
            try:
                logger.info(f"Setting {len(prompts)} custom prompts for stream {request_id}")
                
                # Use the appropriate method based on pipeline type
                if use_prompt_worker:
                    await pipeline.set_prompts(prompts)
                else:
                    await pipeline.update_prompts(prompts)
                    
                logger.info(f"Successfully set prompts for stream {request_id}")
                
            except ValueError as e:
                # Handle ComfyUI validation errors
                error_msg = str(e)
                logger.error(f"Prompt validation failed for stream {request_id}: {error_msg}")
                
                # Try to parse the JSON error for better user feedback
                try:
                    error_data = json.loads(error_msg)
                    validation_errors = []
                    for node_id, node_error in error_data.items():
                        if 'errors' in node_error:
                            for error in node_error['errors']:
                                validation_errors.append({
                                    'node_id': node_id,
                                    'node_type': node_error.get('class_type', 'unknown'),
                                    'error_type': error.get('type', 'unknown'),
                                    'message': error.get('message', ''),
                                    'details': error.get('details', ''),
                                    'input_name': error.get('extra_info', {}).get('input_name', ''),
                                    'received_value': error.get('extra_info', {}).get('received_value', ''),
                                    'valid_options': error.get('extra_info', {}).get('input_config', [[]])[0] if error.get('extra_info', {}).get('input_config') else []
                                })
                    
                    return web.json_response({
                        'error': 'Prompt validation failed',
                        'message': 'The provided prompt references models or values that are not available in this ComfyUI workspace',
                        'validation_errors': validation_errors
                    }, status=400)
                    
                except (json.JSONDecodeError, KeyError):
                    # Fallback for non-JSON validation errors
                    return web.json_response({
                        'error': 'Prompt validation failed',
                        'message': error_msg
                    }, status=400)
                    
            except Exception as e:
                logger.error(f"Error setting prompts for stream {request_id}: {e}")
                return web.json_response(
                    {'error': f'Failed to set prompts: {str(e)}'}, 
                    status=500
                )
        else:
            # Set a default simple inversion workflow for testing
            default_workflow = {
                "1": {
                    "inputs": {
                        "images": ["2", 0]
                    },
                    "class_type": "SaveTensor"
                },
                "2": {
                    "inputs": {},
                    "class_type": "LoadTensor"
                },
                "3": {
                    "inputs": {
                        "images": ["2", 0]
                    },
                    "class_type": "ImageInvert"
                }
            }
            
            try:
                logger.info(f"Setting default inversion workflow for stream {request_id}")
                
                # Use the appropriate method based on pipeline type
                if use_prompt_worker:
                    await pipeline.set_prompts([default_workflow])
                else:
                    await pipeline.update_prompts([default_workflow])
                    
                logger.info(f"Set default inversion workflow for stream {request_id}")
                    
            except Exception as e:
                logger.error(f"Error setting default prompts for stream {request_id}: {e}")
                return web.json_response(
                    {'error': f'Failed to set default prompts: {str(e)}'}, 
                    status=500
                )
        
        # Start the stream using the pipeline
        success = await stream_manager.create_stream(
            request_id=request_id,
            subscribe_url=data['subscribe_url'],
            publish_url=data['publish_url'],
            pipeline=pipeline,
            width=width,
            height=height
        )
        
        if success:
            # Determine architecture type for response
            if use_prompt_worker:
                architecture = 'prompt_per_worker'
                pipeline_type = 'PromptWorkerPipeline'
            elif use_dedicated_worker:
                architecture = 'dedicated_worker_per_stream'
                pipeline_type = 'ProcessPoolPipeline'
            else:
                architecture = 'shared_single_process'
                pipeline_type = 'Pipeline'
            
            return web.json_response({
                'status': 'success',
                'message': f'Stream {request_id} started successfully',
                'request_id': request_id,
                'config': {
                    'subscribe_url': data['subscribe_url'],
                    'publish_url': data['publish_url'],
                    'width': width,
                    'height': height,
                    'dedicated_worker': use_dedicated_worker,
                    'use_prompt_worker': use_prompt_worker,
                    'pipeline_type': pipeline_type,
                    'architecture': architecture,
                    'prompts_set': prompts is not None,
                    'prompt_count': len(prompts) if prompts else 1
                }
            })
        else:
            return web.json_response({
                'status': 'error',
                'message': f'Failed to start stream {request_id}'
            }, status=500)
            
    except json.JSONDecodeError:
        return web.json_response({'error': 'Invalid JSON'}, status=400)
    except Exception as e:
        logger.error(f"Error starting stream: {e}")
        return web.json_response({
            'status': 'error',
            'message': f'Internal server error: {str(e)}'
        }, status=500)

async def stop_stream(request):
    """Stop a trickle stream."""
    try:
        if not stream_manager:
            return web.json_response({'error': 'Stream manager not initialized'}, status=500)
            
        request_id = request.match_info.get('request_id')
        if not request_id:
            return web.json_response({'error': 'Missing request_id'}, status=400)
        
        success = await stream_manager.stop_stream(request_id)
        
        if success:
            return web.json_response({
                'status': 'success',
                'message': f'Stream {request_id} stopped successfully'
            })
        else:
            return web.json_response({
                'status': 'error',
                'message': f'Stream {request_id} not found or failed to stop'
            }, status=404)
            
    except Exception as e:
        logger.error(f"Error stopping stream: {e}")
        return web.json_response({
            'status': 'error',
            'message': f'Internal server error: {str(e)}'
        }, status=500)

async def get_stream_status(request):
    """Get status of a trickle stream."""
    try:
        if not stream_manager:
            return web.json_response({'error': 'Stream manager not initialized'}, status=500)
            
        request_id = request.match_info.get('request_id')
        if not request_id:
            return web.json_response({'error': 'Missing request_id'}, status=400)
        
        status = await stream_manager.get_stream_status(request_id)
        
        if status:
            # Add worker information if using ProcessPoolPipeline
            handler = stream_manager.handlers.get(request_id)
            if handler and isinstance(handler.pipeline, ProcessPoolPipeline):
                worker_status = handler.pipeline.get_worker_status()
                status['worker_status'] = worker_status
                status['pipeline_type'] = 'ProcessPoolPipeline'
                status['architecture'] = 'dedicated_worker_per_stream'
            else:
                status['pipeline_type'] = 'Pipeline'
                status['architecture'] = 'shared_single_process'
            
            return web.json_response(status)
        else:
            return web.json_response({
                'error': f'Stream {request_id} not found'
            }, status=404)
            
    except Exception as e:
        logger.error(f"Error getting stream status: {e}")
        return web.json_response({
            'status': 'error',
            'message': f'Internal server error: {str(e)}'
        }, status=500)

async def list_streams(request):
    """List all active trickle streams."""
    try:
        if not stream_manager:
            return web.json_response({'error': 'Stream manager not initialized'}, status=500)
            
        streams = await stream_manager.list_streams()
        return web.json_response({
            'streams': streams,
            'count': len(streams)
        })
        
    except Exception as e:
        logger.error(f"Error listing streams: {e}")
        return web.json_response({
            'status': 'error',
            'message': f'Internal server error: {str(e)}'
        }, status=500)

async def stop_current_stream(request):
    """Stop the current stream (webrtc-worker compatible endpoint)."""
    try:
        if not stream_manager:
            return web.json_response({'error': 'Stream manager not initialized'}, status=500)
        
        # Get all streams and stop them (simple approach for single stream scenarios)
        streams = await stream_manager.list_streams()
        
        if not streams:
            return web.json_response({
                'status': 'error',
                'message': 'No active streams found'
            }, status=404)
        
        # Stop all streams (typically there should only be one in process capability mode)
        stopped_count = 0
        for stream_id in streams.keys():
            success = await stream_manager.stop_stream(stream_id)
            if success:
                stopped_count += 1
        
        if stopped_count > 0:
            return web.json_response({
                'status': 'stopped',
                'message': f'Stopped {stopped_count} stream(s)'
            })
        else:
            return web.json_response({
                'status': 'error',
                'message': 'Failed to stop streams'
            }, status=500)
            
    except Exception as e:
        logger.error(f"Error stopping current stream: {e}")
        return web.json_response({
            'status': 'error',
            'message': f'Internal server error: {str(e)}'
        }, status=500)

async def get_current_stream_status(request):
    """Get current stream status (webrtc-worker compatible endpoint)."""
    try:
        if not stream_manager:
            return web.json_response({'error': 'Stream manager not initialized'}, status=500)
        
        streams = await stream_manager.list_streams()
        
        if not streams:
            return web.json_response({
                'processing_active': False,
                'stream_count': 0,
                'message': 'No active streams'
            })
        
        # Return status compatible with webrtc-worker format
        stream_id = next(iter(streams.keys()))  # Get first stream
        stream_status = streams[stream_id]
        
        return web.json_response({
            'processing_active': stream_status.get('running', False),
            'stream_count': len(streams),
            'current_stream': stream_status,
            'all_streams': streams
        })
        
    except Exception as e:
        logger.error(f"Error getting current stream status: {e}")
        return web.json_response({
            'status': 'error',
            'message': f'Internal server error: {str(e)}'
        }, status=500)

async def health_check(request):
    """Health check endpoint (webrtc-worker compatible)."""
    try:
        # Check if stream manager is initialized
        manager_healthy = stream_manager is not None
        
        return web.json_response({
            'status': 'healthy',
            'service': 'trickle-stream-processor',
            'version': '1.0.0',
            'stream_manager_ready': manager_healthy
        })
    except Exception as e:
        logger.error(f"Error in health check: {e}")
        return web.json_response({
            'status': 'unhealthy',
            'service': 'trickle-stream-processor',
            'error': str(e)
        }, status=500)

async def restart_worker(request):
    """Restart the dedicated worker for a specific stream (ProcessPoolPipeline only)."""
    try:
        if not stream_manager:
            return web.json_response({'error': 'Stream manager not initialized'}, status=500)
            
        request_id = request.match_info.get('request_id')
        if not request_id:
            return web.json_response({'error': 'Missing request_id'}, status=400)
        
        handler = stream_manager.handlers.get(request_id)
        if not handler:
            return web.json_response({
                'error': f'Stream {request_id} not found'
            }, status=404)
        
        if isinstance(handler.pipeline, ProcessPoolPipeline):
            await handler.pipeline.restart_worker()
            return web.json_response({
                'status': 'success',
                'message': f'Dedicated worker restarted for stream {request_id}'
            })
        else:
            return web.json_response({
                'error': f'Stream {request_id} is not using ProcessPoolPipeline (dedicated worker)'
            }, status=400)
            
    except Exception as e:
        logger.error(f"Error restarting worker: {e}")
        return web.json_response({
            'status': 'error',
            'message': f'Internal server error: {str(e)}'
        }, status=500)

# Backward compatibility alias
async def restart_workers(request):
    """Restart workers (backward compatibility - calls restart_worker)."""
    return await restart_worker(request)

async def update_prompts(request):
    """Update prompts for an existing trickle stream.
    
    Expected request format:
    {
        "prompt": "{\"1\":{\"inputs\":{\"images\":[\"2\",0]},\"class_type\":\"SaveTensor\"},...}"
    }
    or
    {
        "prompts": [
            {"1": {"inputs": {"images": ["2", 0]}, "class_type": "SaveTensor"}},
            {"2": {"inputs": {}, "class_type": "LoadTensor"}}
        ]
    }
    or
    {
        "prompts": [...],
        "parameters": {
            "width": 512,
            "height": 512,
            "warmup": true
        }
    }
    """
    try:
        if not stream_manager:
            return web.json_response({'error': 'Stream manager not initialized'}, status=500)
            
        request_id = request.match_info.get('request_id')
        if not request_id:
            return web.json_response({'error': 'Missing request_id'}, status=400)
        
        # Check if stream exists
        handler = stream_manager.handlers.get(request_id)
        if not handler:
            return web.json_response({
                'error': f'Stream {request_id} not found'
            }, status=404)
        
        data = await request.json()
        
        # Parse prompts from request
        prompts = None
        if 'prompt' in data:
            try:
                prompt_data = json.loads(data['prompt'])
                prompts = [prompt_data]
            except json.JSONDecodeError as e:
                return web.json_response(
                    {'error': f'Invalid prompt JSON: {str(e)}'}, 
                    status=400
                )
        elif 'prompts' in data:
            prompts = data['prompts']
            if not isinstance(prompts, list):
                return web.json_response(
                    {'error': 'prompts must be a list of prompt objects'}, 
                    status=400
                )
        else:
            return web.json_response(
                {'error': 'Either "prompt" (JSON string) or "prompts" (list) must be provided'}, 
                status=400
            )
        
        # Check for parameter updates
        parameters = data.get('parameters', {})
        force_warmup = parameters.get('warmup', False)
        
        # Update pipeline parameters if provided
        if parameters:
            logger.info(f"Updating parameters for stream {request_id}: {parameters}")
            
            # Handle resolution changes
            if 'width' in parameters or 'height' in parameters:
                new_width = parameters.get('width', handler.pipeline.width)
                new_height = parameters.get('height', handler.pipeline.height)
                
                if new_width != handler.pipeline.width or new_height != handler.pipeline.height:
                    logger.info(f"Resolution change for stream {request_id}: {handler.pipeline.width}x{handler.pipeline.height} -> {new_width}x{new_height}")
                    handler.pipeline.width = new_width
                    handler.pipeline.height = new_height
                    force_warmup = True
            
            # Handle other parameter changes for ProcessPoolPipeline
            if isinstance(handler.pipeline, ProcessPoolPipeline):
                if handler.pipeline.requires_warmup(parameters):
                    logger.info(f"Parameter changes require warmup for stream {request_id}")
                    force_warmup = True
                
                # Update parameters
                await handler.pipeline.update_parameters(parameters)
        
        # Update prompts on the pipeline
        try:
            logger.info(f"Updating prompts for stream {request_id}")
            await handler.pipeline.update_prompts(prompts)
            
            # Force warmup if requested or if parameters changed
            if force_warmup:
                logger.info(f"Performing warmup for stream {request_id}")
                try:
                    await asyncio.wait_for(handler.pipeline.warm_video(), timeout=60.0)
                    logger.info(f"Warmup completed for stream {request_id}")
                except asyncio.TimeoutError:
                    logger.warning(f"Warmup timeout for stream {request_id}")
                except Exception as e:
                    logger.warning(f"Warmup error for stream {request_id}: {e}")
            
            logger.info(f"Successfully updated prompts for stream {request_id}")
            
            return web.json_response({
                'status': 'success',
                'message': f'Prompts updated for stream {request_id}',
                'request_id': request_id,
                'prompts_count': len(prompts),
                'parameters_updated': bool(parameters),
                'warmup_performed': force_warmup
            })
            
        except Exception as e:
            logger.error(f"Error updating prompts for stream {request_id}: {e}")
            return web.json_response({
                'status': 'error',
                'message': f'Failed to update prompts: {str(e)}'
            }, status=500)
            
    except json.JSONDecodeError:
        return web.json_response({'error': 'Invalid JSON'}, status=400)
    except Exception as e:
        logger.error(f"Error updating prompts: {e}")
        return web.json_response({
            'status': 'error',
            'message': f'Internal server error: {str(e)}'
        }, status=500)

async def get_worker_status(request):
    """Get dedicated worker status for a specific stream."""
    try:
        if not stream_manager:
            return web.json_response({'error': 'Stream manager not initialized'}, status=500)
            
        request_id = request.match_info.get('request_id')
        if not request_id:
            return web.json_response({'error': 'Missing request_id'}, status=400)
        
        handler = stream_manager.handlers.get(request_id)
        if not handler:
            return web.json_response({
                'error': f'Stream {request_id} not found'
            }, status=404)
        
        if isinstance(handler.pipeline, ProcessPoolPipeline):
            worker_status = handler.pipeline.get_worker_status()
            return web.json_response({
                'request_id': request_id,
                'worker_status': worker_status,
                'architecture': 'dedicated_worker_per_stream'
            })
        else:
            return web.json_response({
                'request_id': request_id,
                'pipeline_type': 'Pipeline',
                'architecture': 'shared_single_process',
                'message': 'Shared single-process pipeline, no dedicated worker'
            })
            
    except Exception as e:
        logger.error(f"Error getting worker status: {e}")
        return web.json_response({
            'status': 'error',
            'message': f'Internal server error: {str(e)}'
        }, status=500)

async def root_info(request):
    """Root endpoint with service info (webrtc-worker compatible)."""
    try:
        return web.json_response({
            'service': 'Trickle Stream Processor',
            'version': '1.0.0',
            'description': 'ComfyStream trickle streaming processor with dedicated workers per stream',
            'capabilities': ['video-processing', 'trickle-streaming', 'dedicated-worker-per-stream'],
            'architecture': 'One dedicated worker process per stream for optimal isolation and parallel processing',
            'endpoints': {
                'start': 'POST /stream/start - Start stream processing',
                'stop': 'POST /stream/stop - Stop current stream processing',
                'stop_by_id': 'POST /stream/{request_id}/stop - Stop specific stream',
                'status': 'GET /stream/status - Get current stream status',
                'status_by_id': 'GET /stream/{request_id}/status - Get specific stream status',
                'list': 'GET /streams - List all active streams',
                'health': 'GET /health - Health check',
                'live_video': 'POST /live-video-to-video - Start live video processing',
                'update_prompts': 'POST /stream/{request_id}/update-prompts - Update prompts for a stream',
                'restart_worker': 'POST /stream/{request_id}/restart-worker - Restart dedicated worker',
                'worker_status': 'GET /stream/{request_id}/worker - Get dedicated worker status',
                'restart_workers': 'POST /stream/{request_id}/restart-workers - Restart worker (backward compatibility)',
                'workers_status': 'GET /stream/{request_id}/workers - Get worker status (backward compatibility)'
            }
        })
    except Exception as e:
        logger.error(f"Error in root info: {e}")
        return web.json_response({
            'service': 'Trickle Stream Processor',
            'status': 'error',
            'error': str(e)
        }, status=500)

async def switch_prompt(request):
    """Switch to a different prompt for an existing trickle stream.
    
    Expected request format:
    {
        "prompt_id": "abc123def456"
    }
    """
    try:
        if not stream_manager:
            return web.json_response({'error': 'Stream manager not initialized'}, status=500)
            
        request_id = request.match_info.get('request_id')
        if not request_id:
            return web.json_response({'error': 'Missing request_id'}, status=400)
        
        # Check if stream exists
        handler = stream_manager.handlers.get(request_id)
        if not handler:
            return web.json_response({
                'error': f'Stream {request_id} not found'
            }, status=404)
        
        data = await request.json()
        prompt_id = data.get('prompt_id')
        
        if not prompt_id:
            return web.json_response({
                'error': 'Missing prompt_id'
            }, status=400)
        
        # Check if pipeline supports prompt switching
        if hasattr(handler.pipeline, 'switch_prompt'):
            try:
                await handler.pipeline.switch_prompt(prompt_id)
                logger.info(f"Switched to prompt {prompt_id} for stream {request_id}")
                
                return web.json_response({
                    'status': 'success',
                    'message': f'Switched to prompt {prompt_id} for stream {request_id}',
                    'request_id': request_id,
                    'active_prompt_id': prompt_id
                })
                
            except Exception as e:
                logger.error(f"Error switching prompt for stream {request_id}: {e}")
                return web.json_response({
                    'status': 'error',
                    'message': f'Failed to switch prompt: {str(e)}'
                }, status=500)
        else:
            return web.json_response({
                'error': f'Stream {request_id} does not support prompt switching'
            }, status=400)
            
    except json.JSONDecodeError:
        return web.json_response({'error': 'Invalid JSON'}, status=400)
    except Exception as e:
        logger.error(f"Error switching prompt: {e}")
        return web.json_response({
            'status': 'error',
            'message': f'Internal server error: {str(e)}'
        }, status=500)


async def list_prompts(request):
    """List available prompts for an existing trickle stream.
    
    Returns:
    {
        "prompts": ["abc123def456", "def456ghi789"],
        "active_prompt_id": "abc123def456"
    }
    """
    try:
        if not stream_manager:
            return web.json_response({'error': 'Stream manager not initialized'}, status=500)
            
        request_id = request.match_info.get('request_id')
        if not request_id:
            return web.json_response({'error': 'Missing request_id'}, status=400)
        
        # Check if stream exists
        handler = stream_manager.handlers.get(request_id)
        if not handler:
            return web.json_response({
                'error': f'Stream {request_id} not found'
            }, status=404)
        
        # Check if pipeline supports prompt listing
        if hasattr(handler.pipeline, 'get_available_prompts') and hasattr(handler.pipeline, 'get_active_prompt_id'):
            try:
                available_prompts = handler.pipeline.get_available_prompts()
                active_prompt_id = handler.pipeline.get_active_prompt_id()
                
                return web.json_response({
                    'status': 'success',
                    'request_id': request_id,
                    'prompts': available_prompts,
                    'active_prompt_id': active_prompt_id,
                    'total_prompts': len(available_prompts)
                })
                
            except Exception as e:
                logger.error(f"Error listing prompts for stream {request_id}: {e}")
                return web.json_response({
                    'status': 'error',
                    'message': f'Failed to list prompts: {str(e)}'
                }, status=500)
        else:
            return web.json_response({
                'error': f'Stream {request_id} does not support prompt listing'
            }, status=400)
            
    except Exception as e:
        logger.error(f"Error listing prompts: {e}")
        return web.json_response({
            'status': 'error',
            'message': f'Internal server error: {str(e)}'
        }, status=500)

async def get_workspace_info(request):
    """Get workspace configuration information for debugging.
    
    Returns workspace path and configuration details for the specified stream.
    """
    try:
        if not stream_manager:
            return web.json_response({'error': 'Stream manager not initialized'}, status=500)
            
        request_id = request.match_info.get('request_id')
        if not request_id:
            return web.json_response({'error': 'Missing request_id'}, status=400)
        
        # Check if stream exists
        handler = stream_manager.handlers.get(request_id)
        if not handler:
            return web.json_response({
                'error': f'Stream {request_id} not found'
            }, status=404)
        
        # Get workspace info from the pipeline
        pipeline = handler.pipeline
        if hasattr(pipeline, 'get_workspace_info'):
            workspace_info = pipeline.get_workspace_info()
        else:
            # Fallback for regular Pipeline/ProcessPoolPipeline
            workspace_info = {
                'workspace_path': getattr(pipeline.client, 'config_kwargs', {}).get('cwd', 'unknown'),
                'pipeline_type': type(pipeline).__name__,
                'note': 'Limited info available for this pipeline type'
            }
        
        return web.json_response({
            'success': True,
            'stream_id': request_id,
            'workspace_info': workspace_info
        })
        
    except Exception as e:
        logger.error(f"Error getting workspace info: {str(e)}")
        return web.json_response({
            'error': f'Failed to get workspace info: {str(e)}'
        }, status=500)

def setup_trickle_routes(app, cors):
    """Setup trickle API routes.
    
    Args:
        app: The aiohttp web application
        cors: The CORS setup object
    """
    global stream_manager
    stream_manager = TrickleStreamManager(app_context={
        'warm_pipeline': app.get('warm_pipeline', False),
        'workspace': app.get('workspace'),
        'pipeline': app.get('pipeline')  # This will be set later during startup
    })

    # Core trickle streaming routes
    cors.add(app.router.add_post("/stream/start", start_stream))
    cors.add(app.router.add_post("/stream/{request_id}/stop", stop_stream))
    cors.add(app.router.add_get("/stream/{request_id}/status", get_stream_status))
    cors.add(app.router.add_get("/streams", list_streams))
    
    # Prompt management routes
    cors.add(app.router.add_post("/stream/{request_id}/update-prompts", update_prompts))
    cors.add(app.router.add_post("/stream/{request_id}/switch-prompt", switch_prompt))
    cors.add(app.router.add_get("/stream/{request_id}/prompts", list_prompts))
    
    # Debug and info routes
    cors.add(app.router.add_get("/stream/{request_id}/workspace-info", get_workspace_info))
    
    # ProcessPoolExecutor dedicated worker management routes
    cors.add(app.router.add_post("/stream/{request_id}/restart-worker", restart_worker))
    cors.add(app.router.add_get("/stream/{request_id}/worker", get_worker_status))
    
    # Backward compatibility routes
    cors.add(app.router.add_post("/stream/{request_id}/restart-workers", restart_workers))
    cors.add(app.router.add_get("/stream/{request_id}/workers", get_worker_status))
    
    # Process capability compatible routes (for byoc worker compatibility)
    cors.add(app.router.add_post("/stream/stop", stop_current_stream))
    cors.add(app.router.add_get("/stream/status", get_current_stream_status))
    
    # Service info routes
    cors.add(app.router.add_get("/health", health_check))
    cors.add(app.router.add_get("/", root_info))
    
    # Alias for live-video-to-video endpoint (same as stream/start)
    cors.add(app.router.add_post("/live-video-to-video", start_stream))
    
    logger.info("Trickle API routes registered")

# Cleanup function for app shutdown
async def cleanup_trickle_streams():
    """Cleanup all trickle streams on app shutdown."""
    if stream_manager:
        await stream_manager.cleanup_all()