#!/usr/bin/env bash
# Create RG, ACR, and AKS cluster with OIDC issuer + Workload Identity enabled.
# Appends OIDC_ISSUER back to $VARS_FILE.
set -euo pipefail
: "${SUBSCRIPTION_ID:?}" "${RG:?}" "${LOCATION:?}" "${AKS_NAME:?}" "${ACR_NAME:?}"
: "${NODE_VM_SIZE:?}" "${NODE_COUNT:?}" "${ACR_SKU:?}"
VARS_FILE="${VARS_FILE:-/tmp/deploy-vars.sh}"

az account set --subscription "$SUBSCRIPTION_ID"

if ! az group show -n "$RG" >/dev/null 2>&1; then
  az group create -n "$RG" -l "$LOCATION" -o none
fi

if ! az acr show -n "$ACR_NAME" -g "$RG" >/dev/null 2>&1; then
  az acr create -n "$ACR_NAME" -g "$RG" --sku "$ACR_SKU" -o none
fi

if ! az aks show -n "$AKS_NAME" -g "$RG" >/dev/null 2>&1; then
  az aks create \
    -n "$AKS_NAME" -g "$RG" -l "$LOCATION" \
    --node-count "$NODE_COUNT" --node-vm-size "$NODE_VM_SIZE" \
    --enable-oidc-issuer --enable-workload-identity \
    --attach-acr "$ACR_NAME" \
    --generate-ssh-keys -o none
else
  # Idempotency: ensure OIDC + Workload Identity are on.
  az aks update -n "$AKS_NAME" -g "$RG" \
    --enable-oidc-issuer --enable-workload-identity -o none || true
  az aks update -n "$AKS_NAME" -g "$RG" --attach-acr "$ACR_NAME" -o none || true
fi

az aks get-credentials -n "$AKS_NAME" -g "$RG" --overwrite-existing
OIDC_ISSUER="$(az aks show -n "$AKS_NAME" -g "$RG" --query oidcIssuerProfile.issuerUrl -o tsv)"
export OIDC_ISSUER

# Persist OIDC_ISSUER in the vars file (replace any prior value).
if grep -q '^export OIDC_ISSUER=' "$VARS_FILE"; then
  sed -i.bak "s|^export OIDC_ISSUER=.*|export OIDC_ISSUER=\"$OIDC_ISSUER\"|" "$VARS_FILE"
else
  echo "export OIDC_ISSUER=\"$OIDC_ISSUER\"" >> "$VARS_FILE"
fi
echo "appended: OIDC_ISSUER=$OIDC_ISSUER"
