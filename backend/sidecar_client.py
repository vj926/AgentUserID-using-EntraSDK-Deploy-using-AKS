"""
sidecar_client.py — Thin async wrapper around the Microsoft Entra SDK
auth-sidecar HTTP API (mcr.microsoft.com/entra-sdk/auth-sidecar).

The sidecar runs in the SAME pod as this backend, on localhost:5000. It
holds the Blueprint credential (Workload Identity → SignedAssertionFilePath)
and exposes:

  GET /AuthorizationHeaderUnauthenticated/<svc>
        ?AgentIdentity=<AGENT_APP_ID>
        &AgentUsername=<UPN>          # AUID flow (grant_type=user_fic)
        # or &AgentUserId=<oid>
        # omit both for autonomous (app-only) flow

  GET /AuthorizationHeader/<svc>      # OBO with incoming user token (Bearer Tc)
        ?AgentIdentity=<AGENT_APP_ID>

This wrapper exposes only the AUID method — the whole purpose of this repo.
The token chain (Blueprint FIC → Agent ID FIC → user_fic) is performed
INSIDE the sidecar; this code never touches login.microsoftonline.com.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

import httpx


@dataclass
class SidecarClient:
    base_url: str = "http://localhost:5000"
    timeout: float = 30.0

    @classmethod
    def from_env(cls) -> "SidecarClient":
        return cls(base_url=os.getenv("SIDECAR_URL", "http://localhost:5000"))

    async def get_auid_header(
        self,
        service_name: str,
        agent_identity: str,
        agent_username: Optional[str] = None,
        agent_user_oid: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Acquire an AUID Authorization header for the given downstream service.

        Returns a dict including the bearer header AND a redacted view of the
        request — callers can show this in a UI panel to demonstrate "the SDK
        does the user_fic chain for me".
        """
        if not (agent_username or agent_user_oid):
            raise ValueError("AUID requires agent_username (UPN) or agent_user_oid")

        url = f"{self.base_url}/AuthorizationHeaderUnauthenticated/{service_name}"
        params: Dict[str, str] = {"AgentIdentity": agent_identity}
        if agent_username:
            params["AgentUsername"] = agent_username
        if agent_user_oid:
            params["AgentUserId"] = agent_user_oid

        async with httpx.AsyncClient(timeout=self.timeout) as c:
            r = await c.get(url, params=params)

        # Surface non-2xx responses with the sidecar's error body for debugging.
        if r.status_code >= 400:
            try:
                err_body = r.json()
            except Exception:
                err_body = {"_raw": r.text}
            return {
                "ok": False,
                "request": {"url": url, "params": params},
                "response": {"status": r.status_code, "body": err_body},
            }

        body = r.json()
        header = body.get("authorizationHeader", "")
        token = header.split(" ", 1)[1] if header.startswith("Bearer ") else None
        return {
            "ok": True,
            "request": {"url": url, "params": params},
            "response": {
                "status": r.status_code,
                "authorization_header_preview": (
                    f"Bearer {token[:24]}...({len(token)} chars)" if token else header
                ),
            },
            "authorization_header": header,
            "token": token,
        }

    async def health(self) -> bool:
        """Best-effort health probe. The sidecar always responds to GET / with 404,
        which is fine — we only need to know the listener is reachable."""
        try:
            async with httpx.AsyncClient(timeout=2.0) as c:
                r = await c.get(f"{self.base_url}/")
            return r.status_code < 500
        except Exception:
            return False
