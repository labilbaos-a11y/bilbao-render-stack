# main.py — Servidor MCP remoto para bilbao-render-stack
import os
from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

MCP_AUTH_TOKEN = os.environ["MCP_AUTH_TOKEN"]

mcp = FastMCP("bilbao-render-stack")

@mcp.tool()
def ping() -> str:
    """Healthcheck tool. Devuelve pong si el MCP está vivo."""
    return "pong"

@mcp.tool()
def whoami() -> dict:
    """Devuelve info básica del servidor MCP."""
    return {"service": "bilbao-render-stack", "status": "ok"}

# A medida que sumes integraciones (Meta Ads, GA4, GMB, Make, Reservo)
# agregás más @mcp.tool() acá.

class BearerAuth(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if request.url.path in ("/", "/health"):
            return await call_next(request)
        auth = request.headers.get("authorization", "")
        if auth != f"Bearer {MCP_AUTH_TOKEN}":
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)

async def health(_): return JSONResponse({"status": "healthy"})
async def root(_):   return JSONResponse({"status": "ok", "service": "bilbao-render-stack"})

mcp_asgi = mcp.http_app(transport="sse")

app = Starlette(
    routes=[
        Route("/", root),
        Route("/health", health),
        Mount("/", app=mcp_asgi),
    ],
    middleware=[Middleware(BearerAuth)],
    lifespan=mcp_asgi.lifespan,
)
