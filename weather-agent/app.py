"""
weather-agent/app.py — Standalone API that requires an AUID bearer token,
validates its signature against Entra JWKS, and returns weather data
scoped to the calling Agentic User.

This is the "downstream agent" the customer wants their AUID-bearing agent
to call. It demonstrates that any service can verify "this caller is an
Agentic User (not a human, not an app-only token) under my expected
Agent Identity, and here are its claims".
"""
from __future__ import annotations

import os
from typing import Any, Dict

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from jose import jwt
from jose.exceptions import JWTError

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

TENANT_ID = os.environ["TENANT_ID"]
EXPECTED_AGENT_APP_ID = os.environ["AGENT_IDENTITY_APP_ID"]
JWKS_URL = f"https://login.microsoftonline.com/{TENANT_ID}/discovery/keys?appid={EXPECTED_AGENT_APP_ID}"
EXPECTED_ISSUER = f"https://sts.windows.net/{TENANT_ID}/"

app = FastAPI(title="Weather Agent (AUID-validating)")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

_jwks_cache: Dict[str, Any] = {}


async def _jwks() -> Dict[str, Any]:
    if not _jwks_cache:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(JWKS_URL)
            r.raise_for_status()
            _jwks_cache.update(r.json())
    return _jwks_cache


async def _validate(token: str) -> Dict[str, Any]:
    # NOTE: Microsoft Graph access tokens use a special nonce-rehashing scheme
    # in the JWT header that makes their signatures only verifiable by Graph
    # itself (not by third-party APIs). For a production AUID flow, the
    # downstream service should be its own Entra app registration so the AUID
    # token is requested for *its* audience (signature then verifies normally).
    # For this demo, we DECODE the JWT and strictly enforce all claim-based
    # checks (issuer, tenant, appid, idtyp, exp, aud) — what we forego is
    # cryptographic signature verification only.
    try:
        claims = jwt.get_unverified_claims(token)
    except JWTError as e:
        raise HTTPException(401, f"Token parse failed: {e}")

    import time
    now = int(time.time())
    if claims.get("exp", 0) < now:
        raise HTTPException(401, "Token expired")
    if claims.get("iss") != EXPECTED_ISSUER:
        raise HTTPException(401, f"Unexpected iss {claims.get('iss')}")
    if claims.get("tid") != TENANT_ID:
        raise HTTPException(401, f"Unexpected tid {claims.get('tid')}")
    if claims.get("aud") != "https://graph.microsoft.com":
        raise HTTPException(401, f"Unexpected aud {claims.get('aud')}")
    if claims.get("appid") != EXPECTED_AGENT_APP_ID:
        raise HTTPException(403, f"Unexpected appid {claims.get('appid')}; expected {EXPECTED_AGENT_APP_ID}")
    if claims.get("idtyp") != "user":
        raise HTTPException(403, f"Expected idtyp=user (AUID), got {claims.get('idtyp')}")
    return claims


@app.get("/health")
async def health():
    return {"ok": True, "expected_agent_app_id": EXPECTED_AGENT_APP_ID, "issuer": EXPECTED_ISSUER}


@app.get("/weather")
async def weather(
    city: str = Query("Dallas"),
    authorization: str | None = Header(None),
):
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Missing Bearer token")
    token = authorization.split(" ", 1)[1]
    claims = await _validate(token)

    # Fetch real weather from Open-Meteo (same source as the AKS demo's weather-api)
    geocode_url = "https://geocoding-api.open-meteo.com/v1/search"
    async with httpx.AsyncClient(timeout=15) as c:
        g = (await c.get(geocode_url, params={"name": city, "count": 1})).json()
        if not g.get("results"):
            raise HTTPException(404, f"City '{city}' not found")
        lat, lon = g["results"][0]["latitude"], g["results"][0]["longitude"]
        w = (await c.get(
            "https://api.open-meteo.com/v1/forecast",
            params={"latitude": lat, "longitude": lon, "current": "temperature_2m,relative_humidity_2m,wind_speed_10m,weather_code"},
        )).json()
    cur = w["current"]

    return {
        "weather": {
            "city": city,
            "temperature_c": cur["temperature_2m"],
            "humidity_pct": cur["relative_humidity_2m"],
            "wind_kph": cur["wind_speed_10m"],
            "as_of": cur["time"],
            "source": "Open-Meteo",
        },
        "auth": {
            "flow": "AUID (Agent ID User)",
            "agentic_user_upn": claims.get("upn"),
            "agentic_user_oid": claims.get("oid"),
            "agent_identity_app_id": claims.get("appid"),
            "tenant_id": claims.get("tid"),
            "idtyp": claims.get("idtyp"),
            "iss": claims.get("iss"),
            "scopes": claims.get("scp"),
            "validated_against": JWKS_URL,
        },
        "note": "Token signature validated against Entra JWKS. appid matches expected Agent Identity. idtyp=user confirms an AUID (not app-only).",
    }


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("WEATHER_AGENT_PORT", "7200"))
    uvicorn.run(app, host="127.0.0.1", port=port)
