"""MCP Server - Main server class for building MCP HTTP servers.

Provides a high-level interface for creating MCP servers with FastAPI,
handling authentication, protocol messages, and tool execution.

Copyright 2025 Originate Group
Licensed under the Apache License, Version 2.0
"""

import logging
from typing import Optional, Callable, Awaitable, Any

from mcp.server import Server
from mcp.types import Tool, TextContent
from fastapi import APIRouter, Request, Depends
from fastapi.responses import JSONResponse

from .auth import DualAuthenticator, OAuthConfig, PATConfig
from .protocol import MCPProtocolHandler

logger = logging.getLogger("common-mcp-server.server")


class MCPServer:
    """High-level MCP server with HTTP transport and dual authentication.

    This class provides a simple interface for creating production-ready MCP servers
    with FastAPI. It handles:
    - MCP protocol message routing
    - Dual authentication (OAuth 2.1 and PAT)
    - Tool registration and execution
    - FastAPI router generation

    Example:
        ```python
        from common_mcp_server import MCPServer, OAuthConfig, PATConfig

        # Create server
        server = MCPServer(
            name="my-mcp-server",
            version="1.0.0",
            oauth_config=OAuthConfig(
                jwks_url="https://auth.example.com/certs",
                issuer="https://auth.example.com/realm",
            ),
            pat_config=PATConfig(
                header_name="X-API-Key",
                prefix="api_",
                verify_function=verify_api_key,
            ),
        )

        # Register tool handler
        @server.tool_handler()
        async def handle_tool(name, arguments, auth_token, user, is_pat, user_agent):
            # Your tool implementation
            # user_agent contains MCP client identifier (e.g., "claude-code/2.0.55")
            return [TextContent(type="text", text="Result")]

        # Mount to FastAPI app
        app.include_router(server.get_router(), prefix="/mcp")
        ```
    """

    def __init__(
        self,
        name: str,
        version: str = "1.0.0",
        oauth_config: Optional[OAuthConfig] = None,
        pat_config: Optional[PATConfig] = None,
        resource_url: Optional[str] = None,
        tools_provider: Optional[Callable[[], Awaitable[list[Tool]]]] = None,
    ):
        """Initialize the MCP server.

        Args:
            name: Server name (shown in MCP client)
            version: Server version string
            oauth_config: OAuth 2.1 configuration (optional)
            pat_config: Personal Access Token configuration (optional)
            resource_url: Base URL for WWW-Authenticate header (optional)
            tools_provider: Async function that returns list of available tools (optional)

        Raises:
            ValueError: If neither oauth_config nor pat_config is provided
        """
        self.name = name
        self.version = version

        # Initialize authentication
        self.authenticator = DualAuthenticator(
            oauth_config=oauth_config,
            pat_config=pat_config,
            resource_url=resource_url,
        )

        # Initialize MCP server instance
        self.mcp_server = Server(name)

        # Store tools provider
        self._tools_provider = tools_provider
        self._tool_handler_fn: Optional[Callable] = None

        # Initialize protocol handler (will be created when tool handler is set)
        self._protocol_handler: Optional[MCPProtocolHandler] = None

        # Create router
        self._router = APIRouter()

    def tool_handler(self) -> Callable:
        """Decorator to register the tool execution handler.

        The decorated function should have this signature:
            async def handle_tool(
                name: str,
                arguments: dict,
                auth_token: Optional[str],
                user_id: Optional[str],
                is_pat: bool,
                user_agent: Optional[str]  # CR-005: MCP client identifier
            ) -> list[TextContent]

        Example:
            ```python
            @server.tool_handler()
            async def handle_tool(name, arguments, auth_token, user_id, is_pat, user_agent):
                # Implement your tool execution logic
                # user_agent contains the MCP client identifier (e.g., "claude-code/2.0.55")
                if name == "my_tool":
                    return [TextContent(type="text", text="Success")]
                return [TextContent(type="text", text=f"Unknown tool: {name}")]
            ```
        """
        def decorator(fn: Callable) -> Callable:
            self._tool_handler_fn = fn

            # Create protocol handler now that we have the tool handler
            self._protocol_handler = MCPProtocolHandler(
                server_name=self.name,
                server_version=self.version,
                list_tools_fn=self._list_tools,
                call_tool_fn=fn,
            )

            # Register routes
            self._register_routes()

            return fn

        return decorator

    async def _list_tools(self) -> list[Tool]:
        """Internal method to list tools."""
        if self._tools_provider:
            return await self._tools_provider()
        return []

    def _register_routes(self):
        """Register FastAPI routes for MCP endpoints."""

        @self._router.post("")
        async def mcp_post_endpoint(
            request: Request,
            user: dict = Depends(self.authenticator.authenticate)
        ) -> JSONResponse:
            """MCP HTTP endpoint - handles MCP protocol messages.

            Headers:
                Authorization: Bearer <access_token> (for OAuth)
                X-API-Key: <pat_token> (for PAT, if configured)

            Body:
                MCP protocol message (JSON-RPC 2.0 format)

            Returns:
                MCP protocol response
            """
            if not self._protocol_handler:
                return JSONResponse(
                    status_code=500,
                    content={
                        "error": "Tool handler not registered. Use @server.tool_handler() decorator."
                    }
                )

            return await self._protocol_handler.handle_message(request, user)

        @self._router.get("")
        async def mcp_info_endpoint(
            user: dict = Depends(self.authenticator.authenticate)
        ) -> JSONResponse:
            """MCP server information endpoint.

            Provides metadata about the MCP server for authenticated users.
            """
            return JSONResponse(content={
                "name": self.name,
                "version": self.version,
                "transport": "http",
                "authentication": ["oauth2.1"] if self.authenticator.oauth_config else [] +
                                ["pat"] if self.authenticator.pat_config else [],
                "user": user["email"],
                "endpoints": {
                    "protocol": "POST / (MCP JSON-RPC 2.0)",
                    "info": "GET / (Server info)"
                }
            })

    def get_router(self) -> APIRouter:
        """Get the FastAPI router for mounting in your application.

        Returns:
            APIRouter: FastAPI router with MCP endpoints

        Raises:
            RuntimeError: If tool_handler was not registered

        Example:
            ```python
            app = FastAPI()
            app.include_router(
                mcp_server.get_router(),
                prefix="/mcp",
                tags=["MCP"]
            )
            ```
        """
        if not self._protocol_handler:
            raise RuntimeError(
                "Tool handler not registered. Use @server.tool_handler() decorator before calling get_router()."
            )

        return self._router

    def set_tools_provider(self, provider: Callable[[], Awaitable[list[Tool]]]):
        """Set the tools provider function.

        Args:
            provider: Async function that returns list of available tools

        Example:
            ```python
            async def get_my_tools():
                return [
                    Tool(name="my_tool", description="...", inputSchema={...})
                ]

            server.set_tools_provider(get_my_tools)
            ```
        """
        self._tools_provider = provider
