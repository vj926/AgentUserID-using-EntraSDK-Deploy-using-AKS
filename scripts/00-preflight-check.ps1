# 00-preflight-check.ps1
#
# Programmatic preflight that verifies the tenant is ready for the AUID flow.
# Run this BEFORE 01-provision-agentic-user.ps1. The script reports each
# required permission/scope/role as PASS / FAIL / WARN with remediation hints,
# and exits non-zero if anything is FAIL so CI/automation can gate on it.
#
# What it checks (in order):
#   [A] .env values are present and non-empty
#   [B] Admin can sign in (delegated) with the scopes needed to inspect tenant
#   [C] Blueprint app + Blueprint SP exist
#   [D] Blueprint SP has Graph app role  AgentIdUser.ReadWrite.IdentityParentedBy
#       (roleId 4aa6e624-eee0-40ab-bdd8-f9639038a614) assigned + admin consented
#   [E] Blueprint app has at least one non-expired client secret
#   [F] Agent Identity app + SP exist
#   [G] Agent Identity app has a Federated Identity Credential trusting Blueprint
#   [H] Microsoft Graph SP exists in tenant (it always should — sanity)
#   [I] (Optional, after provisioning) Agentic User has User.Read for AllPrincipals
#
# Usage:
#   pwsh ./scripts/00-preflight-check.ps1
#   pwsh ./scripts/00-preflight-check.ps1 -SkipAgenticUser   # before provisioning

[CmdletBinding()]
param(
    [string]$EnvFile = "$PSScriptRoot/../.env",
    [switch]$SkipAgenticUser
)

$ErrorActionPreference = 'Stop'
$script:FAILS = 0
$script:WARNS = 0

function Write-Result {
    param([string]$Label, [string]$Status, [string]$Detail = "")
    $color = switch ($Status) {
        "PASS" { "Green" }
        "FAIL" { "Red";  $script:FAILS++ }
        "WARN" { "Yellow"; $script:WARNS++ }
        default { "Gray" }
    }
    Write-Host ("  [{0,-4}] " -f $Status) -ForegroundColor $color -NoNewline
    Write-Host $Label -NoNewline
    if ($Detail) { Write-Host "  -  $Detail" -ForegroundColor DarkGray } else { Write-Host "" }
}
function Section($t) { Write-Host ""; Write-Host "=== $t ===" -ForegroundColor Cyan }

# ---- [A] Load .env ----
Section "A. Local config (.env)"
if (-not (Test-Path $EnvFile)) {
    Write-Result ".env file at $EnvFile" "FAIL" "Copy .env.example to .env and fill in values"
    Write-Host ""
    Write-Host "Aborting — fix the .env file and rerun." -ForegroundColor Red
    exit 1
}
$envv = @{}
Get-Content $EnvFile | ForEach-Object {
    if ($_ -match '^\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*)\s*$') { $envv[$matches[1]] = $matches[2].Trim() }
}
$required = @{
    "TENANT_ID"               = "Your Entra tenant ID (GUID)"
    "BLUEPRINT_APP_ID"        = "appId of the Blueprint app registration"
    "AGENT_IDENTITY_APP_ID"   = "appId of the Agent Identity app registration"
}
foreach ($k in $required.Keys) {
    if ([string]::IsNullOrWhiteSpace($envv[$k])) {
        Write-Result "$k present" "FAIL" $required[$k]
    } else {
        Write-Result "$k = $($envv[$k])" "PASS"
    }
}
if (-not $envv.BLUEPRINT_CLIENT_SECRET) {
    Write-Result "BLUEPRINT_CLIENT_SECRET present" "WARN" "Required to run scripts 01 and 03. Mint one in Entra portal or via 01-provision."
} else {
    Write-Result "BLUEPRINT_CLIENT_SECRET present" "PASS" "(value masked)"
}

if ($script:FAILS -gt 0) {
    Write-Host ""
    Write-Host "Aborting before admin sign-in — fix the .env file and rerun." -ForegroundColor Red
    exit 1
}

$tenant      = $envv.TENANT_ID
$blueprint   = $envv.BLUEPRINT_APP_ID
$agentId     = $envv.AGENT_IDENTITY_APP_ID

# ---- [B] Admin sign-in (device code) ----
Section "B. Admin delegated sign-in"
$clientId = "14d82eec-204b-4c2f-b7e8-296a70dab67e" # Microsoft Graph PowerShell well-known
$scopes   = "Application.ReadWrite.All AppRoleAssignment.ReadWrite.All DelegatedPermissionGrant.ReadWrite.All Directory.Read.All User.Read offline_access"
try {
    $dc = Invoke-RestMethod -Method POST "https://login.microsoftonline.com/$tenant/oauth2/v2.0/devicecode" -Body @{client_id=$clientId; scope=$scopes}
} catch {
    Write-Result "Reach Entra device-code endpoint" "FAIL" $_.Exception.Message
    exit 1
}
Write-Host ""
Write-Host "  Sign in as a Cloud Application Administrator (or higher) of tenant $tenant" -ForegroundColor Yellow
Write-Host "  Open: $($dc.verification_uri)" -ForegroundColor Yellow
Write-Host "  Code: $($dc.user_code)" -ForegroundColor Yellow
Write-Host ""

$token = $null
for ($i = 0; $i -lt 180; $i++) {
    try {
        $r = Invoke-RestMethod -Method POST "https://login.microsoftonline.com/$tenant/oauth2/v2.0/token" -Body @{
            grant_type   = "urn:ietf:params:oauth:grant-type:device_code"
            client_id    = $clientId
            device_code  = $dc.device_code
        } -ErrorAction Stop
        $token = $r.access_token
        break
    } catch {
        $err = $null
        try { $err = ($_.ErrorDetails.Message | ConvertFrom-Json).error } catch {}
        if ($err -eq "authorization_pending") { Start-Sleep -Seconds $dc.interval; continue }
        Write-Result "Device-code sign-in" "FAIL" "$err"
        exit 1
    }
}
if (-not $token) { Write-Result "Device-code sign-in" "FAIL" "Timed out"; exit 1 }
Write-Result "Admin signed in" "PASS"

# Decode token, inspect granted scopes
$parts = $token.Split('.')
$pad = $parts[1].Replace('-','+').Replace('_','/'); while ($pad.Length % 4) { $pad += '=' }
$claims = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($pad)) | ConvertFrom-Json
$grantedScopes = $claims.scp -split ' '

$needScopes = @(
    "Application.ReadWrite.All",
    "AppRoleAssignment.ReadWrite.All",
    "DelegatedPermissionGrant.ReadWrite.All",
    "Directory.Read.All"
)
foreach ($s in $needScopes) {
    if ($grantedScopes -contains $s) {
        Write-Result "Admin token has scope: $s" "PASS"
    } else {
        Write-Result "Admin token has scope: $s" "FAIL" "Admin consent missing for this scope on the well-known Graph PowerShell client; ask a Global Admin to consent."
    }
}

$H = @{ Authorization = "Bearer $token"; "Content-Type" = "application/json" }

# ---- [C] Blueprint app + SP ----
Section "C. Blueprint app registration"
$bpApp = $null; $bpSp = $null
try {
    $r = Invoke-RestMethod -Method GET "https://graph.microsoft.com/v1.0/applications?`$filter=appId eq '$blueprint'" -Headers $H
    $bpApp = $r.value | Select-Object -First 1
} catch {}
if (-not $bpApp) {
    Write-Result "Blueprint app exists (appId=$blueprint)" "FAIL" "App registration not found in tenant $tenant"
} else {
    Write-Result "Blueprint app exists (appId=$blueprint)" "PASS" "displayName=$($bpApp.displayName)"
}
try {
    $r = Invoke-RestMethod -Method GET "https://graph.microsoft.com/v1.0/servicePrincipals?`$filter=appId eq '$blueprint'" -Headers $H
    $bpSp = $r.value | Select-Object -First 1
} catch {}
if (-not $bpSp) {
    Write-Result "Blueprint service principal exists" "FAIL" "Run: New-MgServicePrincipal -AppId $blueprint, or in portal: Enterprise Apps -> New application -> from app registration"
} else {
    Write-Result "Blueprint service principal exists" "PASS" "spId=$($bpSp.id)"
}

# ---- [D] Blueprint SP has Graph app role AgentIdUser.ReadWrite.IdentityParentedBy ----
Section "D. Required Graph application permissions on Blueprint SP"
$graphSp = $null
try {
    $r = Invoke-RestMethod -Method GET "https://graph.microsoft.com/v1.0/servicePrincipals?`$filter=appId eq '00000003-0000-0000-c000-000000000000'" -Headers $H
    $graphSp = $r.value | Select-Object -First 1
} catch {}
if (-not $graphSp) {
    Write-Result "Microsoft Graph SP found in tenant" "FAIL" "Highly unusual — this SP is normally always present."
} else {
    Write-Result "Microsoft Graph SP found in tenant" "PASS" "spId=$($graphSp.id)"
}

$requiredAppRoles = @{
    "AgentIdUser.ReadWrite.IdentityParentedBy" = "4aa6e624-eee0-40ab-bdd8-f9639038a614"
}
if ($bpSp -and $graphSp) {
    $bpAssignments = (Invoke-RestMethod -Method GET "https://graph.microsoft.com/v1.0/servicePrincipals/$($bpSp.id)/appRoleAssignments" -Headers $H).value
    foreach ($roleName in $requiredAppRoles.Keys) {
        $roleId = $requiredAppRoles[$roleName]
        $hit = $bpAssignments | Where-Object { $_.resourceId -eq $graphSp.id -and $_.appRoleId -eq $roleId }
        if ($hit) {
            Write-Result "Blueprint SP has Graph app role: $roleName" "PASS"
        } else {
            Write-Result "Blueprint SP has Graph app role: $roleName" "FAIL" @"
Grant it with:
  POST https://graph.microsoft.com/v1.0/servicePrincipals/$($bpSp.id)/appRoleAssignments
  { "principalId": "$($bpSp.id)", "resourceId": "$($graphSp.id)", "appRoleId": "$roleId" }
This is the role that allows the Blueprint to create microsoft.graph.agentUser objects.
"@
        }
    }
}

# ---- [E] Blueprint client secret ----
Section "E. Blueprint client secret"
if ($bpApp) {
    $now = Get-Date
    $live = @($bpApp.passwordCredentials | Where-Object { $_.endDateTime -and ([DateTime]$_.endDateTime) -gt $now })
    if ($live.Count -eq 0) {
        Write-Result "Blueprint has a non-expired client secret" "FAIL" "Mint one in portal (Certificates & secrets) or via Graph: POST /applications/$($bpApp.id)/addPassword"
    } else {
        $soonest = ($live | Sort-Object endDateTime)[0]
        $daysLeft = [int](([DateTime]$soonest.endDateTime) - $now).TotalDays
        if ($daysLeft -lt 14) {
            Write-Result "Blueprint has a non-expired client secret" "WARN" "$($live.Count) secret(s), soonest expires in $daysLeft day(s) — rotate soon"
        } else {
            Write-Result "Blueprint has a non-expired client secret" "PASS" "$($live.Count) secret(s), soonest expires in $daysLeft day(s)"
        }
    }
}

# ---- [F] Agent Identity app + SP ----
Section "F. Agent Identity app registration"
$aiApp = $null; $aiSp = $null
try {
    $r = Invoke-RestMethod -Method GET "https://graph.microsoft.com/v1.0/applications?`$filter=appId eq '$agentId'" -Headers $H
    $aiApp = $r.value | Select-Object -First 1
} catch {}
if (-not $aiApp) {
    Write-Result "Agent Identity app exists (appId=$agentId)" "FAIL" "App registration not found"
} else {
    Write-Result "Agent Identity app exists (appId=$agentId)" "PASS" "displayName=$($aiApp.displayName)"
}
try {
    $r = Invoke-RestMethod -Method GET "https://graph.microsoft.com/v1.0/servicePrincipals?`$filter=appId eq '$agentId'" -Headers $H
    $aiSp = $r.value | Select-Object -First 1
} catch {}
if (-not $aiSp) {
    Write-Result "Agent Identity service principal exists" "FAIL" "Required so delegated consent can be granted to the Agentic User."
} else {
    Write-Result "Agent Identity service principal exists" "PASS" "spId=$($aiSp.id)"
}

# ---- [G] FIC trusting the Blueprint ----
Section "G. Federated Identity Credential on Agent Identity"
if ($aiApp) {
    $fics = @()
    try { $fics = (Invoke-RestMethod -Method GET "https://graph.microsoft.com/v1.0/applications/$($aiApp.id)/federatedIdentityCredentials" -Headers $H).value } catch {}
    if (-not $fics -or $fics.Count -eq 0) {
        Write-Result "Agent Identity has a Federated Identity Credential" "FAIL" "Required for step 03.02 (jwt-bearer with Blueprint FIC). Add a FIC on the Agent Identity app trusting the Blueprint as issuer."
    } else {
        Write-Result "Agent Identity has $($fics.Count) FIC(s)" "PASS" ("subjects: " + (($fics | ForEach-Object subject) -join ", "))
    }
}

# ---- [I] Agentic User delegated consent (only if AGENT_USER_OBJECT_ID is set) ----
if (-not $SkipAgenticUser -and $envv.AGENT_USER_OBJECT_ID) {
    Section "I. Agentic User delegated Graph permissions"
    if ($aiSp -and $graphSp) {
        $grants = @()
        try {
            $grants = (Invoke-RestMethod -Method GET "https://graph.microsoft.com/v1.0/oauth2PermissionGrants?`$filter=clientId eq '$($aiSp.id)' and resourceId eq '$($graphSp.id)'" -Headers $H).value
        } catch {}
        $allPrincipalsGrant = $grants | Where-Object { $_.consentType -eq "AllPrincipals" } | Select-Object -First 1
        if (-not $allPrincipalsGrant) {
            Write-Result "Agent Identity SP -> Graph oauth2PermissionGrant (AllPrincipals)" "FAIL" "Run scripts/02-grant-agentic-user-consent.ps1 — this is the consent that lets the Agentic User call Graph."
        } else {
            $scp = $allPrincipalsGrant.scope
            Write-Result "Agent Identity SP -> Graph oauth2PermissionGrant (AllPrincipals)" "PASS" "scope=$scp"
            if ($scp -notmatch "\bUser\.Read\b") {
                Write-Result "Granted scopes include User.Read" "WARN" "Add User.Read so the Agentic User can call /me"
            } else {
                Write-Result "Granted scopes include User.Read" "PASS"
            }
        }
    }
} else {
    Section "I. Agentic User delegated Graph permissions"
    Write-Result "Agentic User check skipped" "WARN" "Either -SkipAgenticUser was passed, or AGENT_USER_OBJECT_ID is empty (Agentic User not yet provisioned)."
}

# ---- Summary ----
Section "Summary"
if ($script:FAILS -gt 0) {
    Write-Host "  $script:FAILS check(s) FAILED, $script:WARNS warning(s)." -ForegroundColor Red
    Write-Host "  Fix the failures above before running scripts/01-provision-agentic-user.ps1." -ForegroundColor Red
    exit 1
} elseif ($script:WARNS -gt 0) {
    Write-Host "  All checks PASSED with $script:WARNS warning(s) — review above." -ForegroundColor Yellow
    exit 0
} else {
    Write-Host "  All checks PASSED. You're ready to provision the Agentic User." -ForegroundColor Green
    exit 0
}
