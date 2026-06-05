"""
backend/app.py — FastAPI broker that demonstrates AUID acquisition via the
Microsoft Entra SDK auth-sidecar (mcr.microsoft.com/entra-sdk/auth-sidecar),
then calls a downstream Weather Agent with the resulting AUID bearer.

Identity model on AKS:

   Kubernetes ServiceAccount  auid/backend-sa
              │  (Workload Identity webhook projects an SA token at
              │   /var/run/secrets/azure/tokens/azure-identity-token)
              ▼
   FIC on Blueprint app (audience api://AzureADTokenExchange,
                         subject system:serviceaccount:auid:backend-sa)
              │
              ▼
   Auth sidecar (localhost:5000) holds the Blueprint credential as
   SignedAssertionFilePath → uses it to run the user_fic grant for
   the configured AGENT_USER_UPN against the WEATHER_AGENT scope.

This app NEVER talks to login.microsoftonline.com directly. It only calls
http://localhost:5000/AuthorizationHeaderUnauthenticated/<svc> with
AgentIdentity + AgentUsername.
"""
from __future__ import annotations

import os
from typing import Any, Dict

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from sidecar_client import SidecarClient

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

TENANT_ID = os.getenv("TENANT_ID", "")
BLUEPRINT_APP_ID = os.getenv("BLUEPRINT_APP_ID", "")
AGENT_IDENTITY_APP_ID = os.getenv("AGENT_IDENTITY_APP_ID", "")
AGENT_USER_UPN = os.getenv("AGENT_USER_UPN", "")
AGENT_USER_OBJECT_ID = os.getenv("AGENT_USER_OBJECT_ID", "")
WEATHER_AGENT_APP_ID = os.getenv("WEATHER_AGENT_APP_ID", "")
WEATHER_AGENT_APP_ID_URI = os.getenv("WEATHER_AGENT_APP_ID_URI", "")
WEATHER_AGENT_SCOPE = os.getenv("WEATHER_AGENT_SCOPE", "Weather.Read")
WEATHER_AGENT_URL = os.getenv(
    "WEATHER_AGENT_URL", f"http://localhost:{os.getenv('WEATHER_AGENT_PORT', '7200')}"
)

# Name MUST match the DownstreamApis__<name>__BaseUrl key in the sidecar env.
WEATHER_SVC_NAME = os.getenv("WEATHER_SVC_NAME", "weather")

app = FastAPI(title="AUID-on-AKS — SDK Broker (auth-sidecar)")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

sidecar = SidecarClient.from_env()


def _require_env() -> None:
    missing = [
        k for k in (
            "TENANT_ID", "BLUEPRINT_APP_ID", "AGENT_IDENTITY_APP_ID", "AGENT_USER_UPN",
        ) if not os.getenv(k)
    ]
    if missing:
        raise HTTPException(500, f"Missing env vars: {missing}")


@app.get("/api/health")
async def health():
    weather_status = "offline"
    try:
        async with httpx.AsyncClient(timeout=2.0) as c:
            r = await c.get(f"{WEATHER_AGENT_URL}/health")
            if r.status_code == 200:
                weather_status = "online"
    except Exception:
        pass
    return {
        "ok": True,
        "config_loaded": bool(TENANT_ID and BLUEPRINT_APP_ID and AGENT_IDENTITY_APP_ID),
        "sidecar_reachable": await sidecar.health(),
        "sidecar_url": sidecar.base_url,
        "weather_agent": weather_status,
    }


@app.get("/api/config")
async def config():
    _require_env()
    return {
        "tenant_id": TENANT_ID,
        "blueprint_app_id": BLUEPRINT_APP_ID,
        "agent_identity_app_id": AGENT_IDENTITY_APP_ID,
        "agent_user_upn": AGENT_USER_UPN,
        "weather_agent_app_id": WEATHER_AGENT_APP_ID,
        "weather_agent_url": WEATHER_AGENT_URL,
        "sidecar_url": sidecar.base_url,
        "downstream_svc_name": WEATHER_SVC_NAME,
        "downstream_scope": (
            f"{WEATHER_AGENT_APP_ID_URI}/.default" if WEATHER_AGENT_APP_ID_URI else None
        ),
    }


# ---------------------------------------------------------------------------
# The 4 demo steps. With the SDK + sidecar, the app no longer sees the
# Blueprint FIC → Agent ID FIC → user_fic hops directly — they happen INSIDE
# the sidecar. These endpoints narrate the equivalent SDK actions so the
# UI can still show a 4-step story.
# ---------------------------------------------------------------------------


@app.post("/api/step/01-blueprint-fic")
async def step1_workload_identity():
    """Step 1 — Workload Identity badge: K8s mounts a signed SA token at
    /var/run/secrets/azure/tokens/azure-identity-token. The sidecar reads it
    via SignedAssertionFilePath and uses it as the Blueprint client credential."""
    _require_env()
    token_path = "/var/run/secrets/azure/tokens/azure-identity-token"
    badge_present = os.path.isfile(token_path)
    badge_size = os.path.getsize(token_path) if badge_present else 0
    return {
        "step": "01",
        "description": "AKS Workload Identity → Blueprint credential (no secret)",
        "request": {"reads_file": token_path},
        "response": {
            "badge_mounted": badge_present,
            "badge_size_bytes": badge_size,
            "blueprint_app_id": BLUEPRINT_APP_ID,
            "subject": f"system:serviceaccount:{os.getenv('POD_NAMESPACE', 'auid')}:"
                       f"{os.getenv('SERVICE_ACCOUNT', 'backend-sa')}",
            "federated_to": "Blueprint app (audience api://AzureADTokenExchange)",
        },
        "note": "Outside AKS this file won't exist — that's expected for local dev.",
    }


@app.post("/api/step/02-agentid-fic")
async def step2_sidecar_config():
    """Step 2 — Auth-sidecar configured: the sidecar knows the Blueprint
    client id + downstream API definitions. We don't see the per-hop tokens;
    we trust the SDK to chain Blueprint FIC → Agent ID FIC internally."""
    _require_env()
    return {
        "step": "02",
        "description": "Auth-sidecar configuration (Blueprint + downstream APIs)",
        "request": {
            "sidecar_image": "mcr.microsoft.com/entra-sdk/auth-sidecar",
            "AzureAd__ClientId": BLUEPRINT_APP_ID,
            "AzureAd__ClientCredentials__0__SourceType": "SignedAssertionFilePath",
            f"DownstreamApis__{WEATHER_SVC_NAME}__BaseUrl": WEATHER_AGENT_URL,
            f"DownstreamApis__{WEATHER_SVC_NAME}__Scopes__0": (
                f"{WEATHER_AGENT_APP_ID_URI}/.default"
                if WEATHER_AGENT_APP_ID_URI else "(unset — set WEATHER_AGENT_APP_ID_URI)"
            ),
        },
        "response": {
            "sidecar_reachable": await sidecar.health(),
            "sidecar_url": sidecar.base_url,
        },
        "note": (
            "The sidecar performs the Blueprint→AgentID FIC exchange (~03.01/03.02 of "
            "the raw protocol) internally. The app never sees those intermediate tokens."
        ),
    }


@app.post("/api/step/03-auid-token")
async def step3_acquire_auid():
    """Step 3 — Acquire the AUID Authorization header from the sidecar.
    The sidecar runs grant_type=user_fic with AgentUsername=<UPN>."""
    _require_env()
    if not WEATHER_AGENT_APP_ID_URI:
        raise HTTPException(
            500,
            "WEATHER_AGENT_APP_ID_URI not set — register the weather-agent app and "
            "set its Application ID URI (e.g. api://<weather-agent-app-id>).",
        )
    result = await sidecar.get_auid_header(
        service_name=WEATHER_SVC_NAME,
        agent_identity=AGENT_IDENTITY_APP_ID,
        agent_username=AGENT_USER_UPN,
        agent_user_oid=AGENT_USER_OBJECT_ID or None,
    )
    return {
        "step": "03",
        "description": "AUID token acquisition via sidecar (grant_type=user_fic)",
        **result,
    }


@app.post("/api/step/04-call-me")
async def step4_call_weather_default():
    """Step 4 — Use the AUID header to call the Weather Agent's /me-equivalent.
    (We call /whoami which echoes the validated AUID claims back.)"""
    _require_env()
    res = await step3_acquire_auid()
    if not res.get("ok"):
        return {
            "step": "04",
            "description": "Call downstream as Agentic User",
            "skipped": "no token from step 03",
            "step03": res,
        }
    header = res["authorization_header"]
    url = f"{WEATHER_AGENT_URL}/whoami"
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(url, headers={"Authorization": header})
    body: Any
    try:
        body = r.json()
    except Exception:
        body = {"_raw": r.text}
    return {
        "step": "04",
        "description": "Call Weather Agent /whoami as Agentic User (no Graph involved)",
        "request": {"url": url, "headers": {"Authorization": "Bearer <AUID>"}},
        "response": {"status": r.status_code, "body": body},
    }


@app.get("/api/chain")
async def chain():
    """Walk the whole demo in one call."""
    s1 = await step1_workload_identity()
    s2 = await step2_sidecar_config()
    s3 = await step3_acquire_auid()
    s4 = await step4_call_weather_default()
    return {"01": s1, "02": s2, "03": s3, "04": s4}


@app.post("/api/call-weather")
async def call_weather(payload: dict):
    """Acquire AUID + call the Weather Agent /weather endpoint for a city."""
    _require_env()
    city = (payload or {}).get("city", "Dallas")
    sc = await sidecar.get_auid_header(
        service_name=WEATHER_SVC_NAME,
        agent_identity=AGENT_IDENTITY_APP_ID,
        agent_username=AGENT_USER_UPN,
        agent_user_oid=AGENT_USER_OBJECT_ID or None,
    )
    if not sc.get("ok"):
        return {"auid_step": sc, "weather_call": {"skipped": "no token"}}
    header = sc["authorization_header"]
    url = f"{WEATHER_AGENT_URL}/weather"
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(url, params={"city": city}, headers={"Authorization": header})
    body: Any
    try:
        body = r.json()
    except Exception:
        body = {"_raw": r.text}
    return {
        "auid_step": sc,
        "weather_call": {
            "url": url,
            "params": {"city": city},
            "status": r.status_code,
            "body": body,
        },
    }


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("BACKEND_PORT", "7100"))
    uvicorn.run(app, host="0.0.0.0", port=port)
