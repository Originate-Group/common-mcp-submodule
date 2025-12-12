"""Authentication module for MCP servers.

Provides dual authentication support:
1. OAuth 2.1 with JWKS validation
2. Personal Access Token (PAT) via custom header

Copyright 2025 Originate Group
Licensed under the Apache License, Version 2.0
"""

import logging
from typing import Optional, Callable, Awaitable
from dataclasses import dataclass

import httpx
from fastapi import Request, HTTPException
from jose import jwt, JWTError
from jose.exceptions import ExpiredSignatureError, JWTClaimsError

logger = logging.getLogger("common-mcp-server.auth")


class TokenValidationError(Exception):
    """Custom exception for token validation errors."""
    pass


@dataclass
class OAuthConfig:
    """OAuth 2.1 authentication configuration.

    Attributes:
        jwks_url: URL to fetch JSON Web Key Set for token validation
        issuer: Expected token issuer (e.g., Keycloak realm URL)
        algorithms: List of allowed JWT signing algorithms (default: ["RS256"])
        verify_audience: Whether to verify audience claim (default: False)
        audience: Expected audience value if verify_audience is True
    """
    jwks_url: str
    issuer: str
    algorithms: list[str] = None
    verify_audience: bool = False
    audience: Optional[str] = None

    def __post_init__(self):
        if self.algorithms is None:
            self.algorithms = ["RS256"]


@dataclass
class PATConfig:
    """Personal Access Token authentication configuration.

    Attributes:
        header_name: HTTP header name for PAT (e.g., "X-API-Key")
        prefix: Required token prefix (e.g., "raas_pat_")
        verify_function: Async function to verify token and return user
                        Signature: async (token: str, request: Request) -> dict | None
    """
    header_name: str
    prefix: str
    verify_function: Callable[[str, Request], Awaitable[Optional[dict]]]


class DualAuthenticator:
    """Handles dual authentication with OAuth and PAT.

    Validates requests using either OAuth 2.1 tokens or Personal Access Tokens,
    with PAT taking priority if both are present.
    """

    def __init__(
        self,
        oauth_config: Optional[OAuthConfig] = None,
        pat_config: Optional[PATConfig] = None,
        resource_url: Optional[str] = None,
    ):
        """Initialize the authenticator.

        Args:
            oauth_config: OAuth 2.1 configuration (optional)
            pat_config: PAT configuration (optional)
            resource_url: Base URL for WWW-Authenticate header (optional)

        Raises:
            ValueError: If neither oauth_config nor pat_config is provided
        """
        if not oauth_config and not pat_config:
            raise ValueError("At least one of oauth_config or pat_config must be provided")

        self.oauth_config = oauth_config
        self.pat_config = pat_config
        self.resource_url = resource_url
        self._jwks_cache: Optional[dict] = None

    async def _get_jwks(self) -> dict:
        """Fetch Keycloak's JSON Web Key Set for token validation."""
        if not self.oauth_config:
            raise TokenValidationError("OAuth not configured")

        # TODO: Add caching with TTL
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                response = await client.get(self.oauth_config.jwks_url)
                response.raise_for_status()
                return response.json()
            except Exception as e:
                logger.error(f"Failed to fetch JWKS: {e}")
                raise TokenValidationError(f"Unable to fetch public keys: {e}")

    async def _validate_oauth_token(self, authorization: Optional[str]) -> dict:
        """Validate OAuth access token and extract user information.

        Args:
            authorization: The Authorization header value (Bearer <token>)

        Returns:
            dict: Token payload with user information

        Raises:
            TokenValidationError: If token is invalid or expired
        """
        if not self.oauth_config:
            raise TokenValidationError("OAuth not configured")

        if not authorization or not authorization.startswith("Bearer "):
            raise TokenValidationError("Missing or invalid Authorization header")

        token = authorization.replace("Bearer ", "")

        try:
            # Fetch JWKS for signature verification
            jwks = await self._get_jwks()

            # Decode and validate token
            payload = jwt.decode(
                token,
                jwks,
                algorithms=self.oauth_config.algorithms,
                issuer=self.oauth_config.issuer,
                options={
                    "verify_signature": True,
                    "verify_aud": self.oauth_config.verify_audience,
                    "verify_iat": True,
                    "verify_exp": True,
                    "verify_nbf": True,
                    "verify_iss": True,
                },
                audience=self.oauth_config.audience if self.oauth_config.verify_audience else None,
            )

            logger.info(f"✅ OAuth token validated for user: {payload.get('sub')}")
            return {
                "user_id": payload.get("sub"),
                "email": payload.get("email"),
                "username": payload.get("preferred_username"),
                "name": payload.get("name"),
                "auth_method": "oauth",
            }

        except ExpiredSignatureError:
            raise TokenValidationError("Token has expired")
        except JWTClaimsError as e:
            raise TokenValidationError(f"Invalid token claims: {e}")
        except JWTError as e:
            raise TokenValidationError(f"Invalid token: {e}")
        except Exception as e:
            logger.error(f"Unexpected error validating OAuth token: {e}")
            raise TokenValidationError(f"Token validation failed: {e}")

    async def _validate_pat(self, token: str, request: Request) -> dict:
        """Validate Personal Access Token.

        Args:
            token: PAT token value
            request: FastAPI request object

        Returns:
            dict: User information

        Raises:
            TokenValidationError: If token is invalid
        """
        if not self.pat_config:
            raise TokenValidationError("PAT not configured")

        # Check prefix
        if not token.startswith(self.pat_config.prefix):
            raise TokenValidationError(f"Invalid PAT format (must start with '{self.pat_config.prefix}')")

        # Call application-specific verification function
        user = await self.pat_config.verify_function(token, request)

        if not user:
            raise TokenValidationError("Invalid or expired personal access token")

        logger.info(f"✅ PAT authenticated for user: {user.get('email')}")

        # Ensure consistent user structure while preserving all fields from verify_function
        # This allows application-specific fields (like organization_ids) to pass through
        result = {
            "user_id": user.get("user_id"),
            "email": user.get("email"),
            "username": user.get("username"),
            "name": user.get("name"),
            "auth_method": "pat",
        }
        # Preserve any additional fields from the verify function (e.g., organization_ids)
        for key, value in user.items():
            if key not in result:
                result[key] = value
        return result

    async def authenticate(self, request: Request) -> dict:
        """Authenticate request using PAT or OAuth.

        Authentication priority:
        1. PAT (header configured in PATConfig)
        2. OAuth (Authorization: Bearer header)

        Args:
            request: FastAPI request object

        Returns:
            dict: User information with keys: user_id, email, username, name, auth_method

        Raises:
            HTTPException: 401 if authentication fails
        """
        # Try PAT authentication first
        if self.pat_config:
            pat_token = request.headers.get(self.pat_config.header_name)
            if pat_token:
                try:
                    return await self._validate_pat(pat_token, request)
                except TokenValidationError as e:
                    logger.warning(f"PAT validation failed: {e}")
                    raise HTTPException(
                        status_code=401,
                        detail=str(e),
                        headers={"WWW-Authenticate": "Bearer"}
                    )

        # Fall back to OAuth authentication
        if self.oauth_config:
            authorization = request.headers.get("Authorization")
            try:
                return await self._validate_oauth_token(authorization)
            except TokenValidationError as e:
                logger.warning(f"OAuth validation failed: {e}")

                # Construct WWW-Authenticate header
                www_authenticate = "Bearer"
                if self.resource_url:
                    www_authenticate += f' resource_metadata="{self.resource_url}/.well-known/oauth-protected-resource"'

                raise HTTPException(
                    status_code=401,
                    detail=str(e),
                    headers={"WWW-Authenticate": www_authenticate}
                )

        # No authentication method available
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"}
        )
