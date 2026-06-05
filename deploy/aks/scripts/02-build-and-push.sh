#!/usr/bin/env bash
# Build and push backend, weather-agent, and ui images via `az acr build`.
# No local Docker required.
set -euo pipefail
: "${ACR_NAME:?}"

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
REPO_ROOT="$( cd "$SCRIPT_DIR/../../.." && pwd )"

echo "[1/3] backend  ($REPO_ROOT/backend)"
az acr build --registry "$ACR_NAME" \
  --image auid-aks/backend:1.0.0 \
  --platform linux/amd64 \
  "$REPO_ROOT/backend"

echo "[2/3] weather-agent  ($REPO_ROOT/weather-agent)"
az acr build --registry "$ACR_NAME" \
  --image auid-aks/weather-agent:1.0.0 \
  --platform linux/amd64 \
  "$REPO_ROOT/weather-agent"

echo "[3/3] ui  ($REPO_ROOT/ui)"
az acr build --registry "$ACR_NAME" \
  --image auid-aks/ui:1.0.0 \
  --platform linux/amd64 \
  "$REPO_ROOT/ui"
