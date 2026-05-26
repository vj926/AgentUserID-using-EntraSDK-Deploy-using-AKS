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

## 5. (Optional) Dedicated Weather Agent app — for full signature verification

The default chain in this repo requests `scope=https://graph.microsoft.com/.default`. The resulting AUID token carries a `nonce` claim in its JWT header that prevents third parties from cryptographically verifying its signature — only Graph itself can. The Weather Agent therefore performs **strict claim-based validation** by default (`iss`, `tid`, `aud`, `appid`, `idtyp=user`, `exp`).

If you want full signature verification:

| Item | Required? | Verified by preflight? | How to grant |
|---|---|---|---|
| A separate app registration for the downstream service | Only if you want crypto verification | ❌ (manual) | Portal → App registrations → New registration |
| `identifierUris = [api://<that appId>]` | ✅ if above | ❌ | `PATCH /applications/{id}` |
| Exposed scope `Weather.Read` (or similar) | ✅ if above | ❌ | `PATCH /applications/{id}` adding to `api.oauth2PermissionScopes` |
| Service principal for the downstream app | ✅ if above | ❌ | Same flow as Blueprint SP |
| `oauth2PermissionGrant` on Agent Identity SP → Weather Agent SP (AllPrincipals, scope=`Weather.Read`) | ✅ if above | ❌ | `POST /v1.0/oauth2PermissionGrants` |
| `.env` set `WEATHER_AGENT_APP_ID=<that appId>` | ✅ if above | ❌ | Edit `.env` |

When all of the above are present, `backend/auid_flow.py` requests `scope=api://<weather-app>/.default`, the issued AUID token has `aud=api://<weather-app>`, and `weather-agent/app.py` switches to full RS256 verification against the v2 JWKS.

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
