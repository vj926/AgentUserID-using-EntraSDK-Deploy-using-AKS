# Federate the AKS KSA `auid/backend-sa` directly to the Blueprint app.
# Subject  = system:serviceaccount:auid:backend-sa
# Audience = api://AzureADTokenExchange
param(
  [Parameter(Mandatory=$true)] [string] $TenantId,
  [Parameter(Mandatory=$true)] [string] $BlueprintAppId,
  [Parameter(Mandatory=$true)] [string] $OidcIssuerUrl,
  [string] $Namespace      = "auid",
  [string] $ServiceAccount = "backend-sa",
  [string] $FicName        = "aks-backend-sa"
)

$ErrorActionPreference = "Stop"

Import-Module Microsoft.Graph.Authentication -ErrorAction Stop
Connect-MgGraph -TenantId $TenantId -Scopes "AgentIdentityBlueprint.AddRemoveCreds.All" -NoWelcome | Out-Null

$subject = "system:serviceaccount:$Namespace`:$ServiceAccount"
$body = @{
  name        = $FicName
  issuer      = $OidcIssuerUrl
  subject     = $subject
  audiences   = @("api://AzureADTokenExchange")
  description = "AKS KSA $subject (AUID demo)"
} | ConvertTo-Json -Depth 5

$uri = "https://graph.microsoft.com/beta/applications(appId='$BlueprintAppId')/federatedIdentityCredentials"

try {
  Invoke-MgGraphRequest -Method POST -Uri $uri -Body $body -ContentType "application/json" | Out-Null
  Write-Host "Federated credential '$FicName' created on Blueprint $BlueprintAppId"
  Write-Host "  issuer  : $OidcIssuerUrl"
  Write-Host "  subject : $subject"
}
catch {
  if ($_.Exception.Message -match "already exists|FederatedIdentityCredential with the same") {
    Write-Host "Federated credential '$FicName' already exists - skipping."
  } else { throw }
}
