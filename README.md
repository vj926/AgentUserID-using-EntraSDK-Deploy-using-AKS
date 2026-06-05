# AUID on AKS — using the Microsoft Entra SDK auth-sidecar

Reference deployment of the **Agent User Identity (AUID)** flow
(`grant_type=user_fic`) on **Azure Kubernetes Service**, using the
Microsoft-published **`mcr.microsoft.com/entra-sdk/auth-sidecar`** image and
**Azure Workload Identity**.

This is the AUID counterpart to [`vj926/AgentID-using-EntraSDK_AKS`](https://github.com/vj926/AgentID-using-EntraSDK_AKS):
same identity pattern (KSA → FIC → Blueprint via auth-sidecar), specialized
for an agent that acts **as a designated Agentic User** rather than as itself
or on behalf of an interactive sign-in.

## What changed vs the previous revision

The earlier revision of this repo hand-rolled the AUID token chain in Python
(`backend/auid_flow.py` posting to `login.microsoftonline.com` directly with
a `BLUEPRINT_CLIENT_SECRET`) and skipped JWT signature verification in the
Weather Agent. **That has been replaced** with the SDK pattern:

| Before | Now |
|---|---|
| `httpx` POSTing to `oauth2/v2.0/token` from app code | Auth-sidecar in same pod (`localhost:5000`) |
| `BLUEPRINT_CLIENT_SECRET` env var | **No secret** — Workload Identity → `SignedAssertionFilePath` |
| Hand-implemented FIC chain (03.01 → 03.04) | One sidecar GET; SDK chains internally |
| Weather Agent skipped signature verification | Full JWKS signature + claim validation against the Weather Agent's own audience |
| No deployment scripts | `deploy/aks/scripts/deploy-aks-dev.sh` orchestrator |

## Repository layout

```
.
├── backend/                    FastAPI broker. Calls the sidecar.
│   ├── app.py                  4-step demo + /api/call-weather
│   ├── sidecar_client.py       Thin wrapper around /AuthorizationHeaderUnauthenticated
│   └── Dockerfile
├── weather-agent/              Downstream API. Validates AUID JWTs (signature + claims).
│   ├── app.py
│   └── Dockerfile
├── ui/                         Static demo page + nginx that proxies /api/* → backend.
│   ├── index.html
│   ├── nginx.conf
│   └── Dockerfile
├── deploy/aks/
│   ├── manifests/              00-namespace, 10-serviceaccount, 20-weather-agent,
│   │                           30-ui (LB), 40-backend (with sidecar container)
│   └── scripts/                deploy-vars.sh.template, 01–04 scripts, deploy-aks-dev.sh
└── scripts/                    PowerShell helpers (Phase 1 / one-time Entra setup)
    ├── 00-preflight-check.ps1
    ├── 01-provision-agentic-user.ps1
    ├── 02-grant-agentic-user-consent.ps1
    └── 04-register-weather-app.ps1   ← NEW: required for proper signature validation
```

## Identity chain on AKS

```
ServiceAccount  auid/backend-sa
        │  (Workload Identity webhook projects an SA token at
        │   /var/run/secrets/azure/tokens/azure-identity-token)
        ▼
FIC on Blueprint app   (subject  = system:serviceaccount:auid:backend-sa,
                        audience = api://AzureADTokenExchange)
        │
        ▼
Auth sidecar (localhost:5000)
   reads the SA token via SignedAssertionFilePath, runs the
   user_fic grant against api://<weather-agent>/.default
        │
        ▼
Backend container gets a fully-formed `Authorization: Bearer <AUID>` header
        │
        ▼
Weather Agent (separate Entra app) verifies signature against tenant JWKS,
checks aud / appid / idtyp=user / upn, then serves the request.
```

## Two-phase setup

### Phase 1 — one-time Entra objects (run from your laptop)

You need (a) a Blueprint + Agent Identity (b) an Agentic User and (c) a
Weather Agent app registration. The first two come from the upstream
[`entra-agent-id-setup`](https://github.com/microsoft/entra-agentid-samples/tree/main/.claude/skills/entra-agent-id-setup) skill.

```powershell
# Already had Blueprint + Agent Identity? Skip to step (b).

# (a) Provision the Agentic User (regular cloud-only user, mail-nickname configurable):
pwsh ./scripts/01-provision-agentic-user.ps1 -TenantId <tid> -BlueprintAppId <bp> -AgentIdentityAppId <agent>

# (b) Grant the Agent Identity → Agentic User consent (delegated):
pwsh ./scripts/02-grant-agentic-user-consent.ps1 -TenantId <tid> -AgentIdentityAppId <agent>

# (c) NEW — register the Weather Agent and grant Agent Identity admin consent
#     for the Weather.Read scope. Required so the AUID JWT signature is verifiable:
pwsh ./scripts/04-register-weather-app.ps1 -TenantId <tid> -AgentIdentityAppId <agent>
# Copy WEATHER_AGENT_APP_ID + WEATHER_AGENT_APP_ID_URI from the script's output.
```

### Phase 2 — deploy to AKS

```bash
cp deploy/aks/scripts/deploy-vars.sh.template /tmp/deploy-vars.sh
# Edit /tmp/deploy-vars.sh — fill in TENANT_ID, SUBSCRIPTION_ID, RG, AKS_NAME,
# ACR_NAME, BLUEPRINT_APP_ID, AGENT_IDENTITY_APP_ID, AGENT_USER_UPN,
# WEATHER_AGENT_APP_ID, WEATHER_AGENT_APP_ID_URI.

source /tmp/deploy-vars.sh
az login --tenant "${SUBSCRIPTION_TENANT_ID:-$TENANT_ID}"
az account set --subscription "$SUBSCRIPTION_ID"

bash deploy/aks/scripts/deploy-aks-dev.sh
```

The orchestrator does:

1. `01-create-aks.sh` — RG + ACR + AKS (OIDC issuer + Workload Identity on, attach-acr).
2. `02-build-and-push.sh` — `az acr build` for backend, weather-agent, ui.
3. `03-federate-blueprint.ps1` — adds the FIC `system:serviceaccount:auid:backend-sa` to the Blueprint app.
4. `04-apply-manifests.sh` — `envsubst` + `kubectl apply` for namespace, KSA, weather-agent, ui (LB), backend (with sidecar).

When the LB IP is assigned, open `http://<lb-ip>/` and click through the
4-step demo. Step 3 calls the sidecar; step 4 hits the Weather Agent and
shows the validated AUID claims.

## Local dev (no AKS) — caveats

The auth-sidecar requires a credential source. In AKS that's
`SignedAssertionFilePath` fed by Workload Identity. **Local docker-compose
is not supported in this branch** — running the sidecar locally would
require a `BLUEPRINT_CLIENT_SECRET`, which is exactly the secret-in-the-app
pattern this migration replaces. Use AKS (or `kind` with workload-identity
add-ons, out of scope here) to exercise the full flow.

The 4-step UI panel will still render outside AKS, but step 03 returns the
sidecar's connection error — that's expected.

## Smoke-test the AUID acquisition

```bash
kubectl exec -n auid deploy/backend -c backend -- \
  curl -s -X POST http://localhost:8080/api/step/03-auid-token | head -c 800
```

Expected: `"ok": true`, an `authorization_header_preview` like
`Bearer eyJ...` and the request showing `AgentIdentity` + `AgentUsername`.

```bash
kubectl logs -n auid -l app=backend -c sidecar --tail=80
```

Look for `Acquired token for downstream API 'weather'`. Sidecar errors here
typically mean the Weather Agent app wasn't registered with the right
scope, or admin consent for the Agent → Weather Agent grant is missing —
re-run `scripts/04-register-weather-app.ps1`.

## Status

- ✅ Auth-sidecar pattern wired end-to-end (Workload Identity → Blueprint FIC → sidecar → AUID).
- ✅ Weather Agent verifies JWT signatures against tenant JWKS (no more `get_unverified_claims`).
- ✅ Manifests + scripts mirror the autonomous-mode reference repo's structure.
- 🟡 Cross-tenant deploy supported by inheriting reference patterns; not yet validated in this fork.

## Acknowledgements

Pattern and scripts adapted from [`vj926/AgentID-using-EntraSDK_AKS`](https://github.com/vj926/AgentID-using-EntraSDK_AKS), which is in turn built on top of [`microsoft/entra-agentid-samples`](https://github.com/microsoft/entra-agentid-samples). Sidecar AUID query parameters (`AgentIdentity`, `AgentUsername`, `AgentUserId`) confirmed against `rido-min/spike-agentic-tokens` and the Microsoft Entra Agent ID public docs.
