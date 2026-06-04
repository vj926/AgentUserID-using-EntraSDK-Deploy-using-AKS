---
name: deploy-auid-demo
description: Provision and deploy the Agent ID User (AUID) demo. Use when the user mentions "AUID", "Agent ID User", "microsoft.graph.agentUser", "digital colleague identity", or wants to demo "an agent acting as its own user" (the non-OBO complement to the AKS Agent ID demo). The skill walks tenant prerequisites, mints the Agentic User parented to an existing Agent Identity, builds the Blueprint → Agent ID → AUID FIC chain, and brings up a local 3-tier stack (broker + downstream Weather Agent + UI) that mirrors the look-and-feel of the OBO AKS demo.
---

# Deploy AUID demo (Agent ID User)

## When to use this skill
Trigger when the user wants to demonstrate Agent ID User (AUID) — the mode where an Entra Agent Identity has its **own first-class user object** (`microsoft.graph.agentUser`) and the agent calls downstream services **as itself** (no human in the loop). This is the AUID analog of the OBO AKS demo in the `AgentID-using-EntraSDK_AKS` repo.

Do NOT use this skill for:
- Autonomous Agent flows (no user dimension — use the Agent ID AKS demo).
- On-Behalf-Of flows where a real human signs in (use the AKS OBO demo).

## Outcome
After completing this skill the customer will have:
- A `microsoft.graph.agentUser` provisioned in their tenant, parented to their existing Agent Identity app.
- The full Blueprint → Agent ID → AUID FIC chain proven end-to-end with a single PowerShell sanity check (`scripts/03-test-token-chain.ps1`).
- A running 3-tier local stack (broker on :7100, Weather Agent on :7200, UI on :7001) that visually matches the AKS OBO demo, with an "Acting as &lt;Agentic User UPN&gt;" badge replacing the human MSAL sign-in.

## Pre-flight checklist (DO NOT SKIP)

**Always run this before anything else:**

```powershell
pwsh ./scripts/00-preflight-check.ps1
```

The script signs the operator in (device code, as Cloud Application Administrator) and produces a colored PASS / FAIL / WARN report for every permission, scope, app role, client secret, service principal, and federated identity credential required by the AUID flow. It exits non-zero if anything is FAIL so you can wire it into CI.

See **`PERMISSIONS.md`** in this same folder for the complete list of what's checked and how to remediate each FAIL — broken down by:
- **Admin operator** (delegated Graph scopes needed to run the scripts)
- **Blueprint app** (Graph **app role** `AgentIdUser.ReadWrite.IdentityParentedBy` + client secret + SP)
- **Agent Identity app** (SP + Federated Identity Credential trusting the Blueprint)
- **Agentic User** (delegated `oauth2PermissionGrant` for `User.Read` AllPrincipals on Agent Identity SP → Graph SP)
- **Optional Weather Agent app** (only if the customer wants full cryptographic signature verification — see PERMISSIONS.md §5)

If the customer cannot pass preflight, **do not run any later script** — talk them through the FAIL rows first. Common blockers:

1. Blueprint SP missing app role `AgentIdUser.ReadWrite.IdentityParentedBy` (roleId `4aa6e624-eee0-40ab-bdd8-f9639038a614`) — Step 1 will return `403 Authorization_RequestDenied`.
2. No non-expired Blueprint client secret — Steps 03.01 and 03.03 fail.
3. Agent Identity app has no Federated Identity Credential trusting the Blueprint — Step 03.02 returns `AADSTS700016` or `invalid_client`.
4. Admin's delegated token missing `AppRoleAssignment.ReadWrite.All` — can't grant the Blueprint app role programmatically.
5. Multi-scope browser admin-consent URL splitting `GroupMember.Read.All` → AADSTS650053 — use Step 2 script (`02-grant-agentic-user-consent.ps1`) which posts to `oauth2PermissionGrants` directly instead.

## Workflow

### Step 0 — Preflight (REQUIRED)
```powershell
pwsh ./scripts/00-preflight-check.ps1
```
**If any row prints FAIL, fix it before proceeding** — see `PERMISSIONS.md` for the granular how-to on each row.

### Step 1 — Configure .env
```powershell
Copy-Item .env.example .env
# Fill: TENANT_ID, BLUEPRINT_APP_ID, AGENT_IDENTITY_APP_ID, BLUEPRINT_CLIENT_SECRET
```

### Step 2 — Provision the Agentic User
```powershell
pwsh ./scripts/01-provision-agentic-user.ps1
```
This script:
- Uses an app-only token from the Blueprint (the `AgentIdUser.ReadWrite.IdentityParentedBy` app role).
- POSTs to `/v1.0/users` with `@odata.type=#microsoft.graph.agentUser` and `identityParentId=<AgentIdentityAppId>`.
- Writes `AGENT_USER_UPN` and `AGENT_USER_OBJECT_ID` back into `.env`.

**Common pitfall:** running this with a delegated admin token instead of an app-only Blueprint token returns `403 Authorization_RequestDenied`. The Blueprint **must** hold the `AgentIdUser.ReadWrite.IdentityParentedBy` application role.

### Step 3 — Grant the Agentic User delegated Graph access
```powershell
pwsh ./scripts/02-grant-agentic-user-consent.ps1
```
Grants `User.Read` for `AllPrincipals` on the Agent Identity service principal via `oauth2PermissionGrants`. We do this programmatically — **not** via the browser admin-consent URL, because the consent-prompt page incorrectly splits `GroupMember.Read.All` into `GroupMember.Read` and fails with AADSTS650053.

### Step 4 — Sanity-check the FIC chain
```powershell
pwsh ./scripts/03-test-token-chain.ps1
```
Expect to see:
```
✓ 03.01 Blueprint FIC obtained
✓ 03.02 Agent ID FIC obtained
✓ 03.03 AUID access token obtained (idtyp=user, sub=<Agentic User OID>)
✓ 03.04 GET /me returned @odata.type=#microsoft.graph.agentUser
🎉 Full AUID token chain works end-to-end.
```

### Step 5 — Run the demo stack
```powershell
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r backend/requirements.txt -r weather-agent/requirements.txt

# 3 terminals (or background async sessions):
python -m uvicorn backend.app:app          --host 127.0.0.1 --port 7100
python -m uvicorn weather-agent.app:app    --host 127.0.0.1 --port 7200
python -m http.server 7001 --directory ui
```
Open `http://localhost:7001`. Ask **"What is the weather in Dallas?"**. The right panel will render the live FIC chain trace; the left panel will respond with weather data + Agentic User claims.

## Troubleshooting

| Symptom | Cause | Fix |
|--|--|--|
| Step 2 returns `403 Authorization_RequestDenied` | Trying to create `agentUser` with a delegated admin token | Blueprint SP must hold the **application** role `AgentIdUser.ReadWrite.IdentityParentedBy`. Grant via `POST /servicePrincipals/{bpSpId}/appRoleAssignments`. |
| Step 3 fails AADSTS650053 (`GroupMember.Read doesn't exist`) | Multi-scope browser admin-consent URL splits scopes wrong | Use `02-grant-agentic-user-consent.ps1` instead of the browser. |
| Step 4 03.03 returns `invalid_grant` | `username` not matching the Agentic User UPN, or grant_type=user_fic missing | Confirm `AGENT_USER_UPN` in `.env` matches what `01-provision` wrote, and that step 03.03 uses `multipart/form-data`. |
| Weather Agent returns `Signature verification failed` | AUID token has `aud=graph` with a Graph nonce in the JWT header (intentionally non-verifiable by third parties) | Either accept claim-only validation (default), or register a dedicated Weather Agent app, expose a scope, set `WEATHER_AGENT_APP_ID` in `.env`. |
| `Connect-MgGraph -UseDeviceCode` device code never prints | PowerShell async pipe doesn't flush the prompt | Hit `https://login.microsoftonline.com/{tenant}/oauth2/v2.0/devicecode` directly with `Invoke-RestMethod` to surface the code. |
| PS 5.1 fails to load Microsoft.Graph 2.x | Module needs PS7 | Use `pwsh` (already installed at `C:\Users\<you>\AppData\Local\Microsoft\WindowsApps\pwsh.exe`). |

## Files in this repo

- `scripts/01-provision-agentic-user.ps1` — creates the `microsoft.graph.agentUser` parented to the Agent Identity.
- `scripts/02-grant-agentic-user-consent.ps1` — grants delegated `User.Read` for AllPrincipals.
- `scripts/03-test-token-chain.ps1` — proves the FIC chain end-to-end in pure PowerShell.
- `backend/auid_flow.py` — Python implementation of the FIC chain (recipe 03.01–03.04).
- `backend/app.py` — FastAPI broker exposing each step + a one-shot `/api/call-weather` endpoint.
- `weather-agent/app.py` — Downstream service that validates the AUID token and returns weather.
- `ui/index.html` — Single-file UI matching the OBO AKS demo's look-and-feel.

## Customer hand-off checklist

- [ ] Customer ran `scripts/00-preflight-check.ps1` and **all rows are PASS** (warnings may be acceptable — review with them).
- [ ] Tenant ID, Blueprint App ID, Agent Identity App ID confirmed with customer.
- [ ] Blueprint SP holds `AgentIdUser.ReadWrite.IdentityParentedBy` (app role) — preflight section D verifies.
- [ ] Blueprint client secret minted and pasted into `.env` — preflight section E verifies.
- [ ] Agent Identity has a FIC trusting the Blueprint — preflight section G verifies.
- [ ] `scripts/03-test-token-chain.ps1` prints the success banner.
- [ ] Local UI at `http://localhost:7001` shows green PASS rows and weather response.
- [ ] Customer understands the **OBO vs AUID** comparison (table in `README.md`).
- [ ] Customer reviewed the **token verification caveat** in `README.md` and chose either claim-only validation or the dedicated Weather Agent app registration.
- [ ] Customer has a copy of `PERMISSIONS.md` for ongoing reference.
