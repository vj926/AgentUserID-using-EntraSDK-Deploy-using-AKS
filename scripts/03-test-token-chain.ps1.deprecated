# 03-test-token-chain.ps1
# Walks the full FIC token chain end-to-end with no Python:
#   1. Blueprint FIC token (recipe 03.01)
#   2. Agent ID FIC token  (recipe 03.02)
#   3. Agentic User access token (recipe 03.03)
#   4. Call Graph /me as the Agentic User (recipe 03.04)
#
# Reads config from .env (use 01-provision script to populate it first).
#
# Usage:
#   ./03-test-token-chain.ps1
#   ./03-test-token-chain.ps1 -BlueprintClientSecret "..."   # if not in .env

[CmdletBinding()]
param(
    [string]$EnvFile = "$PSScriptRoot/../.env",
    [string]$BlueprintClientSecret
)

$ErrorActionPreference = 'Stop'

# ---- Load .env ----
if (-not (Test-Path $EnvFile)) { throw "Missing $EnvFile. Run 01-provision-agentic-user.ps1 first." }
$env = @{}
Get-Content $EnvFile | ForEach-Object {
    if ($_ -match '^\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*)\s*$') { $env[$matches[1]] = $matches[2] }
}
$tenant      = $env.TENANT_ID
$blueprint   = $env.BLUEPRINT_APP_ID
$agentId     = $env.AGENT_IDENTITY_APP_ID
$userUpn     = $env.AGENT_USER_UPN
$bpSecret    = if ($BlueprintClientSecret) { $BlueprintClientSecret } else { $env.BLUEPRINT_CLIENT_SECRET }

foreach ($k in 'TENANT_ID','BLUEPRINT_APP_ID','AGENT_IDENTITY_APP_ID','AGENT_USER_UPN') {
    if (-not $env[$k]) { throw "Missing $k in $EnvFile" }
}
if (-not $bpSecret) { throw "Missing BLUEPRINT_CLIENT_SECRET (pass -BlueprintClientSecret or set in .env)" }

$tokenUrl = "https://login.microsoftonline.com/$tenant/oauth2/v2.0/token"
Write-Host "Tenant=$tenant"
Write-Host "Blueprint=$blueprint  AgentID=$agentId  AgenticUser=$userUpn"
Write-Host ""

# ---- Step 03.01: Blueprint FIC token (fmi_path = AgentID) ----
Write-Host "[1/4] Blueprint FIC token..." -ForegroundColor Cyan
$basic = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes("${blueprint}:${bpSecret}"))
$resp1 = Invoke-RestMethod -Method POST -Uri $tokenUrl -Headers @{ Authorization = "Basic $basic" } `
    -ContentType 'application/x-www-form-urlencoded' -Body @{
        scope      = 'api://AzureADTokenExchange/.default'
        grant_type = 'client_credentials'
        fmi_path   = $agentId
    }
$bpFic = $resp1.access_token
if (-not $bpFic) { throw "Step 1 returned no access_token. Response: $($resp1 | ConvertTo-Json -Depth 5)" }
Write-Host "  ✅ Blueprint FIC obtained (len=$($bpFic.Length))" -ForegroundColor Green

# ---- Step 03.02: Agent ID FIC token (client_assertion = BP FIC) ----
Write-Host "[2/4] Agent ID FIC token..." -ForegroundColor Cyan
$resp2 = Invoke-RestMethod -Method POST -Uri $tokenUrl -ContentType 'application/x-www-form-urlencoded' -Body @{
    client_id             = $agentId
    scope                 = 'api://AzureADTokenExchange/.default'
    grant_type            = 'client_credentials'
    client_assertion_type = 'urn:ietf:params:oauth:client-assertion-type:jwt-bearer'
    client_assertion      = $bpFic
}
$agFic = $resp2.access_token
if (-not $agFic) { throw "Step 2 returned no access_token. Response: $($resp2 | ConvertTo-Json -Depth 5)" }
Write-Host "  ✅ Agent ID FIC obtained (len=$($agFic.Length))" -ForegroundColor Green

# ---- Step 03.03: Agentic User access token (multipart, grant_type=user_fic) ----
Write-Host "[3/4] Agentic User access token..." -ForegroundColor Cyan
# Build multipart/form-data manually for max compatibility
$boundary = [Guid]::NewGuid().ToString()
function Add-Part { param($sb, $name, $value)
    [void]$sb.AppendLine("--$boundary")
    [void]$sb.AppendLine("Content-Disposition: form-data; name=`"$name`"")
    [void]$sb.AppendLine()
    [void]$sb.AppendLine($value)
}
$sb = New-Object System.Text.StringBuilder
Add-Part $sb 'client_id'                          $agentId
Add-Part $sb 'client_assertion_type'              'urn:ietf:params:oauth:client-assertion-type:jwt-bearer'
Add-Part $sb 'client_assertion'                   $bpFic
Add-Part $sb 'grant_type'                         'user_fic'
Add-Part $sb 'requested_token_use'                'on_behalf_of'
Add-Part $sb 'scope'                              'https://graph.microsoft.com/.default'
Add-Part $sb 'username'                           $userUpn
Add-Part $sb 'user_federated_identity_credential' $agFic
[void]$sb.AppendLine("--$boundary--")
$body = $sb.ToString()
try {
    $resp3 = Invoke-RestMethod -Method POST -Uri $tokenUrl -ContentType "multipart/form-data; boundary=$boundary" -Body $body
} catch {
    Write-Host "  ❌ Step 3 failed. Server response:" -ForegroundColor Red
    if ($_.ErrorDetails.Message) { Write-Host $_.ErrorDetails.Message -ForegroundColor Red }
    throw
}
$auid = $resp3.access_token
if (-not $auid) { throw "Step 3 returned no access_token. Response: $($resp3 | ConvertTo-Json -Depth 5)" }
Write-Host "  ✅ Agentic User token obtained (len=$($auid.Length))" -ForegroundColor Green

# ---- Decode the AUID token claims (no signature check) ----
function Decode-JwtPayload {
    param([string]$jwt)
    $parts = $jwt.Split('.')
    $pad = $parts[1].PadRight($parts[1].Length + (4 - $parts[1].Length % 4) % 4, '=')
    $bytes = [Convert]::FromBase64String($pad.Replace('-','+').Replace('_','/'))
    return [Text.Encoding]::UTF8.GetString($bytes) | ConvertFrom-Json
}
$claims = Decode-JwtPayload -jwt $auid
Write-Host "  AUID token claims (selected):" -ForegroundColor Cyan
$claims | Select-Object aud, iss, appid, oid, sub, upn, unique_name, scp, idtyp, xms_idrel | Format-List

# ---- Step 03.04: call Graph /me ----
Write-Host "[4/4] GET https://graph.microsoft.com/v1.0/me (as Agentic User)..." -ForegroundColor Cyan
try {
    $me = Invoke-RestMethod -Method GET -Uri "https://graph.microsoft.com/v1.0/me" -Headers @{ Authorization = "Bearer $auid" }
    Write-Host "  ✅ /me returned:" -ForegroundColor Green
    $me | ConvertTo-Json -Depth 5
} catch {
    Write-Host "  ❌ /me failed. Server response:" -ForegroundColor Red
    if ($_.ErrorDetails.Message) { Write-Host $_.ErrorDetails.Message -ForegroundColor Red }
    throw
}

Write-Host ""
Write-Host "🎉 Full AUID token chain works end-to-end." -ForegroundColor Green
