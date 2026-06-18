# Register the Weather Agent as its own Entra app registration with an exposed
# scope, then grant the Agent Identity admin consent for that scope. This is
# REQUIRED for the AUID demo: the AUID token must be issued for the Weather
# Agent's own audience so its JWT signature is verifiable against the tenant
# JWKS (Graph access tokens cannot be verified by 3rd parties).
#
# Output: writes WEATHER_AGENT_APP_ID + WEATHER_AGENT_APP_ID_URI to .env / vars file.
#
# Usage (from repo root):
#   pwsh ./scripts/04-register-weather-app.ps1 `
#     -TenantId          "<tid>" `
#     -AgentIdentityAppId "<agent app id>" `
#     -DisplayName       "AUID Weather Agent (dev)"

param(
  [Parameter(Mandatory=$true)] [string] $TenantId,
  [Parameter(Mandatory=$true)] [string] $AgentIdentityAppId,
  [string] $DisplayName = "AUID Weather Agent",
  [string] $ScopeName   = "Weather.Read",
  [string] $EnvFile     = ".env"
)

$ErrorActionPreference = "Stop"

Import-Module Microsoft.Graph.Authentication -ErrorAction Stop
Connect-MgGraph -TenantId $TenantId -Scopes "Application.ReadWrite.All","DelegatedPermissionGrant.ReadWrite.All" -NoWelcome | Out-Null

# 1. Create the Weather Agent app + SP if it doesn't exist.
$existing = az ad app list --display-name "$DisplayName" --query "[0]" -o json | ConvertFrom-Json
if ($existing) {
  Write-Host "Weather Agent app '$DisplayName' already exists: $($existing.appId)"
  $appId = $existing.appId
  $objectId = $existing.id
} else {
  $created = az ad app create --display-name "$DisplayName" --sign-in-audience "AzureADMyOrg" -o json | ConvertFrom-Json
  $appId    = $created.appId
  $objectId = $created.id
  Write-Host "Created Weather Agent app: $appId"
}

# 2. Set Application ID URI = api://<appId>
$idUri = "api://$appId"
az ad app update --id $appId --identifier-uris $idUri | Out-Null

# 3. Add the Weather.Read scope (delegated) to api.oauth2PermissionScopes if missing.
$app = az ad app show --id $appId -o json | ConvertFrom-Json
$scopes = @($app.api.oauth2PermissionScopes)
if (-not ($scopes | Where-Object { $_.value -eq $ScopeName })) {
  $newScope = @{
    id                      = [guid]::NewGuid().ToString()
    adminConsentDescription = "Read weather as the agentic user."
    adminConsentDisplayName = "Read weather"
    isEnabled               = $true
    type                    = "User"
    userConsentDescription  = "Allow the agent to read weather on your behalf."
    userConsentDisplayName  = "Read weather"
    value                   = $ScopeName
  }
  $scopes += $newScope
  $body = @{ api = @{ oauth2PermissionScopes = $scopes } } | ConvertTo-Json -Depth 10
  $tmp = New-TemporaryFile
  $body | Out-File -FilePath $tmp -Encoding utf8
  az rest --method PATCH `
    --url "https://graph.microsoft.com/v1.0/applications/$objectId" `
    --headers "Content-Type=application/json" `
    --body "@$tmp" | Out-Null
  Remove-Item $tmp
  Write-Host "Added scope $ScopeName to $appId"
}

# 4. Ensure the Weather Agent SP exists (consent targets the SP, not the app).
$sp = az ad sp show --id $appId -o json 2>$null | ConvertFrom-Json
if (-not $sp) {
  $sp = az ad sp create --id $appId -o json | ConvertFrom-Json
  Write-Host "Created Weather Agent SP: $($sp.id)"
}

# 5. Ensure the Agent Identity SP exists.
$agentSp = az ad sp show --id $AgentIdentityAppId -o json 2>$null | ConvertFrom-Json
if (-not $agentSp) {
  Write-Error "Agent Identity SP for $AgentIdentityAppId not found. Run the entra-agent-id-setup workflow first."
  exit 1
}

# 6. Grant Agent Identity → Weather Agent (delegated, AllPrincipals) admin consent.
$grantBody = @{
  clientId    = $agentSp.id
  consentType = "AllPrincipals"
  resourceId  = $sp.id
  scope       = $ScopeName
} | ConvertTo-Json
try {
  Invoke-MgGraphRequest -Method POST `
    -Uri "https://graph.microsoft.com/v1.0/oauth2PermissionGrants" `
    -Body $grantBody -ContentType "application/json" | Out-Null
  Write-Host "Granted Agent → Weather Agent ($ScopeName) admin consent."
} catch {
  if ($_.Exception.Message -match "already exists|conflict") {
    Write-Host "Admin consent already in place - skipping."
  } else { throw }
}

# 7. Persist to .env / vars file (or just print for the operator).
Write-Host ""
Write-Host "============================================================"
Write-Host " WEATHER_AGENT_APP_ID     = $appId"
Write-Host " WEATHER_AGENT_APP_ID_URI = $idUri"
Write-Host " WEATHER_AGENT_SCOPE      = $ScopeName"
Write-Host "============================================================"
Write-Host "Add (or update) these in $EnvFile and your /tmp/deploy-vars.sh"
