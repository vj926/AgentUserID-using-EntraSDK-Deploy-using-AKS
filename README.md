# AgentUserID using Entra SDK — AKS-ready local demo

End-to-end working sample for **Agent ID User (AUID)** on Microsoft Entra Agent ID. An *agent* mints its own user-shaped access token (`idtyp=user`, `@odata.type=#microsoft.graph.agentUser`) via the Blueprint + Agent Identity FIC chain — **with no human in the loop** — and calls a downstream service that validates the token as a first-class identity.

> **OBO vs AUID at a glance**
>
> | | OBO (the existing AKS demo) | **AUID (this repo)** |
> |--|--|--|
> | Caller identity | A human user who signed in | A **digital colleague** (Agentic User) |
> | Token `idtyp` | `user` (human) | `user` (Agentic User) |
> | Token `sub`/`oid` | Human user object | `microsoft.graph.agentUser` object |
> | Human in the loop? | Yes — MSAL sign-in | **No** — agent acts as itself |
> | Use case | Agent acts *on behalf of* a person | Agent is *its own* identity, owns artifacts, has its own permissions |

This repo deliberately mirrors the look-and-feel of the OBO AKS demo so customers can see the two patterns side by side.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│  Browser UI  (http://localhost:7001)                             │
│  ─ left: Powered by Microsoft Entra Agent ID                     │
│  ─ right: Agent Identity Flow (token chain trace)                │
└────────────────────────────┬─────────────────────────────────────┘
                             │ POST /api/call-weather
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│  backend/ FastAPI  (:7100) — the AUID **broker**                 │
│                                                                  │
│  03.01  Blueprint FIC                                            │
│         POST /oauth2/v2.0/token                                  │
│         Basic <BlueprintAppId:BlueprintSecret>                   │
│         client_credentials  fmi_path=<AgentID appId>             │
│                                                                  │
│  03.02  Agent ID FIC                                             │
│         jwt-bearer with Blueprint FIC as client_assertion        │
│                                                                  │
│  03.03  AUID access token  (multipart/form-data)                 │
│         grant_type=user_fic                                      │
│         requested_token_use=on_behalf_of                         │
│         scope=https://graph.microsoft.com/.default               │
│         username=<Agentic User UPN>                              │
│         + both FICs                                              │
│                                                                  │
│  →  Authorization: Bearer <AUID>  ───────────────────────────┐   │
└───────────────────────────────────────────────────────────── │ ──┘
                                                              ▼
┌──────────────────────────────────────────────────────────────────┐
│  weather-agent/ FastAPI  (:7200) — downstream AUID-validating API│
│                                                                  │
│  Verifies iss / tid / aud / appid / idtyp=user / exp             │
│  Calls Open-Meteo for real weather data                          │
│  Returns weather + Agentic User claims it identified             │
└──────────────────────────────────────────────────────────────────┘
```

The full FIC chain is the official AUID recipe (`03.01 → 03.02 → 03.03`) — same as `Connect_3P_agent_to_AgentID_using_HTTPs` but expressed as Python instead of raw Insomnia HTTP recipes, plus a downstream service that demonstrates what a Weather/CRM/HR/etc. API would do when it receives an AUID token.

---

## Prerequisites

- An Entra tenant where you can:
  - register applications,
  - grant admin consent,
  - create users.
- A **Blueprint** app registration and **Agent Identity** app registration. If you don't have one yet, follow the OBO/Autonomous Agent ID demo first — this AUID demo deliberately reuses the same Blueprint + Agent Identity.
- A **Blueprint client secret** with the **Graph application permission** `AgentIdUser.ReadWrite.IdentityParentedBy` granted admin consent. (The provisioning script can mint a secret for you.)
- Python 3.10+ and PowerShell 7 (`pwsh`).

---

## Quick start

```powershell
# 1. Clone & configure
git clone https://github.com/vj926/AgentUserID-using-EntraSDK-Deploy-using-AKS.git
cd AgentUserID-using-EntraSDK-Deploy-using-AKS
Copy-Item .env.example .env
# Fill TENANT_ID, BLUEPRINT_APP_ID, AGENT_IDENTITY_APP_ID, BLUEPRINT_CLIENT_SECRET

# 2. PREFLIGHT — verify every required permission/scope/app-role/secret/FIC
# Reports PASS/FAIL/WARN per row, exits non-zero on FAIL. DO NOT SKIP.
pwsh ./scripts/00-preflight-check.ps1
# See .claude/skills/deploy-auid-demo/PERMISSIONS.md for the full reference.

# 3. Create the Agentic User (microsoft.graph.agentUser) parented to your Agent Identity
pwsh ./scripts/01-provision-agentic-user.ps1

# 4. Grant the Agentic User delegated Graph permissions (User.Read) for AllPrincipals
pwsh ./scripts/02-grant-agentic-user-consent.ps1

# 5. Sanity-check the FIC chain end-to-end in PowerShell (no Python yet)
pwsh ./scripts/03-test-token-chain.ps1
# Expect: 🎉 Full AUID token chain works end-to-end.

# 6. Run the demo stack
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r backend/requirements.txt -r weather-agent/requirements.txt

# Terminal 1: AUID broker
python -m uvicorn backend.app:app --host 127.0.0.1 --port 7100

# Terminal 2: Downstream Weather Agent
python -m uvicorn weather-agent.app:app --host 127.0.0.1 --port 7200

# Terminal 3: UI
python -m http.server 7001 --directory ui

# Open http://localhost:7001 and ask "What is the weather in Dallas?"
```

---

## What the UI shows

- **Left panel** — chat with a static **Acting as** badge showing the Agentic User UPN (no human sign-in, by design — that's the whole point of AUID).
- **Right panel** — live trace of each step in the FIC chain with the decoded JWT claims, ending in a green PASS row block when the Weather Agent validates the token. Mirrors the OBO demo's debug panel.

---

## Repository layout

```
├── backend/                  FastAPI AUID broker (FIC chain in Python)
│   ├── app.py                /api/step/01..03, /api/chain, /api/call-weather
│   └── auid_flow.py          Pure-Python implementation of recipe 03.01–03.04
├── weather-agent/            Downstream AUID-validating API
│   └── app.py                Verifies AUID, returns weather + claims
├── ui/                       Single-file HTML UI matching the OBO AKS demo
│   └── index.html
├── scripts/                  PowerShell helpers
│   ├── 01-provision-agentic-user.ps1
│   ├── 02-grant-agentic-user-consent.ps1
│   └── 03-test-token-chain.ps1
└── .env.example
```

---

## Token verification caveat (and the "do it properly" path)

The default AUID chain in this repo requests `scope=https://graph.microsoft.com/.default`, so the issued token carries `aud=https://graph.microsoft.com`. Microsoft Graph access tokens include a special `nonce` claim in the JWT header that makes their signature only verifiable by Graph itself — third-party services cannot cryptographically verify them. The `weather-agent` therefore performs **strict claim-based validation** (`iss`, `tid`, `aud`, `appid`, `idtyp=user`, `exp`) without crypto signature verification.

For a production-grade pattern, register the downstream service as its own Entra app with an exposed scope (e.g., `Weather.Read`), grant the Agentic User delegated consent on it, set `WEATHER_AGENT_APP_ID=<that app's appId>` in `.env`, and the broker will request `scope=api://<weather-app>/.default`. The Weather Agent will then verify the token signature against the v2 JWKS endpoint normally.

---

## AKS deployment (parity with the OBO demo)

The included `k8s/` folder (coming next) provides a Helm chart mirroring the structure of the OBO AKS demo, so the same Blueprint + Agent Identity can host both demos on a single cluster. For now, the local stack is sufficient to demonstrate the AUID flow end-to-end to customers.

---

## License

MIT
