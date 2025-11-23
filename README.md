# Common MCP Server

A production-ready framework for building HTTP MCP (Model Context Protocol) servers with FastAPI, supporting both OAuth 2.1 and Personal Access Token authentication.

## Features

- **HTTP Transport**: MCP over HTTP with JSON-RPC 2.0 protocol
- **Dual Authentication**: Support for both OAuth 2.1 (Keycloak/OIDC) and Personal Access Tokens
- **FastAPI Integration**: Clean integration with FastAPI applications
- **Type Safe**: Full type hints and mypy support
- **Production Ready**: Battle-tested in production environments
- **Simple API**: Minimal boilerplate, decorator-based configuration

## Installation

```bash
pip install common-mcp-server
```

Or with git submodule:

```bash
git submodule add https://github.com/Originate-Group/common-mcp-server.git
pip install -e common-mcp-server/
```

## Quick Start

```python
from fastapi import FastAPI
from common_mcp_server import MCPServer, OAuthConfig, PATConfig
from mcp.types import Tool, TextContent

# Create FastAPI app
app = FastAPI()

# Configure MCP server
mcp_server = MCPServer(
    name="my-mcp-server",
    version="1.0.0",
    oauth_config=OAuthConfig(
        jwks_url="https://auth.example.com/realms/myrealm/protocol/openid-connect/certs",
        issuer="https://auth.example.com/realms/myrealm",
    ),
    pat_config=PATConfig(
        header_name="X-API-Key",
        prefix="api_",
        verify_function=verify_api_key,  # Your PAT verification function
    ),
)

# Define your tools
async def get_tools():
    return [
        Tool(
            name="my_tool",
            description="Does something useful",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The query"}
                },
                "required": ["query"]
            }
        )
    ]

mcp_server.set_tools_provider(get_tools)

# Implement tool handler
@mcp_server.tool_handler()
async def handle_tool(name: str, arguments: dict, auth_token: str, user_id: str, is_pat: bool):
    """Handle MCP tool calls.

    Args:
        name: Tool name
        arguments: Tool arguments from MCP client
        auth_token: Authentication token (OAuth Bearer or PAT)
        user_id: Authenticated user ID
        is_pat: True if auth_token is a PAT, False if OAuth

    Returns:
        List of TextContent objects with results
    """
    if name == "my_tool":
        query = arguments.get("query")
        result = f"Processed: {query}"
        return [TextContent(type="text", text=result)]

    return [TextContent(type="text", text=f"Unknown tool: {name}")]

# Mount MCP router
app.include_router(mcp_server.get_router(), prefix="/mcp", tags=["MCP"])

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
```

## Authentication

### OAuth 2.1 Configuration

```python
from common_mcp_server import OAuthConfig

oauth_config = OAuthConfig(
    jwks_url="https://auth.example.com/realms/myrealm/protocol/openid-connect/certs",
    issuer="https://auth.example.com/realms/myrealm",
    algorithms=["RS256"],  # Optional, defaults to ["RS256"]
    verify_audience=False,  # Optional, defaults to False
)
```

### Personal Access Token Configuration

```python
from common_mcp_server import PATConfig
from fastapi import Request

async def verify_pat(token: str, request: Request) -> dict | None:
    """Verify PAT and return user information.

    Args:
        token: The PAT token value
        request: FastAPI request object (for DB access, etc.)

    Returns:
        User dict with keys: user_id, email, username, name
        Or None if token is invalid
    """
    # Your verification logic here
    # Example: check against database
    user = await db.get_user_by_pat(token)
    if not user:
        return None

    return {
        "user_id": user.id,
        "email": user.email,
        "username": user.username,
        "name": user.full_name,
    }

pat_config = PATConfig(
    header_name="X-API-Key",  # HTTP header for PAT
    prefix="api_",  # Required token prefix
    verify_function=verify_pat,
)
```

### Authentication Priority

When both OAuth and PAT are configured:
1. **PAT is checked first** (if X-API-Key header is present)
2. **OAuth is checked second** (if Authorization: Bearer header is present)
3. **401 error** if both fail or are missing

## Client Configuration

### Claude Desktop (Custom Connector)

This server is designed to be used as a Custom Connector in Claude Desktop:

1. Open Claude Desktop settings
2. Navigate to Custom Connectors
3. Add a new connector with your server URL (e.g., `https://api.example.com/mcp`)
4. Authentication is handled via OAuth 2.1 or PAT headers automatically

The server has been tested and verified working as a Custom Connector.

### Claude Code CLI

Add to `~/.claude.json`:

```json
{
  "mcpServers": {
    "my-server": {
      "type": "http",
      "url": "https://api.example.com/mcp",
      "headers": {
        "X-API-Key": "api_your_token_here"
      }
    }
  }
}
```

## Advanced Usage

### Accessing Request Dependencies

If you need access to database connections or other FastAPI dependencies in your tool handler:

```python
from fastapi import Request
from sqlalchemy.orm import Session

# Store DB session in request state during authentication
class CustomAuthenticator(DualAuthenticator):
    async def authenticate(self, request: Request) -> dict:
        user = await super().authenticate(request)
        # Add DB session to request state
        request.state.db = get_db_session()
        return user

# Access in tool handler
@mcp_server.tool_handler()
async def handle_tool(name, arguments, auth_token, user_id, is_pat):
    # Access request state via closure or pass request through
    # Your API calls can use the auth_token to make authenticated requests
    async with httpx.AsyncClient() as client:
        headers = {"X-API-Key" if is_pat else "Authorization": auth_token}
        response = await client.get("http://localhost:8000/api/v1/data", headers=headers)

    return [TextContent(type="text", text=response.text)]
```

### Custom Tool Provider

```python
from mcp.types import Tool

class MyToolProvider:
    def __init__(self, db):
        self.db = db

    async def get_tools(self) -> list[Tool]:
        # Dynamically generate tools based on database state
        tools = []
        for entity in await self.db.get_entities():
            tools.append(Tool(
                name=f"get_{entity.name}",
                description=f"Get {entity.name} data",
                inputSchema=entity.schema
            ))
        return tools

provider = MyToolProvider(db)
mcp_server.set_tools_provider(provider.get_tools)
```

## API Reference

### `MCPServer`

Main server class for creating MCP HTTP servers.

**Constructor:**
- `name: str` - Server name (shown in MCP clients)
- `version: str` - Server version (default: "1.0.0")
- `oauth_config: Optional[OAuthConfig]` - OAuth configuration
- `pat_config: Optional[PATConfig]` - PAT configuration
- `resource_url: Optional[str]` - Base URL for WWW-Authenticate header
- `tools_provider: Optional[Callable]` - Function to list available tools

**Methods:**
- `tool_handler()` - Decorator to register tool execution handler
- `get_router() -> APIRouter` - Get FastAPI router for mounting
- `set_tools_provider(provider)` - Set tools provider function

### `OAuthConfig`

OAuth 2.1 authentication configuration.

**Fields:**
- `jwks_url: str` - URL to fetch JWKS for token validation
- `issuer: str` - Expected token issuer (e.g., Keycloak realm URL)
- `algorithms: list[str]` - Allowed JWT algorithms (default: ["RS256"])
- `verify_audience: bool` - Verify audience claim (default: False)
- `audience: Optional[str]` - Expected audience value

### `PATConfig`

Personal Access Token authentication configuration.

**Fields:**
- `header_name: str` - HTTP header name (e.g., "X-API-Key")
- `prefix: str` - Required token prefix (e.g., "api_")
- `verify_function: Callable` - Async function to verify token

## Examples

See the [examples/](examples/) directory for complete working examples:

- [examples/basic.py](examples/basic.py) - Minimal example
- [examples/with_database.py](examples/with_database.py) - Database integration
- [examples/oauth_only.py](examples/oauth_only.py) - OAuth-only server
- [examples/pat_only.py](examples/pat_only.py) - PAT-only server

## Development

```bash
# Clone repository
git clone https://github.com/Originate-Group/common-mcp-server.git
cd common-mcp-server

# Install with dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Format code
black src/

# Type check
mypy src/

# Lint
ruff src/
```

## Architecture

```
┌─────────────────────┐
│  MCP Client         │
│  (Claude Code)      │
└──────────┬──────────┘
           │ HTTPS
           │ X-API-Key: pat_token
           │ Authorization: Bearer oauth_token
           ▼
┌─────────────────────────────────────┐
│  common-mcp-server                  │
│  ┌───────────────────────────────┐  │
│  │  DualAuthenticator            │  │
│  │  - Validates PAT or OAuth     │  │
│  │  - Extracts user info         │  │
│  └────────────┬──────────────────┘  │
│               │                     │
│  ┌────────────▼──────────────────┐  │
│  │  MCPProtocolHandler           │  │
│  │  - JSON-RPC 2.0 parser        │  │
│  │  - initialize, tools/list     │  │
│  │  - tools/call dispatcher      │  │
│  └────────────┬──────────────────┘  │
│               │                     │
│  ┌────────────▼──────────────────┐  │
│  │  Your Tool Handler            │  │
│  │  - Application-specific logic │  │
│  │  - API calls, DB queries      │  │
│  └───────────────────────────────┘  │
└─────────────────────────────────────┘
```

## License

Apache License 2.0 - see [LICENSE](LICENSE) file for details.

Copyright 2025 Originate Group

## Contributing

Contributions welcome! Please:
1. Fork the repository
2. Create a feature branch
3. Add tests for new functionality
4. Ensure all tests pass
5. Submit a pull request

## Support

- **Issues**: https://github.com/Originate-Group/common-mcp-server/issues
- **Documentation**: https://github.com/Originate-Group/common-mcp-server
- **Email**: info@originate.group
