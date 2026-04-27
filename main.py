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

# Credenciales de Meta (opcionales: si faltan, esas tools devuelven error claro).
META_ACCESS_TOKEN = os.environ.get("META_ACCESS_TOKEN", "")
META_AD_ACCOUNT_ID = os.environ.get("META_AD_ACCOUNT_ID", "")  # ya viene con prefijo "act_"
META_WABA_ID = os.environ.get("META_WABA_ID", "")
META_WABA_PHONE_ID = os.environ.get("META_WABA_PHONE_ID", "")
META_FB_PAGE_ID = os.environ.get("META_FB_PAGE_ID", "")
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


# ---------- Helpers Meta (Graph API) ----------
def _need(var_name: str, var_value: str) -> Optional[dict]:
    if not var_value:
        return {"error": f"falta variable de entorno {var_name}"}
    return None


def _meta_check_ads() -> Optional[dict]:
    return _need("META_ACCESS_TOKEN", META_ACCESS_TOKEN) or _need("META_AD_ACCOUNT_ID", META_AD_ACCOUNT_ID)


def _meta_check_waba() -> Optional[dict]:
    return _need("META_ACCESS_TOKEN", META_ACCESS_TOKEN) or _need("META_WABA_ID", META_WABA_ID)


def _meta_check_phone() -> Optional[dict]:
    return _need("META_ACCESS_TOKEN", META_ACCESS_TOKEN) or _need("META_WABA_PHONE_ID", META_WABA_PHONE_ID)


def _meta_check_page() -> Optional[dict]:
    return _need("META_ACCESS_TOKEN", META_ACCESS_TOKEN) or _need("META_FB_PAGE_ID", META_FB_PAGE_ID)


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


def _meta_post(path: str, payload: dict) -> dict:
    """POST a la Graph API de Meta (JSON). Devuelve respuesta o {error: ...}."""
    try:
        r = requests.post(
            f"{META_GRAPH}/{path}",
            params={"access_token": META_ACCESS_TOKEN},
            json=payload,
            timeout=30,
        )
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
    Alias soportados: hoy, ayer, ultimos_7d, ultimos_14d, ultimos_30d,
    este_mes, mes_anterior.
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
    return (today - timedelta(days=7)).isoformat(), today.isoformat()


# ---------- Tools Meta Ads (lectura) ----------
@mcp.tool()
def meta_account_info() -> dict:
    """Devuelve datos básicos de la cuenta publicitaria de Meta Ads conectada
    (nombre, moneda, estado, zona horaria, gasto histórico total)."""
    err = _meta_check_ads()
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
    err = _meta_check_ads()
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
    err = _meta_check_ads()
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
    err = _meta_check_ads()
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
        "periodo": periodo,
        "desde": since,
        "hasta": until,
        "ordenado_por": ordenar_por,
        "total": len(top),
        "campanas": top,
    }


# ---------- Tools WhatsApp Business (lectura) ----------
@mcp.tool()
def wsp_info_numero() -> dict:
    """Devuelve info del número de WhatsApp Business conectado
    (nombre verificado, número visible, calidad, plataforma)."""
    err = _meta_check_phone()
    if err:
        return err
    data = _meta_get(
        META_WABA_PHONE_ID,
        {"fields": "verified_name,display_phone_number,quality_rating,platform_type,code_verification_status,throughput"},
    )
    if "error" in data:
        return data
    return {
        "id": data.get("id") or META_WABA_PHONE_ID,
        "nombre_verificado": data.get("verified_name"),
        "numero": data.get("display_phone_number"),
        "calidad": data.get("quality_rating"),
        "plataforma": data.get("platform_type"),
        "verificacion": data.get("code_verification_status"),
        "throughput": (data.get("throughput") or {}).get("level"),
    }


@mcp.tool()
def wsp_listar_plantillas(limite: int = 25, solo_aprobadas: bool = True) -> dict:
    """Lista las plantillas de mensajes de WhatsApp Business.

    Args:
        limite: máximo de plantillas a devolver (1-100).
        solo_aprobadas: si True, filtra solo las plantillas con status APPROVED.
    """
    err = _meta_check_waba()
    if err:
        return err
    limite = max(1, min(int(limite or 25), 100))
    data = _meta_get(
        f"{META_WABA_ID}/message_templates",
        {"fields": "name,language,status,category,components", "limit": limite},
    )
    if "error" in data:
        return data
    plantillas = []
    for t in data.get("data", []):
        if solo_aprobadas and t.get("status") != "APPROVED":
            continue
        plantillas.append({
            "nombre": t.get("name"),
            "idioma": t.get("language"),
            "estado": t.get("status"),
            "categoria": t.get("category"),
        })
    return {"total": len(plantillas), "plantillas": plantillas}


@mcp.tool()
def wsp_metricas_conversaciones(periodo: str = "ultimos_30d") -> dict:
    """Devuelve métricas de conversaciones de WhatsApp Business en un período.

    Args:
        periodo: uno de hoy, ayer, ultimos_7d, ultimos_14d, ultimos_30d, este_mes, mes_anterior.
    """
    err = _meta_check_waba()
    if err:
        return err
    since, until = _resolver_periodo(periodo)
    # La Graph API espera epoch en segundos para conversation_analytics
    import time as _time
    from datetime import datetime as _dt
    start_ts = int(_dt.fromisoformat(since).timestamp())
    end_ts = int(_dt.fromisoformat(until).timestamp()) + 86399  # fin del día
    data = _meta_get(
        META_WABA_ID,
        {
            "fields": (
                f"conversation_analytics.start({start_ts}).end({end_ts})"
                ".granularity(DAILY).dimensions([\"CONVERSATION_CATEGORY\",\"CONVERSATION_TYPE\"])"
            ),
        },
    )
    if "error" in data:
        return data
    ca = (data.get("conversation_analytics") or {}).get("data") or []
    if not ca:
        return {"periodo": periodo, "desde": since, "hasta": until, "mensaje": "sin datos en ese período"}
    total_conv = 0
    total_costo = 0.0
    puntos = []
    for bloque in ca:
        for p in bloque.get("data_points", []):
            conv = int(p.get("conversation") or 0)
            cost = float(p.get("cost") or 0)
            total_conv += conv
            total_costo += cost
            puntos.append({
                "inicio": p.get("start"),
                "fin": p.get("end"),
                "conversaciones": conv,
                "costo": cost,
                "categoria": p.get("conversation_category"),
                "tipo": p.get("conversation_type"),
            })
    return {
        "periodo": periodo,
        "desde": since,
        "hasta": until,
        "total_conversaciones": total_conv,
        "costo_total": round(total_costo, 4),
        "detalle": puntos[:50],
    }


@mcp.tool()
def wsp_enviar_plantilla(numero_destino: str, plantilla: str, idioma: str = "es") -> dict:
    """Envía un mensaje basado en plantilla aprobada por WhatsApp Business.

    Args:
        numero_destino: número en formato internacional sin +, ej "56912345678".
        plantilla: nombre exacto de una plantilla APROBADA.
        idioma: código de idioma de la plantilla (ej "es", "es_CL", "en_US"). Por defecto "es".
    """
    err = _meta_check_phone()
    if err:
        return err
    destino = (numero_destino or "").strip().replace("+", "").replace(" ", "").replace("-", "")
    if not destino.isdigit() or len(destino) < 8:
        return {"error": "numero_destino inválido (usa formato internacional sin +)"}
    if not plantilla:
        return {"error": "falta el nombre de la plantilla"}
    payload = {
        "messaging_product": "whatsapp",
        "to": destino,
        "type": "template",
        "template": {"name": plantilla, "language": {"code": idioma}},
    }
    data = _meta_post(f"{META_WABA_PHONE_ID}/messages", payload)
    if "error" in data:
        return data
    msgs = data.get("messages") or []
    return {
        "ok": True,
        "destino": destino,
        "plantilla": plantilla,
        "idioma": idioma,
        "wa_id": (data.get("contacts") or [{}])[0].get("wa_id"),
        "message_id": msgs[0].get("id") if msgs else None,
    }


# ---------- Tools Facebook Page (lectura) ----------
@mcp.tool()
def fb_info_pagina() -> dict:
    """Devuelve datos básicos de la página de Facebook conectada
    (nombre, categoría, fans, seguidores, sitio web)."""
    err = _meta_check_page()
    if err:
        return err
    data = _meta_get(
        META_FB_PAGE_ID,
        {"fields": "name,category,fan_count,followers_count,about,website,link,verification_status"},
    )
    if "error" in data:
        return data
    return {
        "id": data.get("id") or META_FB_PAGE_ID,
        "nombre": data.get("name"),
        "categoria": data.get("category"),
        "fans": data.get("fan_count"),
        "seguidores": data.get("followers_count"),
        "descripcion": data.get("about"),
        "sitio_web": data.get("website"),
        "url": data.get("link"),
        "verificacion": data.get("verification_status"),
    }


@mcp.tool()
def fb_publicaciones_recientes(limite: int = 5) -> dict:
    """Devuelve las publicaciones recientes de la página de Facebook.

    Args:
        limite: cuántas publicaciones devolver (1-25).
    """
    err = _meta_check_page()
    if err:
        return err
    limite = max(1, min(int(limite or 5), 25))
    data = _meta_get(
        f"{META_FB_PAGE_ID}/posts",
        {
            "fields": "id,message,created_time,permalink_url,reactions.summary(true),comments.summary(true),shares",
            "limit": limite,
        },
    )
    if "error" in data:
        return data
    posts = []
    for p in data.get("data", []):
        reactions = ((p.get("reactions") or {}).get("summary") or {}).get("total_count")
        comments = ((p.get("comments") or {}).get("summary") or {}).get("total_count")
        shares = (p.get("shares") or {}).get("count")
        posts.append({
            "id": p.get("id"),
            "fecha": p.get("created_time"),
            "texto": (p.get("message") or "")[:280],
            "url": p.get("permalink_url"),
            "reacciones": reactions,
            "comentarios": comments,
            "compartidos": shares,
        })
    return {"total": len(posts), "publicaciones": posts}


# A medida que sumes integraciones (GA4, GMB, Make, Reservo, Google Places)
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


async def health(_):
    return JSONResponse({"status": "healthy"})


async def root(_):
    return JSONResponse({"status": "ok", "service": "bilbao-render-stack"})


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
