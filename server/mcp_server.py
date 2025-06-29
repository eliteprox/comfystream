"""
MCP (Model Context Protocol) Server for ComfyStream

This MCP server provides access to ComfyStream's WHIP RTC connections,
pipeline status, and output stream subscription functionality.
"""

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional, Sequence
from contextlib import asynccontextmanager

# MCP imports
from mcp.server import Server
from mcp.server.models import InitializationOptions
from mcp.server.session import ServerSession
from mcp.types import (
    Resource,
    Tool,
    TextContent,
    ImageContent,
    EmbeddedResource,
    CallToolRequest,
    CallToolResult,
    ListResourcesRequest,
    ListResourcesResult,
    ListToolsRequest,
    ListToolsResult,
    ReadResourceRequest,
    ReadResourceResult,
)

from aiohttp import ClientSession
import base64
import io

logger = logging.getLogger(__name__)

class ComfyStreamMCPServer:
    """MCP Server for ComfyStream WHIP/WHEP functionality."""
    
    def __init__(self, comfystream_host: str = "localhost", comfystream_port: int = 8889):
        self.comfystream_host = comfystream_host
        self.comfystream_port = comfystream_port
        self.base_url = f"http://{comfystream_host}:{comfystream_port}"
        self.server = Server("comfystream-mcp")
        self._setup_handlers()
        
    def _setup_handlers(self):
        """Set up MCP server handlers."""
        
        @self.server.list_resources()
        async def list_resources() -> List[Resource]:
            """List available ComfyStream resources."""
            return [
                Resource(
                    uri="comfystream://whip/sessions",
                    name="WHIP Sessions",
                    description="Active WHIP ingestion sessions",
                    mimeType="application/json",
                ),
                Resource(
                    uri="comfystream://whep/sessions", 
                    name="WHEP Sessions",
                    description="Active WHEP subscription sessions",
                    mimeType="application/json",
                ),
                Resource(
                    uri="comfystream://pipeline/status",
                    name="Pipeline Status",
                    description="Current pipeline processing status",
                    mimeType="application/json",
                ),
                Resource(
                    uri="comfystream://ice/servers",
                    name="ICE Servers",
                    description="Available ICE servers for WebRTC connections",
                    mimeType="application/json",
                ),
                Resource(
                    uri="comfystream://frames/latest",
                    name="Latest Processed Frame",
                    description="Most recent processed video frame",
                    mimeType="image/jpeg",
                ),
            ]
            
        @self.server.read_resource()
        async def read_resource(uri: str) -> ReadResourceResult:
            """Read ComfyStream resource data."""
            
            if uri == "comfystream://whip/sessions":
                whip_stats = await self._get_whip_stats()
                return ReadResourceResult(
                    contents=[
                        TextContent(
                            type="text",
                            text=json.dumps(whip_stats, indent=2)
                        )
                    ]
                )
                
            elif uri == "comfystream://whep/sessions":
                whep_stats = await self._get_whep_stats()
                return ReadResourceResult(
                    contents=[
                        TextContent(
                            type="text", 
                            text=json.dumps(whep_stats, indent=2)
                        )
                    ]
                )
                
            elif uri == "comfystream://pipeline/status":
                pipeline_status = await self._get_pipeline_status()
                return ReadResourceResult(
                    contents=[
                        TextContent(
                            type="text",
                            text=json.dumps(pipeline_status, indent=2)
                        )
                    ]
                )
                
            elif uri == "comfystream://ice/servers":
                ice_servers = await self._get_ice_servers()
                return ReadResourceResult(
                    contents=[
                        TextContent(
                            type="text",
                            text=json.dumps(ice_servers, indent=2)
                        )
                    ]
                )
                
            elif uri == "comfystream://frames/latest":
                frame_data = await self._get_latest_frame()
                if frame_data:
                    return ReadResourceResult(
                        contents=[
                            ImageContent(
                                type="image",
                                data=frame_data,
                                mimeType="image/jpeg"
                            )
                        ]
                    )
                else:
                    return ReadResourceResult(
                        contents=[
                            TextContent(
                                type="text",
                                text="No frames available"
                            )
                        ]
                    )
                    
            else:
                raise ValueError(f"Unknown resource: {uri}")
                
        @self.server.list_tools()
        async def list_tools() -> List[Tool]:
            """List available ComfyStream tools."""
            return [
                Tool(
                    name="create_whip_session",
                    description="Create a new WHIP ingestion session",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "sdp_offer": {
                                "type": "string",
                                "description": "SDP offer for the WebRTC connection"
                            },
                            "channel_id": {
                                "type": "string", 
                                "description": "Channel ID for the session (optional)",
                                "default": "default"
                            },
                            "prompts": {
                                "type": "array",
                                "description": "ComfyUI processing prompts (optional)",
                                "items": {"type": "object"}
                            }
                        },
                        "required": ["sdp_offer"]
                    }
                ),
                Tool(
                    name="terminate_whip_session",
                    description="Terminate a WHIP ingestion session",
                    inputSchema={
                        "type": "object", 
                        "properties": {
                            "resource_id": {
                                "type": "string",
                                "description": "WHIP resource ID to terminate"
                            }
                        },
                        "required": ["resource_id"]
                    }
                ),
                Tool(
                    name="create_whep_subscription",
                    description="Create a new WHEP subscription session",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "sdp_offer": {
                                "type": "string",
                                "description": "SDP offer for the WebRTC connection"
                            },
                            "stream_id": {
                                "type": "string",
                                "description": "Stream ID to subscribe to (optional)",
                                "default": "default"
                            }
                        },
                        "required": ["sdp_offer"]
                    }
                ),
                Tool(
                    name="terminate_whep_subscription",
                    description="Terminate a WHEP subscription session",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "resource_id": {
                                "type": "string", 
                                "description": "WHEP resource ID to terminate"
                            }
                        },
                        "required": ["resource_id"]
                    }
                ),
                Tool(
                    name="update_pipeline_prompts",
                    description="Update ComfyUI processing prompts",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "prompts": {
                                "type": "array",
                                "description": "New ComfyUI prompts",
                                "items": {"type": "object"}
                            }
                        },
                        "required": ["prompts"]
                    }
                ),
                Tool(
                    name="get_pipeline_nodes",
                    description="Get information about pipeline nodes",
                    inputSchema={
                        "type": "object",
                        "properties": {},
                        "additionalProperties": False
                    }
                ),
                Tool(
                    name="check_server_health",
                    description="Check ComfyStream server health",
                    inputSchema={
                        "type": "object",
                        "properties": {},
                        "additionalProperties": False
                    }
                ),
            ]
            
        @self.server.call_tool()
        async def call_tool(name: str, arguments: Dict[str, Any]) -> CallToolResult:
            """Execute ComfyStream tools."""
            
            if name == "create_whip_session":
                result = await self._create_whip_session(
                    arguments["sdp_offer"],
                    arguments.get("channel_id", "default"),
                    arguments.get("prompts")
                )
                return CallToolResult(
                    contents=[
                        TextContent(
                            type="text",
                            text=json.dumps(result, indent=2)
                        )
                    ]
                )
                
            elif name == "terminate_whip_session":
                result = await self._terminate_whip_session(arguments["resource_id"])
                return CallToolResult(
                    contents=[
                        TextContent(
                            type="text",
                            text=json.dumps({"success": result}, indent=2)
                        )
                    ]
                )
                
            elif name == "create_whep_subscription":
                result = await self._create_whep_subscription(
                    arguments["sdp_offer"],
                    arguments.get("stream_id", "default")
                )
                return CallToolResult(
                    contents=[
                        TextContent(
                            type="text",
                            text=json.dumps(result, indent=2)
                        )
                    ]
                )
                
            elif name == "terminate_whep_subscription":
                result = await self._terminate_whep_subscription(arguments["resource_id"])
                return CallToolResult(
                    contents=[
                        TextContent(
                            type="text",
                            text=json.dumps({"success": result}, indent=2)
                        )
                    ]
                )
                
            elif name == "update_pipeline_prompts":
                result = await self._update_pipeline_prompts(arguments["prompts"])
                return CallToolResult(
                    contents=[
                        TextContent(
                            type="text",
                            text=json.dumps({"success": result}, indent=2)
                        )
                    ]
                )
                
            elif name == "get_pipeline_nodes":
                result = await self._get_pipeline_nodes()
                return CallToolResult(
                    contents=[
                        TextContent(
                            type="text",
                            text=json.dumps(result, indent=2)
                        )
                    ]
                )
                
            elif name == "check_server_health":
                result = await self._check_server_health()
                return CallToolResult(
                    contents=[
                        TextContent(
                            type="text",
                            text=json.dumps(result, indent=2)
                        )
                    ]
                )
                
            else:
                raise ValueError(f"Unknown tool: {name}")

    async def _get_whip_stats(self) -> Dict[str, Any]:
        """Get WHIP session statistics."""
        try:
            async with ClientSession() as session:
                async with session.get(f"{self.base_url}/whip-stats") as response:
                    if response.status == 200:
                        return await response.json()
                    else:
                        return {"error": f"HTTP {response.status}"}
        except Exception as e:
            return {"error": str(e)}
            
    async def _get_whep_stats(self) -> Dict[str, Any]:
        """Get WHEP session statistics."""
        try:
            async with ClientSession() as session:
                async with session.get(f"{self.base_url}/whep-stats") as response:
                    if response.status == 200:
                        return await response.json()
                    else:
                        return {"error": f"HTTP {response.status}"}
        except Exception as e:
            return {"error": str(e)}
            
    async def _get_pipeline_status(self) -> Dict[str, Any]:
        """Get pipeline processing status."""
        try:
            # Try the processing status endpoint
            async with ClientSession() as session:
                async with session.get(f"{self.base_url}/processing/status") as response:
                    if response.status == 200:
                        return await response.json()
                    
                # Fallback: get basic health status
                async with session.get(f"{self.base_url}/") as health_response:
                    if health_response.status == 200:
                        # Combine WHIP and WHEP stats for pipeline status
                        whip_stats = await self._get_whip_stats()
                        whep_stats = await self._get_whep_stats()
                        
                        return {
                            "healthy": True,
                            "whip_sessions": len(whip_stats) if isinstance(whip_stats, dict) else 0,
                            "whep_sessions": len(whep_stats) if isinstance(whep_stats, dict) else 0,
                            "active_pipelines": len(whip_stats) if isinstance(whip_stats, dict) else 0,
                        }
                    else:
                        return {"healthy": False, "error": f"HTTP {health_response.status}"}
        except Exception as e:
            return {"healthy": False, "error": str(e)}
            
    async def _get_ice_servers(self) -> Dict[str, Any]:
        """Get ICE server configuration."""
        try:
            # Make a OPTIONS request to WHIP endpoint to get ICE servers
            async with ClientSession() as session:
                async with session.options(f"{self.base_url}/whip") as response:
                    ice_servers = []
                    link_header = response.headers.get('Link', '')
                    
                    if link_header:
                        # Parse Link header for ICE servers
                        for link in link_header.split(','):
                            if 'rel="ice-server"' in link:
                                # Extract URL from <url>
                                url_start = link.find('<') + 1
                                url_end = link.find('>')
                                if url_start > 0 and url_end > url_start:
                                    ice_url = link[url_start:url_end]
                                    ice_servers.append({"urls": ice_url})
                    
                    # Add default STUN servers if none found
                    if not ice_servers:
                        ice_servers = [
                            {"urls": "stun:stun.l.google.com:19302"},
                            {"urls": "stun:stun.cloudflare.com:3478"},
                            {"urls": "stun:stun1.l.google.com:19302"},
                            {"urls": "stun:stun2.l.google.com:19302"},
                            {"urls": "stun:stun3.l.google.com:19302"},
                        ]
                    
                    return {"ice_servers": ice_servers}
        except Exception as e:
            return {"error": str(e)}
            
    async def _get_latest_frame(self) -> Optional[str]:
        """Get the latest processed frame as base64-encoded JPEG."""
        try:
            # Try to get frame from frame buffer endpoint
            async with ClientSession() as session:
                async with session.get(f"{self.base_url}/frame") as response:
                    if response.status == 200:
                        frame_data = await response.read()
                        return base64.b64encode(frame_data).decode('utf-8')
                    else:
                        return None
        except Exception as e:
            logger.error(f"Error getting latest frame: {e}")
            return None
            
    async def _create_whip_session(self, sdp_offer: str, channel_id: str = "default", prompts: Optional[List[Dict]] = None) -> Dict[str, Any]:
        """Create a new WHIP ingestion session."""
        try:
            headers = {"Content-Type": "application/sdp"}
            url = f"{self.base_url}/whip"
            
            # Add query parameters
            params = {"channelId": channel_id}
            if prompts:
                params["prompts"] = json.dumps(prompts)
                
            async with ClientSession() as session:
                async with session.post(url, data=sdp_offer, headers=headers, params=params) as response:
                    result = {
                        "status": response.status,
                        "headers": dict(response.headers),
                    }
                    
                    if response.status == 201:
                        result["sdp_answer"] = await response.text()
                        result["resource_url"] = response.headers.get("Location")
                        result["success"] = True
                    else:
                        result["error"] = await response.text()
                        result["success"] = False
                        
                    return result
        except Exception as e:
            return {"success": False, "error": str(e)}
            
    async def _terminate_whip_session(self, resource_id: str) -> bool:
        """Terminate a WHIP session."""
        try:
            async with ClientSession() as session:
                async with session.delete(f"{self.base_url}/whip/{resource_id}") as response:
                    return response.status == 200
        except Exception as e:
            logger.error(f"Error terminating WHIP session: {e}")
            return False
            
    async def _create_whep_subscription(self, sdp_offer: str, stream_id: str = "default") -> Dict[str, Any]:
        """Create a new WHEP subscription."""
        try:
            headers = {"Content-Type": "application/sdp"}
            url = f"{self.base_url}/whep"
            params = {"streamId": stream_id}
                
            async with ClientSession() as session:
                async with session.post(url, data=sdp_offer, headers=headers, params=params) as response:
                    result = {
                        "status": response.status,
                        "headers": dict(response.headers),
                    }
                    
                    if response.status == 201:
                        result["sdp_answer"] = await response.text()
                        result["resource_url"] = response.headers.get("Location")
                        result["success"] = True
                    else:
                        result["error"] = await response.text()
                        result["success"] = False
                        
                    return result
        except Exception as e:
            return {"success": False, "error": str(e)}
            
    async def _terminate_whep_subscription(self, resource_id: str) -> bool:
        """Terminate a WHEP subscription."""
        try:
            async with ClientSession() as session:
                async with session.delete(f"{self.base_url}/whep/{resource_id}") as response:
                    return response.status == 200
        except Exception as e:
            logger.error(f"Error terminating WHEP subscription: {e}")
            return False
            
    async def _update_pipeline_prompts(self, prompts: List[Dict]) -> bool:
        """Update pipeline prompts."""
        try:
            async with ClientSession() as session:
                async with session.post(
                    f"{self.base_url}/set-prompt",
                    json=prompts,
                    headers={"Content-Type": "application/json"}
                ) as response:
                    return response.status == 200
        except Exception as e:
            logger.error(f"Error updating pipeline prompts: {e}")
            return False
            
    async def _get_pipeline_nodes(self) -> Dict[str, Any]:
        """Get pipeline node information."""
        try:
            # This would typically come from a control channel message
            # For now, return a placeholder indicating the functionality exists
            return {
                "message": "Pipeline nodes info requires active WebRTC connection with control channel",
                "available_via": "WebRTC control channel with 'get_nodes' message type"
            }
        except Exception as e:
            return {"error": str(e)}
            
    async def _check_server_health(self) -> Dict[str, Any]:
        """Check server health."""
        try:
            async with ClientSession() as session:
                async with session.get(f"{self.base_url}/") as response:
                    if response.status == 200:
                        return {
                            "healthy": True,
                            "status": "OK",
                            "timestamp": asyncio.get_event_loop().time()
                        }
                    else:
                        return {
                            "healthy": False,
                            "status": f"HTTP {response.status}",
                            "timestamp": asyncio.get_event_loop().time()
                        }
        except Exception as e:
            return {
                "healthy": False,
                "error": str(e),
                "timestamp": asyncio.get_event_loop().time()
            }

    async def run(self, transport_type: str = "stdio"):
        """Run the MCP server."""
        if transport_type == "stdio":
            from mcp.server.stdio import stdio_server
            async with stdio_server() as (read_stream, write_stream):
                await self.server.run(
                    read_stream,
                    write_stream,
                    InitializationOptions(
                        server_name="comfystream-mcp",
                        server_version="1.0.0",
                        capabilities=self.server.get_capabilities(
                            notification_options=None,
                            experimental_capabilities=None,
                        ),
                    ),
                )
        else:
            raise ValueError(f"Unsupported transport type: {transport_type}")


async def main():
    """Main entry point for the MCP server."""
    import argparse
    
    parser = argparse.ArgumentParser(description="ComfyStream MCP Server")
    parser.add_argument("--host", default="localhost", help="ComfyStream server host")
    parser.add_argument("--port", type=int, default=8889, help="ComfyStream server port")
    parser.add_argument("--transport", default="stdio", choices=["stdio"], help="Transport type")
    
    args = parser.parse_args()
    
    logging.basicConfig(level=logging.INFO)
    
    server = ComfyStreamMCPServer(args.host, args.port)
    await server.run(args.transport)


if __name__ == "__main__":
    asyncio.run(main())