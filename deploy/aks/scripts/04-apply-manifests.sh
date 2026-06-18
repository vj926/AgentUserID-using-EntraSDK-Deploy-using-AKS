#!/usr/bin/env bash
# Render manifests via envsubst and apply.
set -euo pipefail
for v in TENANT_ID BLUEPRINT_APP_ID AGENT_IDENTITY_APP_ID AGENT_USER_UPN \
         WEATHER_AGENT_APP_ID WEATHER_AGENT_APP_ID_URI ACR_NAME; do
  if [[ -z "${!v:-}" ]]; then echo "Missing \$$v" >&2; exit 1; fi
done
export AGENT_USER_OBJECT_ID="${AGENT_USER_OBJECT_ID:-}"

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
MANIFESTS="$SCRIPT_DIR/../manifests"
OUT="/tmp/auid-aks-rendered"
mkdir -p "$OUT"

if ! command -v envsubst >/dev/null; then
  echo "envsubst not found. apt: gettext-base | brew: gettext | choco: gettext" >&2
  exit 1
fi

VARS='$TENANT_ID $BLUEPRINT_APP_ID $AGENT_IDENTITY_APP_ID $AGENT_USER_UPN '\
'$AGENT_USER_OBJECT_ID $WEATHER_AGENT_APP_ID $WEATHER_AGENT_APP_ID_URI $ACR_NAME'

for f in "$MANIFESTS"/*.yaml; do
  envsubst "$VARS" < "$f" > "$OUT/$(basename "$f")"
done

kubectl apply -f "$OUT/00-namespace.yaml"
kubectl apply -f "$OUT/10-serviceaccount.yaml"
kubectl apply -f "$OUT/20-weather-agent.yaml"
kubectl apply -f "$OUT/30-ui.yaml"
kubectl apply -f "$OUT/40-backend.yaml"

echo
echo "Waiting for rollouts..."
kubectl -n auid rollout status deploy/weather-agent --timeout=180s
kubectl -n auid rollout status deploy/backend       --timeout=180s
kubectl -n auid rollout status deploy/ui            --timeout=120s

echo
echo "Waiting for LoadBalancer IP..."
for i in $(seq 1 60); do
  IP=$(kubectl -n auid get svc ui -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || true)
  [[ -n "$IP" ]] && break
  sleep 5
done
echo "AUID demo UI: http://${IP:-<pending>}/"
