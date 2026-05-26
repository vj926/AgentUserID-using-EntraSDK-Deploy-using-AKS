"""
auid_flow.py — pure-Python implementation of the AUID FIC token chain.
Lifted from Connect_3P_agent_to_AgentID_using_HTTPs README steps 03.01–03.04.
"""
from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict

import httpx


@dataclass
class AuidConfig:
    tenant_id: str
    blueprint_app_id: str
    agent_identity_app_id: str
    blueprint_client_secret: str
    agent_user_upn: str

    @property
    def token_url(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token"

    @classmethod
    def from_env(cls) -> "AuidConfig":
        required = [
            "TENANT_ID", "BLUEPRINT_APP_ID", "AGENT_IDENTITY_APP_ID",
            "BLUEPRINT_CLIENT_SECRET", "AGENT_USER_UPN",
        ]
        missing = [k for k in required if not os.getenv(k)]
        if missing:
            raise RuntimeError(f"Missing env vars: {missing}")
        return cls(
            tenant_id=os.environ["TENANT_ID"],
            blueprint_app_id=os.environ["BLUEPRINT_APP_ID"],
            agent_identity_app_id=os.environ["AGENT_IDENTITY_APP_ID"],
            blueprint_client_secret=os.environ["BLUEPRINT_CLIENT_SECRET"],
            agent_user_upn=os.environ["AGENT_USER_UPN"],
        )


@dataclass
class StepResult:
    step: str
    description: str
    request: Dict[str, Any]
    response: Dict[str, Any]
    token: str | None = None
    claims: Dict[str, Any] = field(default_factory=dict)


def _decode_jwt_claims(jwt: str) -> Dict[str, Any]:
    parts = jwt.split(".")
    if len(parts) < 2:
        return {}
    pad = parts[1] + "=" * (-len(parts[1]) % 4)
    raw = base64.urlsafe_b64decode(pad.encode("ascii"))
    return json.loads(raw.decode("utf-8"))


def _safe_response(resp: httpx.Response) -> Dict[str, Any]:
    try:
        data = resp.json()
    except Exception:
        data = {"_raw": resp.text}
    if isinstance(data, dict) and "access_token" in data:
        # Don't dump full token in the response inspector view
        tok = data["access_token"]
        data = {**data, "access_token": f"{tok[:20]}...({len(tok)} chars)"}
    return {"status": resp.status_code, "body": data}


async def step_blueprint_fic(cfg: AuidConfig) -> StepResult:
    """Recipe 03.01: Blueprint app authenticates with secret + fmi_path = AgentID."""
    basic = base64.b64encode(f"{cfg.blueprint_app_id}:{cfg.blueprint_client_secret}".encode()).decode()
    headers = {"Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"}
    body = {
        "scope": "api://AzureADTokenExchange/.default",
        "grant_type": "client_credentials",
        "fmi_path": cfg.agent_identity_app_id,
    }
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(cfg.token_url, headers=headers, data=body)
    r.raise_for_status()
    tok = r.json()["access_token"]
    return StepResult(
        step="03.01",
        description="Blueprint FIC token (client_credentials + fmi_path = AgentID)",
        request={"url": cfg.token_url, "headers": {"Authorization": "Basic <redacted>"}, "body": body},
        response=_safe_response(r),
        token=tok,
        claims=_decode_jwt_claims(tok),
    )


async def step_agent_id_fic(cfg: AuidConfig, blueprint_fic: str) -> StepResult:
    """Recipe 03.02: Agent ID FIC, using Blueprint FIC as client_assertion."""
    body = {
        "client_id": cfg.agent_identity_app_id,
        "scope": "api://AzureADTokenExchange/.default",
        "grant_type": "client_credentials",
        "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
        "client_assertion": blueprint_fic,
    }
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(cfg.token_url, data=body)
    r.raise_for_status()
    tok = r.json()["access_token"]
    safe_body = {**body, "client_assertion": f"{blueprint_fic[:20]}...({len(blueprint_fic)} chars)"}
    return StepResult(
        step="03.02",
        description="Agent ID FIC token (jwt-bearer with Blueprint FIC as assertion)",
        request={"url": cfg.token_url, "body": safe_body},
        response=_safe_response(r),
        token=tok,
        claims=_decode_jwt_claims(tok),
    )


async def step_agentic_user_token(
    cfg: AuidConfig,
    blueprint_fic: str,
    agent_id_fic: str,
    scope: str = "https://graph.microsoft.com/.default",
) -> StepResult:
    """Recipe 03.03: Agentic User access token via grant_type=user_fic (multipart)."""
    fields = {
        "client_id": cfg.agent_identity_app_id,
        "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
        "client_assertion": blueprint_fic,
        "grant_type": "user_fic",
        "requested_token_use": "on_behalf_of",
        "scope": scope,
        "username": cfg.agent_user_upn,
        "user_federated_identity_credential": agent_id_fic,
    }
    files = {k: (None, v) for k, v in fields.items()}
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(cfg.token_url, files=files)
    r.raise_for_status()
    tok = r.json()["access_token"]
    safe_fields = {
        **fields,
        "client_assertion": f"{blueprint_fic[:20]}...({len(blueprint_fic)} chars)",
        "user_federated_identity_credential": f"{agent_id_fic[:20]}...({len(agent_id_fic)} chars)",
    }
    return StepResult(
        step="03.03",
        description="Agentic User access token (grant_type=user_fic, multipart)",
        request={"url": cfg.token_url, "form": safe_fields},
        response=_safe_response(r),
        token=tok,
        claims=_decode_jwt_claims(tok),
    )


async def call_graph_me(auid_token: str) -> StepResult:
    """Recipe 03.04: GET /me as the Agentic User."""
    url = "https://graph.microsoft.com/v1.0/me"
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(url, headers={"Authorization": f"Bearer {auid_token}"})
    return StepResult(
        step="03.04",
        description="Call Graph /me as Agentic User",
        request={"url": url, "headers": {"Authorization": "Bearer <AUID>"}},
        response=_safe_response(r),
    )


async def full_chain(cfg: AuidConfig) -> Dict[str, StepResult]:
    s1 = await step_blueprint_fic(cfg)
    s2 = await step_agent_id_fic(cfg, s1.token)
    s3 = await step_agentic_user_token(cfg, s1.token, s2.token)
    s4 = await call_graph_me(s3.token)
    return {"03.01": s1, "03.02": s2, "03.03": s3, "03.04": s4}
