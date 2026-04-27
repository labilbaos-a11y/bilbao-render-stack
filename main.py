# main.py — Servidor MCP remoto para bilbao-render-stack
import os
from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

MCP_AUTH_TOKEN = os.environ["MCP_AUTH_TOKEN"]

mcp = FastMCP("bilbao-render-stack")

@mcp.tool()
def ping() -> str:
    """Healthcheck tool. Devuelve pong si el MCP está vivo."""
    return "pong 🏓"

@mcp.tool()
def whoami() -> dict:
    """Devuelve info básica del servidor MCP."""
    return {"service": "bilbao-render-stack", "status": "ok"}

# A medida que sumes integraciones (Meta Ads, GA4, GMB, Make, Reservo)
# agregás más @mcp.tool() acá.

# Wrapper ASGI puro (sin BaseHTTPMiddleware) que protege /mcp con Bearer.
# No bufferiza la respuesta, compatible con streaming.
def bearer_auth(app):
    async def wrapped(scope, receive, send):
        if scope["type"] != "http":
            return await app(scope, receive, send)
        headers = dict(scope.get("headers") or [])
        auth = headers.get(b"authorization", b"").decode()
        if auth != f"Bearer {MCP_AUTH_TOKEN}":
            response = JSONResponse({"error": "unauthorized"}, status_code=401)
            return await response(scope, receive, send)
        return await app(scope, receive, send)
    return wrapped

async def health(_): return JSONResponse({"status": "healthy"})
async def root(_): return JSONResponse({"status": "ok", "service": "bilbao-render-stack"})

app = Starlette(
    routes=[
        Route("/", root),
        Route("/health", health),
        Mount("/mcp", app=bearer_auth(mcp.streamable_http_app())),
    ],
)
