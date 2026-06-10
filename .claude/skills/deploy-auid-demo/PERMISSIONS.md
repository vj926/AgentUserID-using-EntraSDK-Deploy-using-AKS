# AUID Permissions & Scopes — Customer Checklist

This is the **definitive list** of every Entra permission, scope, role assignment, app role, federated identity credential, and oauth2PermissionGrant required for the AUID flow to work end-to-end. The `scripts/00-preflight-check.ps1` script verifies each item programmatically and reports PASS / FAIL / WARN per row. **Run preflight first.**

---

## 1. Admin operator — delegated scopes needed to run the demo

The human who runs the setup scripts must sign in as a **Cloud Application Administrator** (or higher) and consent to these delegated Graph scopes on the well-known Microsoft Graph PowerShell client (`14d82eec-204b-4c2f-b7e8-296a70dab67e`):

| Scope | Why |
|---|---|
| `Application.ReadWrite.All` | Read/create app registrations; mint Blueprint client secret if missing |
| `AppRoleAssignment.ReadWrite.All` | Grant the Blueprint SP the Graph app role `AgentIdUser.ReadWrite.IdentityParentedBy` |
| `DelegatedPermissionGrant.ReadWrite.All` | Create the `oauth2PermissionGrant` (AllPrincipals) that lets the Agentic User call Graph |
| `Directory.Read.All` | Resolve service principals by appId |
| `User.Read` | Identify the signed-in operator |

The preflight script decodes the issued admin token and verifies every scope above is present in `scp`. Missing scopes → FAIL with a remediation hint.

---

## 2. Blueprint app — what it needs in Entra

| Item | Required? | Verified by preflight? | How to grant |
|---|---|---|---|
| App registration exists in the tenant | ✅ Yes | ✅ | Portal → App registrations → New registration |
| Service principal for the app exists | ✅ Yes | ✅ | Portal → Enterprise apps → New application → from app registration |
| Graph **app role** `AgentIdUser.ReadWrite.IdentityParentedBy` (roleId `4aa6e624-eee0-40ab-bdd8-f9639038a614`) granted **with admin consent** | ✅ Yes — this is the role that authorizes creation of `microsoft.graph.agentUser` objects parented to your Agent Identity | ✅ | `POST /v1.0/servicePrincipals/{bpSpId}/appRoleAssignments` with `{ principalId, resourceId: <graphSpId>, appRoleId: 4aa6e624-... }` |
| At least one **non-expired client secret** | ✅ Yes (for steps 03.01 and 03.03) | ✅ (also warns if &lt;14 days from expiry) | Portal → Certificates & secrets → New client secret, or `POST /applications/{id}/addPassword` |

> **Why an app role and not a delegated scope?** Creating a `microsoft.graph.agentUser` parented to your Agent Identity is an **app-only** Graph operation. A delegated admin token — even Global Admin — gets `403 Authorization_RequestDenied`. The Blueprint app must hold the application permission and use client_credentials to call Graph.

---

## 3. Agent Identity app — what it needs in Entra

| Item | Required? | Verified by preflight? | How to grant |
|---|---|---|---|
| App registration exists | ✅ Yes | ✅ | Portal → App registrations → New registration (with `agentApplication` extension settings against the Blueprint) |
| Service principal exists | ✅ Yes | ✅ | Required so the Agentic User can hold oauth2PermissionGrants against it |
| **Federated Identity Credential** trusting the Blueprint app (for jwt-bearer in step 03.02) | ✅ Yes | ✅ (warns if zero FICs) | `POST /applications/{aiAppObjectId}/federatedIdentityCredentials` with issuer = Entra v2 tenant issuer, subject = Blueprint appId, audience = `api://AzureADTokenExchange` |

---

## 4. Agentic User — delegated Graph permissions

The `microsoft.graph.agentUser` itself does not have its own consent UI. We grant **delegated** Graph scopes for it via `oauth2PermissionGrants` **for AllPrincipals** on the Agent Identity service principal. Without this grant, the AUID token will be issued but `GET /v1.0/me` returns 403.

| Item | Required? | Verified by preflight? | How to grant |
|---|---|---|---|
| `oauth2PermissionGrant` on Agent Identity SP → Graph SP, `consentType=AllPrincipals` | ✅ Yes | ✅ | `scripts/02-grant-agentic-user-consent.ps1` — or `POST /v1.0/oauth2PermissionGrants` with `clientId=<aiSpId>`, `resourceId=<graphSpId>`, `consentType=AllPrincipals`, `scope="User.Read"` |
| `User.Read` is in the granted scope string | ✅ Yes (for Graph `/me`) | ✅ | Same as above |

> **Pitfall:** Do **not** use the browser admin-consent URL (`https://login.microsoftonline.com/{tenant}/adminconsent?...&scope=User.Read+GroupMember.Read.All`) — the consent prompt page splits multi-word scopes incorrectly and you get `AADSTS650053: scope 'GroupMember.Read' that doesn't exist`. Grant via Graph `oauth2PermissionGrants` directly.

---

## 5. Dedicated Weather Agent app — REQUIRED for verifiable signatures

> **Status changed:** in earlier revisions of this repo, this section was *optional* because the AUID token was minted for `https://graph.microsoft.com/.default` and the Weather Agent fell back to claim-only validation. The AKS manifests now perform **full RS256 JWKS signature verification** in the Weather Agent, so the AUID token must be minted for the **Weather Agent's own audience**. A separate Weather Agent app registration is therefore mandatory.

`scripts/04-register-weather-app.ps1` automates everything in this section. Run it as part of Phase 1.

| Item | Required? | Verified by preflight? | How to grant |
|---|---|---|---|
| A separate app registration for the Weather Agent | ✅ Yes | ❌ (created by `scripts/04-register-weather-app.ps1`) | Portal → App registrations → New registration, or run the script |
| `identifierUris = [api://<weatherAppId>]` | ✅ Yes | ❌ | `PATCH /applications/{id}` (script does this) |
| Exposed scope `Weather.Read` | ✅ Yes | ❌ | `PATCH /applications/{id}` adding to `api.oauth2PermissionScopes` (script does this) |
| Service principal for the Weather Agent app | ✅ Yes | ❌ | Same flow as Blueprint SP (script does this) |
| `oauth2PermissionGrant` on Agent Identity SP → Weather Agent SP (`consentType=AllPrincipals`, `scope="Weather.Read"`) | ✅ Yes | ❌ | `POST /v1.0/oauth2PermissionGrants` (script does this) |
| `WEATHER_AGENT_APP_ID` and `WEATHER_AGENT_APP_ID_URI` set in `/tmp/deploy-vars.sh` | ✅ Yes | ❌ | Copy from the script's printed output before running `deploy-aks-dev.sh` |

When all of the above are present:
- The auth-sidecar's `DownstreamApis__weather__Scopes__0` env resolves to `api://<weather>/.default`.
- The issued AUID token has `aud=api://<weather>` and **no header `nonce`** (so it's verifiable by anyone holding the tenant JWKS).
- `weather-agent/app.py` performs full RS256 signature validation against `https://login.microsoftonline.com/<tenant>/discovery/v2.0/keys` plus claim checks (`iss`, `tid`, `aud`, `appid`, `idtyp=user`, `upn`, `exp`).

If the Weather Agent app or its admin-consent grant is missing, the auth-sidecar surfaces it as `AADSTS65001` / `consent_required` when you hit `/api/step/03-auid-token`.

---

## 6. Federated Identity Credential — what to verify after onboarding

`agentApplication` setups normally create the FIC on the Agent Identity app trusting the Blueprint automatically. Confirm by listing FICs:

```http
GET https://graph.microsoft.com/v1.0/applications/{agentIdAppObjectId}/federatedIdentityCredentials
```

You should see at least one credential whose `issuer` matches your Blueprint app's issuer and whose `audiences` includes `api://AzureADTokenExchange`.

---

## 7. What the preflight script does NOT verify

- **Conditional Access policies that block client_credentials or device-code on this tenant.** If 03.01 returns `AADSTS50079: Strong authentication is required` or similar, your CA policy needs a carve-out.
- **Network reachability** to `login.microsoftonline.com` and `graph.microsoft.com` from the host running the demo.
- **Time skew** on the demo host (tokens have `nbf`/`exp` — if your clock is &gt;5 min off, validation will fail).
- **Whether the Agentic User itself is enabled and unblocked** (rare, but the Agentic User is a real user object subject to user lifecycle policies).

These are the items to manually validate if preflight passes but the chain still fails.
