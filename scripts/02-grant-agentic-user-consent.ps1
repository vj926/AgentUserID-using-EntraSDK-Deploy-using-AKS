# 02-grant-agentic-user-consent.ps1
# Prints the admin-consent URL for the Agent Identity, scoped to the permissions the Agentic User will exercise.
# Lifts from: Connect_3P_agent_to_AgentID_using_HTTPs README step 02.03
#
# Usage:
#   ./02-grant-agentic-user-consent.ps1 `
#       -TenantId           "98430660-2a7e-4e6b-b49c-800a8ba8b657" `
#       -AgentIdentityAppId "2b32c2c2-3a5a-435e-b07e-8ef20564364f"

[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$TenantId,
    [Parameter(Mandatory=$true)][string]$AgentIdentityAppId,
    [string]$Scopes = "User.Read groupmember.read.all Chat.ReadWrite Calendars.ReadWrite Mail.ReadWrite Contacts.Read People.Read",
    [string]$RedirectUri = "https://entra.microsoft.com/TokenAuthorize"
)

$scopesEncoded = [System.Web.HttpUtility]::UrlEncode($Scopes)
$redirectEncoded = [System.Web.HttpUtility]::UrlEncode($RedirectUri)
$url = "https://login.microsoftonline.com/$TenantId/v2.0/adminconsent?client_id=$AgentIdentityAppId&scope=$scopesEncoded&redirect_uri=$redirectEncoded&state=auid-experiment"

Write-Host ""
Write-Host "Open this URL in a browser, sign in as Cloud App Admin (or higher), and click 'Accept':" -ForegroundColor Cyan
Write-Host ""
Write-Host $url -ForegroundColor Yellow
Write-Host ""
Write-Host "After consent succeeds, run ./03-test-token-chain.ps1 to verify the AUID token chain works." -ForegroundColor Cyan
