"""MCP Protocol Handler - JSON-RPC 2.0 message processing.

Handles MCP protocol messages according to the MCP specification:
- initialize: Server capability negotiation
- initialized: Handshake confirmation notification
- tools/list: List available tools
- tools/call: Execute a tool

Copyright 2025 Originate Group
Licensed under the Apache License, Version 2.0
"""

import logging
from typing import Any, Callable, Awaitable, Optional

from mcp.types import Tool, TextContent
from fastapi import Request
from fastapi.responses import JSONResponse, Response

logger = logging.getLogger("common-mcp-server.protocol")


class MCPProtocolHandler:
    """Handles MCP protocol messages over HTTP."""

    def __init__(
        self,
        server_name: str,
        server_version: str,
        list_tools_fn: Callable[[], Awaitable[list[Tool]]],
        call_tool_fn: Callable[[str, dict, Optional[str], dict, bool], Awaitable[list[TextContent]]],
    ):
        """Initialize the protocol handler.

        Args:
            server_name: Name of the MCP server
            server_version: Version string
            list_tools_fn: Async function to list available tools
            call_tool_fn: Async function to execute a tool
                Signature: (name, arguments, auth_token, user, is_pat) -> list[TextContent]
                Where user is the full user dict from authentication (includes user_id, email, name, etc.)
        """
        self.server_name = server_name
        self.server_version = server_version
        self.list_tools_fn = list_tools_fn
        self.call_tool_fn = call_tool_fn

    async def handle_message(
        self,
        request: Request,
        user: dict,
    ) -> JSONResponse | Response:
        """Handle an MCP protocol message.

        Args:
            request: FastAPI request with JSON-RPC message in body
            user: Authenticated user information

        Returns:
            JSON-RPC response or HTTP 200 for notifications
        """
        try:
            body = await request.json()
            method = body.get("method")
            params = body.get("params", {})
            request_id = body.get("id")

            logger.info(f"MCP request from {user['email']}: {method}")

            # Handle initialize
            if method == "initialize":
                return await self._handle_initialize(request_id, params, user)

            # Handle initialized notification
            elif method == "initialized":
                return await self._handle_initialized(user)

            # Handle other notifications
            elif method.startswith("notifications/") or method.startswith("$/"):
                logger.info(f"Received notification: {method} from {user['email']}")
                return Response(status_code=200, content="", media_type="text/plain")

            # Handle tools/list
            elif method == "tools/list":
                return await self._handle_tools_list(request_id, user)

            # Handle tools/call
            elif method == "tools/call":
                return await self._handle_tools_call(request_id, params, request, user)

            # Unknown method
            else:
                return JSONResponse(
                    status_code=400,
                    content={
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {
                            "code": -32601,
                            "message": f"Method not found: {method}"
                        }
                    }
                )

        except Exception as e:
            logger.error(f"Error handling MCP request: {e}", exc_info=True)
            return JSONResponse(
                status_code=500,
                content={
                    "jsonrpc": "2.0",
                    "id": body.get("id") if "body" in locals() else None,
                    "error": {
                        "code": -32603,
                        "message": f"Internal error: {str(e)}"
                    }
                }
            )

    async def _handle_initialize(self, request_id: Any, params: dict, user: dict) -> JSONResponse:
        """Handle initialize request - return server capabilities."""
        logger.info(f"📡 Handling initialize request from {user['email']}")

        result = {
            "protocolVersion": "2024-11-05",
            "capabilities": {
                "tools": {
                    "listChanged": True
                },
            },
            "serverInfo": {
                "name": self.server_name,
                "version": self.server_version
            }
        }

        logger.info(f"✅ Returning initialize response with capabilities: tools")

        return JSONResponse(content={
            "jsonrpc": "2.0",
            "id": request_id,
            "result": result
        })

    async def _handle_initialized(self, user: dict) -> Response:
        """Handle initialized notification - handshake complete.

        Per JSON-RPC 2.0 spec, notifications do NOT get JSON-RPC responses.
        Return HTTP 200 with empty body.
        """
        logger.info(f"✅ MCP client initialized notification from {user['email']}")
        return Response(status_code=200, content="", media_type="text/plain")

    async def _handle_tools_list(self, request_id: Any, user: dict) -> JSONResponse:
        """Handle tools/list request - return available tools."""
        logger.info(f"🔧 Handling tools/list request from {user['email']}")

        tools = await self.list_tools_fn()
        logger.info(f"📋 Found {len(tools)} tools to return")

        result = {
            "tools": [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "inputSchema": tool.inputSchema
                }
                for tool in tools
            ]
        }

        logger.info(f"✅ Returning tools/list response with {len(tools)} tools")

        return JSONResponse(content={
            "jsonrpc": "2.0",
            "id": request_id,
            "result": result
        })

    async def _handle_tools_call(
        self,
        request_id: Any,
        params: dict,
        request: Request,
        user: dict
    ) -> JSONResponse:
        """Handle tools/call request - execute a tool."""
        tool_name = params.get("name")
        arguments = params.get("arguments", {})

        if not tool_name:
            return JSONResponse(
                status_code=400,
                content={
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {
                        "code": -32602,
                        "message": "Missing required parameter: name"
                    }
                }
            )

        # Determine authentication method and extract token
        auth_token = None
        is_pat = False

        # Check for PAT first (X-API-Key or other configured header)
        # The header name should come from configuration
        for header_name in ["X-API-Key", "Authorization"]:
            token = request.headers.get(header_name)
            if token:
                if header_name == "X-API-Key":
                    auth_token = token
                    is_pat = True
                else:
                    auth_token = token
                    is_pat = False
                break

        # Call the tool with full user context
        content_items = await self.call_tool_fn(
            tool_name,
            arguments,
            auth_token,
            user,  # Pass full user dict instead of just user_id
            is_pat
        )

        # Convert MCP content items to JSON
        result = {
            "content": [
                {
                    "type": item.type,
                    "text": item.text
                }
                for item in content_items
            ]
        }

        return JSONResponse(content={
            "jsonrpc": "2.0",
            "id": request_id,
            "result": result
        })
