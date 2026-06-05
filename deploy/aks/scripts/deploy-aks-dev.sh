#!/usr/bin/env bash
# One-shot orchestrator. Source /tmp/deploy-vars.sh first.
#
#   cp deploy/aks/scripts/deploy-vars.sh.template /tmp/deploy-vars.sh
#   # edit /tmp/deploy-vars.sh
#   source /tmp/deploy-vars.sh
#   bash deploy/aks/scripts/deploy-aks-dev.sh
#
# Prerequisites:
#  - Blueprint + Agent Identity already created (use the entra-agent-id-setup
#    workflow). BLUEPRINT_APP_ID and AGENT_IDENTITY_APP_ID set in deploy-vars.
#  - Agentic User provisioned (../../../scripts/01-provision-agentic-user.ps1).
#  - Weather Agent app registered with exposed scope, Agent Identity granted
#    admin consent for it (../../../scripts/04-register-weather-app.ps1 — see
#    that script for the exact Graph calls).
set -euo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
export VARS_FILE="${VARS_FILE:-/tmp/deploy-vars.sh}"

for v in TENANT_ID SUBSCRIPTION_ID RG LOCATION AKS_NAME ACR_NAME \
         NODE_COUNT NODE_VM_SIZE BLUEPRINT_APP_ID AGENT_IDENTITY_APP_ID \
         AGENT_USER_UPN WEATHER_AGENT_APP_ID WEATHER_AGENT_APP_ID_URI; do
  if [[ -z "${!v:-}" ]]; then
    echo "ERROR: \$$v unset. Source $VARS_FILE." >&2; exit 1
  fi
done
export SUBSCRIPTION_TENANT_ID="${SUBSCRIPTION_TENANT_ID:-$TENANT_ID}"

echo "============================================================"
echo " AUID-on-AKS deploy plan"
if [[ "$SUBSCRIPTION_TENANT_ID" != "$TENANT_ID" ]]; then
  echo "   *** CROSS-TENANT DEPLOY ***"
  echo "   Entra tenant : $TENANT_ID"
  echo "   Sub tenant   : $SUBSCRIPTION_TENANT_ID"
fi
echo "   Subscription : $SUBSCRIPTION_ID"
echo "   RG/Location  : $RG / $LOCATION"
echo "   AKS / ACR    : $AKS_NAME / $ACR_NAME"
echo "   Blueprint    : $BLUEPRINT_APP_ID"
echo "   Agent ID     : $AGENT_IDENTITY_APP_ID"
echo "   Agent User   : $AGENT_USER_UPN"
echo "   Weather App  : $WEATHER_AGENT_APP_ID ($WEATHER_AGENT_APP_ID_URI)"
echo "============================================================"

bash "$SCRIPT_DIR/01-create-aks.sh"
# 01 appends OIDC_ISSUER — re-source.
# shellcheck disable=SC1090
source "$VARS_FILE"

bash "$SCRIPT_DIR/02-build-and-push.sh"

pwsh -NoProfile -File "$SCRIPT_DIR/03-federate-blueprint.ps1" \
  -TenantId       "$TENANT_ID" \
  -BlueprintAppId "$BLUEPRINT_APP_ID" \
  -OidcIssuerUrl  "$OIDC_ISSUER" \
  -FicName        "${FIC_NAME:-aks-backend-sa}"

bash "$SCRIPT_DIR/04-apply-manifests.sh"

LB_IP=$(kubectl get svc -n auid ui -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || true)
echo
echo "============================================================"
echo " Done. AUID demo UI: http://${LB_IP:-<pending>}/"
echo
echo " Verify:"
echo "   kubectl get pods -n auid"
echo "   kubectl logs   -n auid -l app=backend -c sidecar --tail=50"
echo "   kubectl logs   -n auid -l app=backend -c backend --tail=50"
echo
echo " Smoke-test the AUID acquisition from inside the cluster:"
echo "   kubectl exec -n auid deploy/backend -c backend -- \\"
echo "     curl -s -X POST http://localhost:8080/api/step/03-auid-token | head -c 600"
echo "============================================================"
