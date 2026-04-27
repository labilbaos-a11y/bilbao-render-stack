# main.py — Servidor MCP remoto para bilbao-render-stack
import contextlib
import os
from datetime import date, timedelta
from typing import Optional

import requests
from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

MCP_AUTH_TOKEN = os.environ["MCP_AUTH_TOKEN"]

# Credenciales de Meta Ads (opcionales: si faltan, esas tools devuelven error claro).
META_ACCESS_TOKEN = os.environ.get("META_ACCESS_TOKEN", "")
META_AD_ACCOUNT_ID = os.environ.get("META_AD_ACCOUNT_ID", "")  # ya viene con prefijo "act_"
META_API_VERSION = "v21.0"
META_GRAPH = f"https://graph.facebook.com/{META_API_VERSION}"

# streamable_http_path="/" hace que el sub-app sirva en raíz, así
# al montarlo en /mcp la URL pública final queda en /mcp/.
mcp = FastMCP("bilbao-render-stack", streamable_http_path="/")

# ---------- Tools de prueba ----------

@mcp.tool()
def ping() -> str:
    """Healthcheck tool. Devuelve pong si el MCP está vivo."""
    return "pong 🏓"

@mcp.tool()
def whoami() -> dict:
    """Devuelve info básica del servidor MCP."""
    return {"service": "bilbao-render-stack", "status": "ok"}

# ---------- Helpers Meta Ads ----------

def _meta_check() -> Optional[dict]:
    """Devuelve None si las credenciales están; si no, un dict de error."""
    if not META_ACCESS_TOKEN:
        return {"error": "falta variable de entorno META_ACCESS_TOKEN"}
    if not META_AD_ACCOUNT_ID:
        return {"error": "falta variable de entorno META_AD_ACCOUNT_ID"}
    return None

def _meta_get(path: str, params: Optional[dict] = None) -> dict:
    """GET a la Graph API de Meta. Devuelve dict con la respuesta o {error: ...}."""
    p = dict(params or {})
    p["access_token"] = META_ACCESS_TOKEN
    try:
        r = requests.get(f"{META_GRAPH}/{path}", params=p, timeout=30)
        data = r.json()
        if r.status_code >= 400 or "error" in data:
            return {
                "error": data.get("error", {}).get("message", f"HTTP {r.status_code}"),
                "status_code": r.status_code,
            }
        return data
    except requests.RequestException as exc:
        return {"error": f"network error: {exc}"}

def _resolver_periodo(periodo: str) -> tuple:
    """Convierte un alias en (since, until) con formato YYYY-MM-DD.

    Alias soportados: hoy, ayer, ultimos_7d, ultimos_14d, ultimos_30d, este_mes, mes_anterior.
    """
    today = date.today()
    if periodo == "hoy":
        return today.isoformat(), today.isoformat()
    if periodo == "ayer":
        d = today - timedelta(days=1)
        return d.isoformat(), d.isoformat()
    if periodo == "ultimos_7d":
        return (today - timedelta(days=7)).isoformat(), today.isoformat()
    if periodo == "ultimos_14d":
        return (today - timedelta(days=14)).isoformat(), today.isoformat()
    if periodo == "ultimos_30d":
        return (today - timedelta(days=30)).isoformat(), today.isoformat()
    if periodo == "este_mes":
        return today.replace(day=1).isoformat(), today.isoformat()
    if periodo == "mes_anterior":
        first_this = today.replace(day=1)
        last_prev = first_this - timedelta(days=1)
        first_prev = last_prev.replace(day=1)
        return first_prev.isoformat(), last_prev.isoformat()
    # Default seguro
    return (today - timedelta(days=7)).isoformat(), today.isoformat()

# ---------- Tools Meta Ads (lectura) ----------

@mcp.tool()
def meta_account_info() -> dict:
    """Devuelve datos básicos de la cuenta publicitaria de Meta Ads conectada
    (nombre, moneda, estado, zona horaria, gasto histórico total)."""
    err = _meta_check()
    if err:
        return err
    data = _meta_get(
        META_AD_ACCOUNT_ID,
        {"fields": "name,currency,account_status,timezone_name,amount_spent,business_name"},
    )
    if "error" in data:
        return data
    status_map = {1: "activa", 2: "deshabilitada", 3: "impagada", 7: "en revisión", 9: "en revisión", 100: "cerrada"}
    return {
        "id": data.get("id"),
        "nombre": data.get("name"),
        "negocio": data.get("business_name") or None,
        "moneda": data.get("currency"),
        "zona_horaria": data.get("timezone_name"),
        "estado": status_map.get(data.get("account_status"), str(data.get("account_status"))),
        "gasto_historico_total": data.get("amount_spent"),
    }

@mcp.tool()
def meta_listar_campanas(solo_activas: bool = True, limite: int = 25) -> dict:
    """Lista campañas de Meta Ads.

    Args:
        solo_activas: si True, devuelve solo las campañas con effective_status=ACTIVE.
        limite: máximo de campañas a devolver (1-100).
    """
    err = _meta_check()
    if err:
        return err
    limite = max(1, min(int(limite or 25), 100))
    params = {
        "fields": "id,name,status,effective_status,objective,daily_budget,lifetime_budget,start_time,stop_time",
        "limit": limite,
    }
    if solo_activas:
        params["effective_status"] = '["ACTIVE"]'
    data = _meta_get(f"{META_AD_ACCOUNT_ID}/campaigns", params)
    if "error" in data:
        return data
    campanas = []
    for c in data.get("data", []):
        campanas.append({
            "id": c.get("id"),
            "nombre": c.get("name"),
            "estado": c.get("effective_status"),
            "objetivo": c.get("objective"),
            "presupuesto_diario": c.get("daily_budget"),
            "presupuesto_total": c.get("lifetime_budget"),
            "inicio": c.get("start_time"),
            "fin": c.get("stop_time"),
        })
    return {"total": len(campanas), "campanas": campanas}

@mcp.tool()
def meta_gasto_periodo(periodo: str = "ultimos_7d") -> dict:
    """Devuelve gasto, impresiones, clicks, CTR y CPC de la cuenta en un período.

    Args:
        periodo: uno de hoy, ayer, ultimos_7d, ultimos_14d, ultimos_30d, este_mes, mes_anterior.
    """
    err = _meta_check()
    if err:
        return err
    since, until = _resolver_periodo(periodo)
    data = _meta_get(
        f"{META_AD_ACCOUNT_ID}/insights",
        {
            "fields": "spend,impressions,clicks,ctr,cpc,cpm,reach",
            "time_range": '{"since":"' + since + '","until":"' + until + '"}',
            "level": "account",
        },
    )
    if "error" in data:
        return data
    rows = data.get("data", [])
    if not rows:
        return {"periodo": periodo, "desde": since, "hasta": until, "mensaje": "sin datos en ese período"}
    r = rows[0]
    return {
        "periodo": periodo,
        "desde": since,
        "hasta": until,
        "gasto": r.get("spend"),
        "impresiones": r.get("impressions"),
        "clicks": r.get("clicks"),
        "ctr": r.get("ctr"),
        "cpc": r.get("cpc"),
        "cpm": r.get("cpm"),
        "alcance": r.get("reach"),
    }

@mcp.tool()
def meta_top_campanas(periodo: str = "ultimos_7d", ordenar_por: str = "spend", limite: int = 5) -> dict:
    """Ranking de campañas en un período, ordenadas por una métrica.

    Args:
        periodo: uno de hoy, ayer, ultimos_7d, ultimos_14d, ultimos_30d, este_mes, mes_anterior.
        ordenar_por: spend, impressions, clicks, ctr o cpc.
        limite: cuántas campañas devolver (1-25).
    """
    err = _meta_check()
    if err:
        return err
    metricas_validas = {"spend", "impressions", "clicks", "ctr", "cpc"}
    if ordenar_por not in metricas_validas:
        return {"error": "ordenar_por debe ser uno de " + str(sorted(metricas_validas))}
    limite = max(1, min(int(limite or 5), 25))
    since, until = _resolver_periodo(periodo)
    data = _meta_get(
        f"{META_AD_ACCOUNT_ID}/insights",
        {
            "fields": "campaign_id,campaign_name,spend,impressions,clicks,ctr,cpc",
            "time_range": '{"since":"' + since + '","until":"' + until + '"}',
            "level": "campaign",
            "limit": 200,
        },
    )
    if "error" in data:
        return data
    rows = list(data.get("data", []))
    def _num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0
    rows.sort(key=lambda r: _num(r.get(ordenar_por)), reverse=True)
    top = []
    for r in rows[:limite]:
        top.append({
            "campana_id": r.get("campaign_id"),
            "nombre": r.get("campaign_name"),
            "gasto": r.get("spend"),
            "impresiones": r.get("impressions"),
            "clicks": r.get("clicks"),
            "ctr": r.get("ctr"),
            "cpc": r.get("cpc"),
        })
    return {
        "periodo": periodo, "desde": since, "hasta": until,
        "ordenado_por": ordenar_por, "total": len(top), "campanas": top,
    }

# A medida que sumes integraciones (GA4, GMB, Make, Reservo)
# agregás más @mcp.tool() acá.

# ---------- Auth + montaje ASGI ----------

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

# Construimos el sub-app del MCP UNA sola vez para reusar su lifespan,
# que es lo que arranca el task group interno del transport Streamable HTTP.
mcp_asgi = mcp.streamable_http_app()

@contextlib.asynccontextmanager
async def lifespan(app):
    async with mcp_asgi.router.lifespan_context(app):
        yield

app = Starlette(
    routes=[
        Route("/", root),
        Route("/health", health),
        Mount("/mcp", app=bearer_auth(mcp_asgi)),
    ],
    lifespan=lifespan,
)
