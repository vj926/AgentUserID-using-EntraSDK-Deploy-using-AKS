# 01-provision-agentic-user.ps1
# Creates an Agentic User attached to an existing Blueprint + Agent Identity.
# Lifts from: Connect_3P_agent_to_AgentID_using_HTTPs README step 02.02
#
# Usage:
#   ./01-provision-agentic-user.ps1 `
#       -TenantId          "98430660-2a7e-4e6b-b49c-800a8ba8b657" `
#       -BlueprintAppId    "4f6ca43e-337c-4617-958f-e517cf1a1858" `
#       -AgentIdentityAppId "2b32c2c2-3a5a-435e-b07e-8ef20564364f" `
#       -AgentUserDisplayName "[ai] Digital Worker 01 Agent ID User" `
#       -AgentUserMailNickname "digitalworker01"
#
# Requires: Microsoft.Graph module + AgentIdentity.ReadWrite.All / User.ReadWrite.All / Application.ReadWrite.All

[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$TenantId,
    [Parameter(Mandatory=$true)][string]$BlueprintAppId,
    [Parameter(Mandatory=$true)][string]$AgentIdentityAppId,
    [string]$AgentUserDisplayName = "[ai] Digital Worker 01 Agent ID User",
    [string]$AgentUserMailNickname = "digitalworker01",
    [string]$EnvFile = "$PSScriptRoot/../.env"
)

$ErrorActionPreference = 'Stop'

# ---- Connect ----
if (-not (Get-Module -ListAvailable Microsoft.Graph.Authentication)) {
    throw "Microsoft.Graph module not installed. Run: Install-Module Microsoft.Graph -Scope CurrentUser"
}
Import-Module Microsoft.Graph.Authentication
Write-Host "Connecting to tenant $TenantId..."
Connect-MgGraph -TenantId $TenantId -UseDeviceCode -Scopes @(
    "Application.ReadWrite.All",
    "User.ReadWrite.All",
    "AgentIdentity.ReadWrite.All",
    "Directory.ReadWrite.All"
) -NoWelcome | Out-Null

# ---- Resolve tenant verified domain (UPN needs to live on a verified domain) ----
$org = Invoke-MgGraphRequest -Method GET -Uri "https://graph.microsoft.com/v1.0/organization?`$select=verifiedDomains"
$initialDomain = ($org.value[0].verifiedDomains | Where-Object { $_.isInitial -eq $true }).name
if (-not $initialDomain) { throw "Could not resolve initial verified domain for tenant." }
$upn = "$AgentUserMailNickname@$initialDomain"
Write-Host "Agentic User UPN will be: $upn"

# ---- Resolve Agent Identity client ID (== appId; treat both as same) ----
# The Agent Identity is the service principal of @odata.type microsoft.graph.agentIdentity.
# identityParentId on the agentUser must reference the Agent Identity *clientId* (== appId).
$agentIdentityClientId = $AgentIdentityAppId
Write-Host "Agent Identity clientId: $agentIdentityClientId"

# ---- Check if Agentic User already exists ----
$existing = $null
try {
    $existing = Invoke-MgGraphRequest -Method GET -Uri "https://graph.microsoft.com/beta/users/$upn"
} catch {
    if ($_.Exception.Message -notmatch '404|Request_ResourceNotFound|NotFound') { throw }
}
if ($existing) {
    Write-Host "✅ Agentic User already exists: id=$($existing.id) upn=$($existing.userPrincipalName)" -ForegroundColor Green
    $userId = $existing.id
} else {
    Write-Host "Creating Agentic User..."
    $body = @{
        '@odata.type'       = 'microsoft.graph.agentUser'
        displayName         = $AgentUserDisplayName
        userPrincipalName   = $upn
        mailNickname        = $AgentUserMailNickname
        accountEnabled      = $true
        identityParentId    = $agentIdentityClientId
    } | ConvertTo-Json -Depth 5
    $created = Invoke-MgGraphRequest -Method POST -Uri "https://graph.microsoft.com/beta/users" `
        -ContentType 'application/json' -Body $body
    Write-Host "✅ Created Agentic User: id=$($created.id) upn=$($created.userPrincipalName)" -ForegroundColor Green
    $userId = $created.id
}

# ---- Read-back verification ----
$verify = Invoke-MgGraphRequest -Method GET -Uri "https://graph.microsoft.com/beta/users/$userId`?`$select=id,displayName,userPrincipalName,identityParentId"
$verify | ConvertTo-Json -Depth 5

# ---- Persist to .env (idempotent) ----
function Set-EnvKey {
    param($Path, $Key, $Value)
    if (-not (Test-Path $Path)) {
        Copy-Item "$PSScriptRoot/../.env.example" $Path -Force
    }
    $lines = Get-Content $Path
    if ($lines -match "^$Key=") {
        $lines = $lines -replace "^$Key=.*","$Key=$Value"
    } else {
        $lines += "$Key=$Value"
    }
    Set-Content -Path $Path -Value $lines -Encoding UTF8
}

Set-EnvKey -Path $EnvFile -Key "AGENT_USER_UPN"          -Value $upn
Set-EnvKey -Path $EnvFile -Key "AGENT_USER_OBJECT_ID"    -Value $userId
Set-EnvKey -Path $EnvFile -Key "AGENT_USER_MAIL_NICKNAME" -Value $AgentUserMailNickname

Write-Host ""
Write-Host "Wrote to ${EnvFile}:"
Write-Host "  AGENT_USER_UPN=$upn"
Write-Host "  AGENT_USER_OBJECT_ID=$userId"
Write-Host ""
Write-Host "Next: run ./02-grant-agentic-user-consent.ps1 to grant Graph permissions to this Agentic User." -ForegroundColor Cyan
