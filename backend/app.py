"""
backend/app.py — FastAPI broker that walks the AUID token chain and calls the Weather Agent.
"""
from __future__ import annotations

import os
from dataclasses import asdict
from typing import Dict

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from auid_flow import (
    AuidConfig, full_chain,
    step_blueprint_fic, step_agent_id_fic, step_agentic_user_token, call_graph_me,
)

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

app = FastAPI(title="AUID Experiment — Token Broker")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

WEATHER_AGENT_URL = os.getenv("WEATHER_AGENT_URL", f"http://localhost:{os.getenv('WEATHER_AGENT_PORT', '7200')}")


def _cfg() -> AuidConfig:
    try:
        return AuidConfig.from_env()
    except RuntimeError as e:
        raise HTTPException(500, str(e))


def _step_to_dict(s) -> Dict:
    return {
        "step": s.step,
        "description": s.description,
        "request": s.request,
        "response": s.response,
        "claims": s.claims,
        "token_preview": (s.token[:30] + f"...({len(s.token)} chars)") if s.token else None,
    }


@app.get("/api/health")
async def health():
    weather_url = os.getenv("WEATHER_AGENT_URL", "http://localhost:7200")
    weather_status = "offline"
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            r = await client.get(f"{weather_url}/health")
            if r.status_code == 200:
                weather_status = "online"
    except Exception:
        pass
    return {
        "ok": True,
        "config_loaded": bool(os.getenv("TENANT_ID")),
        "weather_agent": weather_status,
    }


@app.get("/api/config")
async def config():
    cfg = _cfg()
    return {
        "tenant_id": cfg.tenant_id,
        "blueprint_app_id": cfg.blueprint_app_id,
        "agent_identity_app_id": cfg.agent_identity_app_id,
        "agent_user_upn": cfg.agent_user_upn,
        "weather_agent_url": WEATHER_AGENT_URL,
    }


@app.post("/api/step/01-blueprint-fic")
async def s1():
    return _step_to_dict(await step_blueprint_fic(_cfg()))


@app.post("/api/step/02-agentid-fic")
async def s2():
    cfg = _cfg()
    bp = await step_blueprint_fic(cfg)
    res = await step_agent_id_fic(cfg, bp.token)
    return _step_to_dict(res)


@app.post("/api/step/03-auid-token")
async def s3():
    cfg = _cfg()
    bp = await step_blueprint_fic(cfg)
    ag = await step_agent_id_fic(cfg, bp.token)
    res = await step_agentic_user_token(cfg, bp.token, ag.token)
    # Return the full AUID token so the UI can pass it to the weather agent
    return {**_step_to_dict(res), "token": res.token}


@app.post("/api/step/04-call-me")
async def s4():
    cfg = _cfg()
    bp = await step_blueprint_fic(cfg)
    ag = await step_agent_id_fic(cfg, bp.token)
    auid = await step_agentic_user_token(cfg, bp.token, ag.token)
    res = await call_graph_me(auid.token)
    return _step_to_dict(res)


@app.get("/api/chain")
async def chain():
    """Walk the whole chain in one call (Graph /me at the end)."""
    cfg = _cfg()
    res = await full_chain(cfg)
    return {k: _step_to_dict(v) for k, v in res.items()}


@app.post("/api/call-weather")
async def call_weather(payload: dict):
    """Use AUID token to call the Weather Agent for a given city."""
    cfg = _cfg()
    city = payload.get("city", "Dallas")
    # Mint a fresh AUID token
    bp = await step_blueprint_fic(cfg)
    ag = await step_agent_id_fic(cfg, bp.token)
    auid = await step_agentic_user_token(cfg, bp.token, ag.token)
    # Call weather agent with Bearer = AUID token
    url = f"{WEATHER_AGENT_URL}/weather"
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(url, params={"city": city}, headers={"Authorization": f"Bearer {auid.token}"})
    return {
        "auid_step": _step_to_dict(auid),
        "weather_call": {
            "url": url,
            "params": {"city": city},
            "status": r.status_code,
            "body": r.json() if "application/json" in r.headers.get("content-type", "") else r.text,
        },
    }


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("BACKEND_PORT", "7100"))
    uvicorn.run(app, host="127.0.0.1", port=port)
