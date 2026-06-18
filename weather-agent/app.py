"""
weather-agent/app.py — Downstream API that validates AUID bearer tokens
WITH FULL SIGNATURE VERIFICATION against the Entra v2 JWKS for its own
audience (the WEATHER_AGENT_APP_ID), then returns weather data.

This is the proper third-party API pattern. Because the AUID token is now
issued for `api://<this-app>` (not Graph), the signature is verifiable by
us — no Graph-style nonce-rehashing problem.

Claim contract enforced on every request:
  • signature  : valid against tenant JWKS, key matched by kid
  • iss        : https://login.microsoftonline.com/<tid>/v2.0
                 (or https://sts.windows.net/<tid>/ for v1 fallback)
  • tid        : == TENANT_ID
  • aud        : == WEATHER_AGENT_APP_ID  (or app id URI)
  • appid/azp  : == AGENT_IDENTITY_APP_ID (the agent making the call)
  • idtyp      : "user"   ← AUID hallmark
  • upn        : == expected agentic user (if AGENT_USER_UPN set)
  • exp        : not in the past
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, List

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from jose import jwt
from jose.exceptions import JWTError

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

TENANT_ID = os.environ["TENANT_ID"]
WEATHER_AGENT_APP_ID = os.environ["WEATHER_AGENT_APP_ID"]
WEATHER_AGENT_APP_ID_URI = os.getenv("WEATHER_AGENT_APP_ID_URI", "")
EXPECTED_AGENT_APP_ID = os.environ["AGENT_IDENTITY_APP_ID"]
EXPECTED_AGENT_USER_UPN = os.getenv("AGENT_USER_UPN", "").lower()

JWKS_URL = f"https://login.microsoftonline.com/{TENANT_ID}/discovery/v2.0/keys"
ISSUER_V2 = f"https://login.microsoftonline.com/{TENANT_ID}/v2.0"
ISSUER_V1 = f"https://sts.windows.net/{TENANT_ID}/"

ACCEPTED_AUDIENCES: List[str] = [WEATHER_AGENT_APP_ID]
if WEATHER_AGENT_APP_ID_URI:
    ACCEPTED_AUDIENCES.append(WEATHER_AGENT_APP_ID_URI)

app = FastAPI(title="Weather Agent (AUID-validating, signature-verifying)")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

_jwks_cache: Dict[str, Any] = {}
_jwks_fetched_at: float = 0.0
_JWKS_TTL = 3600.0


async def _jwks() -> Dict[str, Any]:
    global _jwks_fetched_at
    if not _jwks_cache or (time.time() - _jwks_fetched_at) > _JWKS_TTL:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(JWKS_URL)
            r.raise_for_status()
            _jwks_cache.clear()
            _jwks_cache.update(r.json())
            _jwks_fetched_at = time.time()
    return _jwks_cache


def _find_key(jwks: Dict[str, Any], kid: str) -> Dict[str, Any]:
    for k in jwks.get("keys", []):
        if k.get("kid") == kid:
            return k
    raise HTTPException(401, f"Signing key kid={kid} not in JWKS")


async def _validate(token: str) -> Dict[str, Any]:
    # 1) Parse the header to find kid
    try:
        unverified_header = jwt.get_unverified_header(token)
    except JWTError as e:
        raise HTTPException(401, f"Token header parse failed: {e}")
    kid = unverified_header.get("kid")
    if not kid:
        raise HTTPException(401, "Token header missing kid")

    # 2) Fetch JWKS and locate the signing key
    jwks = await _jwks()
    key = _find_key(jwks, kid)

    # 3) Verify signature + standard claims (iss, aud, exp). Accept v1 or v2 issuer.
    last_error: Exception | None = None
    claims: Dict[str, Any] | None = None
    for issuer in (ISSUER_V2, ISSUER_V1):
        try:
            claims = jwt.decode(
                token,
                key,
                algorithms=[key.get("alg", "RS256")],
                audience=ACCEPTED_AUDIENCES,
                issuer=issuer,
                options={"verify_at_hash": False},
            )
            break
        except JWTError as e:
            last_error = e
            claims = None
    if claims is None:
        raise HTTPException(401, f"Signature/claim validation failed: {last_error}")

    # 4) AUID-specific claim checks (not covered by jose.decode)
    if claims.get("tid") != TENANT_ID:
        raise HTTPException(401, f"Unexpected tid {claims.get('tid')}")
    appid = claims.get("appid") or claims.get("azp")
    if appid != EXPECTED_AGENT_APP_ID:
        raise HTTPException(
            403, f"Unexpected appid/azp {appid}; expected {EXPECTED_AGENT_APP_ID}"
        )
    if claims.get("idtyp") != "user":
        raise HTTPException(
            403, f"Expected idtyp=user (AUID hallmark), got {claims.get('idtyp')}"
        )
    if EXPECTED_AGENT_USER_UPN:
        token_upn = str(claims.get("upn", "")).lower()
        if token_upn != EXPECTED_AGENT_USER_UPN:
            raise HTTPException(
                403,
                f"Unexpected Agentic User upn {token_upn}; expected {EXPECTED_AGENT_USER_UPN}",
            )
    if claims.get("exp", 0) < int(time.time()):
        raise HTTPException(401, "Token expired")
    return claims


def _bearer(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Missing Bearer token")
    return authorization.split(" ", 1)[1]


@app.get("/health")
async def health():
    return {
        "ok": True,
        "expected_agent_app_id": EXPECTED_AGENT_APP_ID,
        "expected_audiences": ACCEPTED_AUDIENCES,
        "issuer_v2": ISSUER_V2,
        "jwks_url": JWKS_URL,
    }


@app.get("/whoami")
async def whoami(authorization: str | None = Header(None)):
    """Echo back the validated AUID claims. Useful for the demo's step 04."""
    claims = await _validate(_bearer(authorization))
    return {
        "auth": _auth_summary(claims),
        "note": (
            "Signature verified against tenant JWKS. Audience matches this API. "
            "idtyp=user + appid=<agent> confirms an AUID (not app-only, not a human)."
        ),
    }


@app.get("/weather")
async def weather(
    city: str = Query("Dallas"),
    authorization: str | None = Header(None),
):
    claims = await _validate(_bearer(authorization))

    geocode_url = "https://geocoding-api.open-meteo.com/v1/search"
    async with httpx.AsyncClient(timeout=15) as c:
        g = (await c.get(geocode_url, params={"name": city, "count": 1})).json()
        if not g.get("results"):
            raise HTTPException(404, f"City '{city}' not found")
        lat, lon = g["results"][0]["latitude"], g["results"][0]["longitude"]
        w = (await c.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat,
                "longitude": lon,
                "current": "temperature_2m,relative_humidity_2m,wind_speed_10m,weather_code",
            },
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
        "auth": _auth_summary(claims),
        "note": (
            "AUID token verified with FULL signature check against tenant JWKS — "
            "the token's audience is this Weather Agent's app registration, not Graph."
        ),
    }


def _auth_summary(claims: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "flow": "AUID (Agent ID User) — grant_type=user_fic via sidecar",
        "agentic_user_upn": claims.get("upn"),
        "agentic_user_oid": claims.get("oid"),
        "agent_identity_app_id": claims.get("appid") or claims.get("azp"),
        "tenant_id": claims.get("tid"),
        "idtyp": claims.get("idtyp"),
        "iss": claims.get("iss"),
        "aud": claims.get("aud"),
        "scopes": claims.get("scp"),
        "validated_with": "signature (JWKS) + iss + aud + tid + appid + idtyp + upn",
        "jwks_url": JWKS_URL,
    }


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("WEATHER_AGENT_PORT", "7200"))
    uvicorn.run(app, host="0.0.0.0", port=port)
