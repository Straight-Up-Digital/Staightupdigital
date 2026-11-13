"""Bearer-token authentication for the MCP streamable HTTP transport."""
from __future__ import annotations

import hmac
from typing import Any

from config_loader import env

MIN_TOKEN_LENGTH = 32


class BearerTokenMiddleware:
    def __init__(self, app: Any, token: str):
        if len(token) < MIN_TOKEN_LENGTH:
            raise ValueError(f"MCP bearer tokens must be at least {MIN_TOKEN_LENGTH} characters.")
        self.app = app
        self._token = token.encode("utf-8")

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        values = [
            value for name, value in scope.get("headers", [])
            if name.lower() == b"authorization"
        ]
        if len(values) != 1:
            await self._reject(send)
            return

        scheme, separator, supplied_token = values[0].partition(b" ")
        if (
            scheme.lower() != b"bearer"
            or not separator
            or not hmac.compare_digest(supplied_token, self._token)
        ):
            await self._reject(send)
            return

        await self.app(scope, receive, send)

    @staticmethod
    async def _reject(send: Any) -> None:
        body = b'{"detail":"Unauthorized"}'
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                    (b"www-authenticate", b"Bearer"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


def run_authenticated_mcp(server: Any) -> None:
    token = env("MCP_BEARER_TOKEN")
    if not token or len(token.strip()) < MIN_TOKEN_LENGTH:
        raise RuntimeError(
            f"MCP_BEARER_TOKEN is required and must be at least {MIN_TOKEN_LENGTH} characters."
        )

    host = env("MCP_HOST", "127.0.0.1")
    port = int(env("MCP_PORT", "8000"))
    if not 1 <= port <= 65535:
        raise ValueError("MCP_PORT must be between 1 and 65535.")

    app = server.streamable_http_app(host=host)
    import uvicorn

    uvicorn.run(
        BearerTokenMiddleware(app, token.strip()),
        host=host,
        port=port,
        log_level=server.settings.log_level.lower(),
    )
